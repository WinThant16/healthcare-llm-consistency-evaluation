import csv
import json

import pytest

from src.data.schema import make_row, read_jsonl
from src.paraphrase import checks, pipeline, prompts, qc, review
from src.paraphrase.llm import Cache, LLMClient, parse_json_object
from src.paraphrase.variants import paths, text_sha, validate_variants, write_final


class FakeClient:
    """Stands in for LLMClient: returns canned JSON and records the prompts it was sent."""

    model = "fake-model-1"

    def __init__(self, reply=None, judge=False):
        self.calls = []
        self.reply = reply
        self.judge = judge

    @property
    def settings(self):
        return {"provider": "fake", "model": self.model, "temperature": 0.7, "seed": 1, "max_tokens": 1024}

    def complete(self, system, user, tag=""):
        self.calls.append({"system": system, "user": user, "tag": tag})
        if self.reply is not None:
            text = self.reply
        elif self.judge:
            text = json.dumps({"verdict": "same", "issues": [], "explanation": "fine"})
        elif "rejected an earlier" in user:
            vid = user.split('rejected an earlier "')[1].split('"')[0]
            text = json.dumps({vid: f"fixed rewrite {len(self.calls)}"})
        else:
            text = "```json\n" + json.dumps({"p1": "Neutral version of the question?",
                                             "p2": "casual version of the question?",
                                             "p3": "Sorry, I'm worried, but is the question this?"}) + "\n```"
        return {"text": text, "model_version": "fake-model-1-2026", "tokens_in": 100, "tokens_out": 50,
                "latency_s": 0.1, "stop_reason": "end", "cached": False}


def q(group_id, domain, task, control=False, gold=None, premise=None, text="Original question?"):
    gold = gold or ("no_correction" if control else "correction_required")
    return make_row(candidate_id=f"{group_id}-c", group_id=group_id, domain=domain, task=task, is_control=control,
                    source_dataset="care_bench" if task == "escalation" else "cancer_myth", source_id=group_id,
                    original_question=text, source_label_raw=gold, gold_label_mapped=gold,
                    target_false_premise=premise, source_evidence="ev", eligibility_status="include",
                    review_status="pending", source_meta=None)


@pytest.fixture
def questions():
    return [
        q("onc-901", "oncology", "correction", premise="Every cancer is inherited."),
        q("onc-902", "oncology", "correction", control=True),
        q("tri-901", "acute_triage", "escalation", gold="1B", text="Chest pain for 2 hours, should I go in?"),
    ]


@pytest.fixture
def files(tmp_path):
    return paths("pilot", work_dir=tmp_path / "work", decision_dir=tmp_path / "decisions")


# --- prompts -----------------------------------------------------------------------------------------

def test_prompt_files_parse_and_render(questions):
    gen = prompts.load_sections(prompts.GENERATE_VERSION)
    judge = prompts.load_sections(prompts.JUDGE_VERSION)
    assert {"system", "user", "generate", "redo", "task_correction_positive", "task_correction_control",
            "task_escalation"} <= set(gen)
    for row in questions:
        system, user = prompts.generation_messages(row, gen)
        assert row["original_question"] in user and "$" not in user and "$" not in system
        _, juser = prompts.judge_messages(row, "a rewrite", judge)
        assert "a rewrite" in juser and "$" not in juser


def test_premise_shown_only_to_positives_and_gold_never_shown(questions):
    gen = prompts.load_sections(prompts.GENERATE_VERSION)
    _, pos = prompts.generation_messages(questions[0], gen)
    _, ctl = prompts.generation_messages(questions[1], gen)
    _, tri = prompts.generation_messages(questions[2], gen)
    assert "Every cancer is inherited." in pos
    assert "assumes the following belief" not in ctl
    assert "1B" not in tri and "urgent care" not in tri.lower()


def test_positive_without_premise_fails_loudly(questions):
    row = dict(questions[0], target_false_premise=None)
    with pytest.raises(ValueError):
        prompts.generation_messages(row, prompts.load_sections(prompts.GENERATE_VERSION))


def test_dollar_signs_in_question_are_safe(questions):
    row = dict(questions[1], original_question="Is a $50 supplement worth it? $x")
    _, user = prompts.generation_messages(row, prompts.load_sections(prompts.GENERATE_VERSION))
    assert "$50 supplement" in user


# --- parsing and checks ------------------------------------------------------------------------------

def test_parse_json_object_tolerates_fences_and_chatter():
    assert parse_json_object('```json\n{"p1": "a"}\n```') == {"p1": "a"}
    assert parse_json_object('Here you go: {"p1": "a"} thanks') == {"p1": "a"}
    with pytest.raises(ValueError):
        parse_json_object("no json here")


