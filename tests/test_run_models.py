import json

import pytest

from src.data.schema import read_jsonl, write_jsonl
from src.run_models import runner
from src.run_models.config import MODELS, cost_usd
from src.run_models.prompts import load_prompt


class FakeClient:
    def __init__(self, mode, fail_on=()):
        self.mode, self.fail_on, self.calls = mode, set(fail_on), 0

    def call(self, text, task, prompt):
        self.calls += 1
        if text in self.fail_on:
            raise RuntimeError("boom")
        base = {"response_text": None, "decision": None, "probabilities": None, "confidence": None, "error": None,
                "model_version": "fake-1", "stop_reason": "completed", "input_tokens": 10, "output_tokens": 30,
                "reasoning_tokens": None}
        if self.mode == "decision":
            choice = "yes" if task == "correction" else "1B"
            return {**base, "decision": choice, "probabilities": {choice: 1.0}, "confidence": 1.0}
        return {**base, "response_text": f"answer to {text}", "reasoning_tokens": 5}


def fake_clients(**kw):
    return {k: FakeClient(MODELS[k]["mode"], **kw) for k in MODELS}


ITEMS = [{"group_id": "onc-901", "variant_id": "original", "task": "correction", "text": "Q1"},
         {"group_id": "tri-901", "variant_id": "original", "task": "escalation", "text": "Q2"}]


def test_load_items_from_questions_and_variants(tmp_path):
    q = tmp_path / "q.jsonl"
    write_jsonl([{"group_id": "tri-901", "original_question": "chest pain"}], q)
    assert runner.load_items(q) == [{"group_id": "tri-901", "variant_id": "original", "task": "escalation",
                                     "text": "chest pain"}]
    v = tmp_path / "v.jsonl"
    write_jsonl([{"group_id": "ph-001", "variant_id": "p1", "text": "a", "validity": "valid"},
                 {"group_id": "ph-001", "variant_id": "p2", "text": "b", "validity": "invalid"}], v)
    assert [(i["variant_id"], i["task"]) for i in runner.load_items(v)] == [("p1", "correction")]


@pytest.mark.parametrize("rows", [
    [{"group_id": "xx-001", "variant_id": "p1", "text": "a"}],
    [{"group_id": "ph-001", "variant_id": "p9", "text": "a"}],
    [{"group_id": "ph-001", "variant_id": "p1", "text": " "}],
    [{"group_id": "ph-001", "variant_id": "p1", "text": "a"}, {"group_id": "ph-001", "variant_id": "p1", "text": "b"}],
])
def test_load_items_rejects_bad_rows(tmp_path, rows):
    path = tmp_path / "v.jsonl"
    write_jsonl(rows, path)
    with pytest.raises(ValueError):
        runner.load_items(path)


def test_run_writes_blind_rows_and_metadata(tmp_path):
    counts = runner.run(ITEMS, "run-test", list(MODELS), fake_clients(), out_dir=tmp_path, log=lambda *_: None)
    assert counts == {k: (2, 0, 0) for k in MODELS}
    run_dir = tmp_path / "run-test"

    chat = read_jsonl(run_dir / "gpt.jsonl")
    assert set(chat[0]) == {"response_id", "run_id", "group_id", "variant_id", "response_text", "error"}
    jev = {r["group_id"]: r for r in read_jsonl(run_dir / "jev.jsonl")}
    assert jev["onc-901"]["response_text"] is None and jev["onc-901"]["decision"] == "yes"
    assert jev["tri-901"]["decision"] == "1B" and "probabilities" in jev["tri-901"]

    meta = {m["response_id"]: m for m in read_jsonl(run_dir / "metadata.jsonl")}
    ids = [r["response_id"] for k in MODELS for r in read_jsonl(run_dir / f"{k}.jsonl")]
    assert len(set(ids)) == 6 and set(ids) == set(meta)
    assert not any(k in rid for rid in ids for k in MODELS)  # IDs don't reveal the model
    m = meta[chat[0]["response_id"]]
    assert m["model_key"] == "gpt" and m["model_version"] == "fake-1" and m["visible_output_tokens"] == 25
    assert m["over_visible_cap"] is False
    assert json.loads((run_dir / "run.json").read_text())["models"]["jev"]["prompt_sha256"]


def test_rerun_skips_saved_and_fills_failures(tmp_path):
    first = runner.run(ITEMS, "run-test", ["gpt"], fake_clients(fail_on={"Q2"}), out_dir=tmp_path, log=lambda *_: None)
    assert first == {"gpt": (1, 0, 1)}
    assert len(read_jsonl(tmp_path / "run-test" / "errors.jsonl")) == 1
    clients = fake_clients()
    assert runner.run(ITEMS, "run-test", ["gpt"], clients, out_dir=tmp_path) == {"gpt": (1, 1, 0)}
    assert clients["gpt"].calls == 1
    assert len(read_jsonl(tmp_path / "run-test" / "gpt.jsonl")) == 2


def test_models_added_later_are_recorded_in_run_json(tmp_path):
    runner.run(ITEMS, "run-test", ["gpt", "claude"], fake_clients(), out_dir=tmp_path, log=lambda *_: None)
    manifest = tmp_path / "run-test" / "run.json"
    before = json.loads(manifest.read_text())
    runner.run(ITEMS, "run-test", ["jev"], fake_clients(), out_dir=tmp_path, log=lambda *_: None)
    after = json.loads(manifest.read_text())
    assert list(after["models"]) == ["gpt", "claude", "jev"]
    assert {k: after[k] for k in before if k != "models"} == {k: before[k] for k in before if k != "models"}
    assert all(after["models"][k] == before["models"][k] for k in before["models"])


def test_changed_text_needs_new_run_id(tmp_path):
    runner.run(ITEMS, "run-test", ["gpt"], fake_clients(), out_dir=tmp_path)
    changed = [{**ITEMS[0], "text": "Q1 edited"}, ITEMS[1]]
    with pytest.raises(SystemExit):
        runner.run(changed, "run-test", ["gpt"], fake_clients(), out_dir=tmp_path)


def test_prompts_load_and_cover_both_tasks():
    system, sha = load_prompt("claude")
    assert "diagnos" in system and len(sha) == 64
    questions, _ = load_prompt("jev")
    assert set(questions["correction"]["criteria"]) == {"yes", "no"}
    assert set(questions["escalation"]["criteria"]) == {"0A", "0B", "1A", "1B"}


def test_cost_only_when_price_known():
    assert cost_usd("claude", 1_000_000, 1_000_000) == 30.0
    assert cost_usd("gpt", 10, 10) is None