@pytest.mark.parametrize("orig,para,kind,flag", [
    ("Fever for 3 days in my son", "My son has had a fever for three days", "escalation", None),
    ("Fever for 3 days in my son", "My son has had a fever for five days", "escalation", "number_changed"),
    ("I have a headache", "I have a severe headache", "escalation", "urgency_term_added"),
    ("I have a headache", "I just have a mild headache", "escalation", "minimizing_term_added"),
    ("My daughter has a rash", "My son has a rash", "escalation", "person_terms_changed"),
    ("It doesn't hurt", "It hurts", "escalation", "negation_count_changed"),
    ("Since all cancer is inherited, which gene?", "I've heard all cancer might be inherited, which gene?",
     "correction_positive", "hedge_added"),
    ("Is this normal?", "Is this normal?", "correction_control", "identical_to_original"),
])
def test_rule_checks(orig, para, kind, flag):
    flags = checks.check_pair(orig, para, kind)
    if flag is None:
        assert "number_changed" not in flags and "urgency_term_added" not in flags
    else:
        assert flag in flags


def test_word_boundaries():
    assert checks.terms_in("the woman said", ["man"]) == set()
    assert checks.terms_in("went to the ER", ["er"]) == {"er"}
    assert checks.terms_in("I'm here", ["er"]) == set()


# --- pipeline ----------------------------------------------------------------------------------------

def test_generate_writes_schema_rows_and_is_idempotent(questions, files):
    client = FakeClient()
    res = pipeline.generate(questions, files, client, log=lambda *_: None)
    assert res == {"generated": 3, "failed": 0}
    rows = read_jsonl(files["variants"])
    assert [r["variant_id"] for r in rows[:4]] == ["original", "p1", "p2", "p3"]
    assert len(rows) == 12 and not validate_variants(rows)
    assert rows[0]["generator"] == "source" and rows[1]["generator"] == "fake-model-1-2026"
    assert all(r["validity"] == "needs_review" for r in rows if r["variant_id"] != "original")
    # Re-running makes no new calls.
    pipeline.generate(questions, files, client, log=lambda *_: None)
    assert len(client.calls) == 3
    log = read_jsonl(files["log"])
    assert len(log) == 3 and log[0]["prompt_version"] == prompts.GENERATE_VERSION


def test_generate_records_failure_on_bad_reply(questions, files):
    res = pipeline.generate(questions[:1], files, FakeClient(reply='{"p1": "only one"}'), log=lambda *_: None)
    assert res == {"generated": 0, "failed": 1}
    assert read_jsonl(files["log"])[0]["error"]


def test_limit(questions, files):
    assert pipeline.generate(questions, files, FakeClient(), limit=1, log=lambda *_: None)["generated"] == 1


def test_checks_with_judge_and_rechecks_only_new_text(questions, files):
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    judge = FakeClient(judge=True)
    pipeline.run_checks(questions, files, judge, log=lambda *_: None)
    assert len(judge.calls) == 9
    assert "Neutral version" in judge.calls[0]["user"] and "neutral" not in judge.calls[0]["user"].split("Rewrite")[0]
    pipeline.run_checks(questions, files, judge, log=lambda *_: None)
    assert len(judge.calls) == 9
    rows = read_jsonl(files["checks"])
    assert all(r["judge_verdict"] == "same" for r in rows)


# --- review round trip -------------------------------------------------------------------------------

def _fill(path, fn):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        fn(r)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _review_all(r, bad=("tri-901", "p3")):
    r.update(reviewer_1="sahil", reviewer_2="win", validity_1="valid", validity_2="valid")
    if (r["group_id"], r["variant_id"]) == bad:
        r.update(validity_1="invalid", reason_1="adds urgency", validity_2="invalid", reason_2="adds urgency")


def test_full_review_cycle(questions, files):
    gen = FakeClient()
    pipeline.generate(questions, files, gen, log=lambda *_: None)
    pipeline.run_checks(questions, files, log=lambda *_: None)
    assert review.export_sheet(questions, files) == 9
    with pytest.raises(FileExistsError):
        review.export_sheet(questions, files)
    _fill(files["review"], _review_all)
    review.import_sheets([files["review"]], questions, files, log=lambda *_: None)

    rows = {(r["group_id"], r["variant_id"]): r for r in read_jsonl(files["variants"])}
    assert rows[("onc-901", "p1")]["validity"] == "valid" and rows[("onc-901", "p1")]["verified_by"] == "sahil;win"
    assert rows[("tri-901", "p3")]["validity"] == "invalid"
    # Committed decisions contain no question or paraphrase text.
    decisions_text = files["decisions"].read_text(encoding="utf-8")
    assert files["review"].read_bytes().startswith(b"\xef\xbb\xbf")  # review sheet keeps its BOM for Excel
    assert "Original question" not in decisions_text and "version of the question" not in decisions_text

    # Regenerate the invalid one; it goes back to needs_review with new text.
    pipeline.redo(questions, files, gen, log=lambda *_: None)
    rows = {(r["group_id"], r["variant_id"]): r for r in read_jsonl(files["variants"])}
    assert rows[("tri-901", "p3")]["validity"] == "needs_review"
    assert rows[("tri-901", "p3")]["text"].startswith("fixed rewrite")
    assert "adds urgency" in gen.calls[-1]["user"]
    assert read_jsonl(files["log"])[-1]["attempt"] == 2

    # Only the regenerated paraphrase needs review now.
    pipeline.run_checks(questions, files, log=lambda *_: None)
    assert review.export_sheet(questions, files, force=True) == 1
    _fill(files["review"], lambda r: r.update(reviewer_1="sahil", reviewer_2="win", validity_1="valid",
                                              validity_2="valid"))
    review.import_sheets([files["review"]], questions, files, log=lambda *_: None)
    assert not validate_variants(read_jsonl(files["variants"]), [x["group_id"] for x in questions],
                                 require_complete=True)

    report = qc.build_report(questions, files, "pilot")
    assert "**Pipeline checks:** pass" in report and "Ready for the model run" not in report
    assert "Original question" not in report
    assert "rejected by a reviewer and regenerated" in report and "1 (11%)" in report
    assert "Regenerations: 1 (1 after a reviewer rejected a paraphrase, 0 before review)" in report


def test_import_rejects_bad_rows_atomically(questions, files):
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    review.export_sheet(questions, files)

    def bad(r):
        r.update(reviewer_1="sahil", validity_1="valid", reviewer_2="win", validity_2="valid")
        if r["variant_id"] == "p2":
            r.update(validity_2="invalid")  # no reason
    _fill(files["review"], bad)
    with pytest.raises(ValueError):
        review.import_sheets([files["review"]], questions, files, log=lambda *_: None)
    assert not files["decisions"].exists()


def test_stale_sheet_rejected(questions, files):
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    review.export_sheet(questions, files)
    _fill(files["review"], lambda r: r.update(reviewer_1="a", validity_1="valid", reviewer_2="b", validity_2="valid",
                                              text_sha="000000000000"))
    with pytest.raises(ValueError):
        review.import_sheets([files["review"]], questions, files, log=lambda *_: None)


@pytest.mark.parametrize("fill,expected", [
    (dict(reviewer_1="a", validity_1="valid"), "needs_review"),  # second review missing
    (dict(reviewer_1="a", validity_1="valid", reviewer_2="b", validity_2="invalid", reason_2="x"), "needs_review"),
    (dict(reviewer_1="a", validity_1="valid", reviewer_2="b", validity_2="invalid", reason_2="x",
          final_validity="valid"), "valid"),
])
def test_effective_validity(fill, expected):
    row = {k: "" for k in review.REVIEWER_FIELDS}
    row.update(fill)
    assert review._effective(row)[0] == expected


def test_same_reviewer_twice_rejected():
    row = {k: "" for k in review.REVIEWER_FIELDS}
    row.update(reviewer_1="Win", validity_1="valid", reviewer_2="win", validity_2="valid")
    with pytest.raises(ValueError):
        review._effective(row)


def test_apply_decisions_after_rebuild(questions, files):
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    review.export_sheet(questions, files)
    _fill(files["review"], lambda r: r.update(reviewer_1="a", validity_1="valid", reviewer_2="b", validity_2="valid"))
    review.import_sheets([files["review"]], questions, files, log=lambda *_: None)
    files["variants"].unlink()
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    assert review.apply_decisions(questions, files) == 9


# --- client cache ------------------------------------------------------------------------------------

def test_client_cache_avoids_second_call(tmp_path, monkeypatch):
    client = LLMClient("anthropic", "m", temperature=0.7, cache=Cache(tmp_path / "c.jsonl"))
    calls = []
    monkeypatch.setattr(client, "_call", lambda s, u: calls.append(1) or {"text": "{}", "model_version": "m"})
    assert client.complete("s", "u")["cached"] is False
    again = LLMClient("anthropic", "m", temperature=0.7, cache=Cache(tmp_path / "c.jsonl"))
    monkeypatch.setattr(again, "_call", lambda s, u: calls.append(1))
    assert again.complete("s", "u")["cached"] is True
    assert len(calls) == 1
    assert client.cache_key("s", "u") != client.cache_key("s", "u", tag="redo2")


def test_request_shapes(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1/")
    url, headers, body = LLMClient("anthropic", "m", temperature=0.5, cache=Cache("/nonexistent/x"))._request("s", "u")
    assert url.endswith("/v1/messages") and body["system"] == "s" and headers["x-api-key"] == "k"
    url, headers, body = LLMClient("openai", "m", temperature=None, seed=3, max_tokens_field="max_completion_tokens",
                                   cache=Cache("/nonexistent/x"))._request("s", "u")
    assert url == "https://example.com/v1/chat/completions" and "temperature" not in body
    assert body["seed"] == 3 and body["max_completion_tokens"] == 1024


# --- PR review fixes: committed-file format, reviewer IDs, final export, QC wording ---------------------

def test_committed_decisions_are_plain_utf8_with_lf(questions, files):
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    review.export_sheet(questions, files)
    _fill(files["review"], lambda r: r.update(reviewer_1="sahil", validity_1="valid", reviewer_2="win",
                                              validity_2="valid"))
    review.import_sheets([files["review"]], questions, files, log=lambda *_: None)
    raw = files["decisions"].read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    # Importing again leaves the file byte-for-byte unchanged (no churn in git).
    review.import_sheets([files["review"]], questions, files, log=lambda *_: None)
    assert files["decisions"].read_bytes() == raw


def test_reviewer_ids_are_stored_lowercase(questions, files):
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    review.export_sheet(questions, files)
    _fill(files["review"], lambda r: r.update(reviewer_1="Sahil", validity_1="valid", reviewer_2="WIN",
                                              validity_2="valid"))
    review.import_sheets([files["review"]], questions, files, log=lambda *_: None)
    assert {r["verified_by"] for r in read_jsonl(files["variants"]) if r["variant_id"] != "original"} == {"sahil;win"}
    text = files["decisions"].read_text(encoding="utf-8")
    assert "Sahil" not in text and "WIN" not in text and ",sahil," in text and ",win," in text


def test_import_rejects_same_reviewer_in_different_case(questions, files):
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    review.export_sheet(questions, files)
    _fill(files["review"], lambda r: r.update(reviewer_1="Win", validity_1="valid", reviewer_2="win",
                                              validity_2="valid"))
    with pytest.raises(ValueError):
        review.import_sheets([files["review"]], questions, files, log=lambda *_: None)


def test_validate_counts_win_and_Win_as_one_person():
    row = {"group_id": "g", "variant_id": "p1", "text": "t", "generator": "m", "validity": "valid",
           "validity_reason": None, "verified_by": "win;Win"}
    assert any("two distinct reviewers" in e for e in validate_variants([row]))
    assert not validate_variants([dict(row, verified_by="sahil;Win")])


def test_write_final_only_when_everything_is_valid(questions, files, tmp_path):
    out = tmp_path / "final" / "variants.jsonl"
    ids = [x["group_id"] for x in questions]
    pipeline.generate(questions, files, FakeClient(), log=lambda *_: None)
    with pytest.raises(ValueError, match="not writing"):
        write_final(files, ids, out=out)
    assert not out.exists()
    review.export_sheet(questions, files)
    _fill(files["review"], lambda r: r.update(reviewer_1="sahil", validity_1="valid", reviewer_2="win",
                                              validity_2="valid"))
    review.import_sheets([files["review"]], questions, files, log=lambda *_: None)
    assert write_final(files, ids, out=out) == 12
    rows = read_jsonl(out)
    assert [r["variant_id"] for r in rows[:4]] == ["original", "p1", "p2", "p3"]
    assert not validate_variants(rows, ids, require_complete=True)


def test_qc_wording_for_regeneration_before_review_and_hand_written_sets(questions, files):
    client = FakeClient()
    pipeline.generate(questions, files, client, log=lambda *_: None)
    pipeline._append(files["log"], pipeline._log_row(questions[2], ["p3"], 2, client.complete("s", "u"), client,
                                                      reason="pre-review revision"))
    report = qc.build_report(questions, files, "pilot")
    assert "Regenerations: 1 (0 after a reviewer rejected a paraphrase, 1 before review)" in report
    assert "**Pipeline checks:** fail" in report  # nothing reviewed yet
    assert "written by hand in a chat session" in report  # FakeClient's provider is not a real API provider
    assert "Judge model: none (rule flags only)." in report


def test_qc_has_no_hand_written_note_for_api_generators(questions, files):
    class ApiClient(FakeClient):
        @property
        def settings(self):
            return {**super().settings, "provider": "anthropic"}
    pipeline.generate(questions, files, ApiClient(), log=lambda *_: None)
    assert "written by hand" not in qc.build_report(questions, files, "pilot")
