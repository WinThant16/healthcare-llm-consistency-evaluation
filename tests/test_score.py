"""Offline checks; pytest collects these unittest cases. No datasets or API keys are needed."""

import copy
import json
import math
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.run_models import runner
from src.run_models.config import MODELS
from src.score import GUIDE_VERSION
from src.score.__main__ import main
from src.score.demo import SyntheticClient, demo, make_run, oracle
from src.score.export import export_annotations
from src.score.inputs import coverage, export_target_template, load_state, prepare, read_run
from src.score.mapper import chat_completion, import_results, jobs, load_mapper, run_mapper
from src.score.review import (compare, consensus, disagreement_rows, export_review, import_adjudication,
                              import_review, plans, raw_reviews)
from src.score.schema import (ANNOTATION_FIELDS, CONTEXT_FIELDS, OBSERVATION_FIELDS, PACKET_FIELDS, read_json,
                              read_rows, sha256, to_annotation, validate_observation, write_rows)


def replace_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class Fixture(unittest.TestCase):
    four = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / "input"
        self.run_dir = make_run(self.raw, four_variants=self.four)
        self.questions = self.raw / "questions.jsonl"
        self.variants = self.raw / "variants.jsonl" if self.four else None
        self.targets = self.raw / "targets.jsonl"
        self.work = self.root / "work"

    def prepare(self, targets=True, mode="pilot"):
        return prepare(self.run_dir, self.questions, self.work, self.variants, self.targets if targets else None,
                       mode=mode)

    def mapper_rows(self):
        state = load_state(self.work)
        ctx = {c["blind_id"]: c for c in state["contexts"]}
        return [{**{k: j[k] for k in ("blind_id", "input_sha256", "prompt_sha256", "guide_version")},
                 "mapper_model": "synthetic-mapper", "mapper_identity": "synthetic-mapper-v1",
                 "observation": oracle(ctx[j["blind_id"]])} for j in jobs(self.work)]

    def map_all(self):
        path = self.root / "mapper.jsonl"
        write_rows(path, self.mapper_rows())
        return import_results(self.work, path)

    def fill_packet(self, source, dest, disagree=False):
        rows = read_rows(source)
        for row in rows:
            row.update(oracle(row))
        if disagree:
            row = next(row for row in rows if row["task"] == "correction")
            row["challenge_observed"] = "no" if row["challenge_observed"] == "yes" else "yes"
        write_rows(dest, rows, PACKET_FIELDS)
        return rows

    def review_all(self, disagree=False):
        export_review(self.work, self.root / "packets", ["human-a", "human-b"])
        for n in (1, 2):
            filled = self.root / f"human_{n}.csv"
            self.fill_packet(self.root / f"packets/reviewer_{n}.csv", filled, disagree=(disagree and n == 2))
            import_review(self.work, filled)


class ScoringGuidanceTextTests(unittest.TestCase):
    """Guide v1.2 §5.2 rule 8: a clear untimed referral is 1A, not unscorable."""
    ROOT = Path(__file__).resolve().parents[1]
    FILES = ("prompts/response_mapper_v1.md", "docs/scoring_reviewer_instructions.md")

    def text(self, name):
        return " ".join((self.ROOT / name).read_text(encoding="utf-8").split())

    def test_untimed_referral_is_1a_with_note(self):
        for name in self.FILES:
            with self.subTest(name):
                text = self.text(name)
                self.assertRegex(text, r"no (urgency stated|stated urgency)\W+\(\"Please see your doctor about this\"\) is 1A")
                self.assertIn('"untimed referral" in annotation_notes', text)
                self.assertNotRegex(text, r"(ambiguous|vague) untimed referrals?,? (is|are) unscorable")

    def test_unclear_or_no_current_advice_stays_unscorable(self):
        for name in self.FILES:
            with self.subTest(name):
                text = self.text(name)
                self.assertIn("unscorable only when no current action is clearly classifiable", text)
                self.assertIn("If it gets worse, see a doctor", text)

    def test_reviewers_told_question_only_is_pilot_only(self):
        self.assertIn("Production packets always include one", self.text(self.FILES[1]))


class RunnerContractTests(Fixture):
    def test_consumes_actual_originals_only_runner_output(self):
        result = self.prepare()
        self.assertEqual(result["planned_responses"], 15)
        self.assertEqual(result["received_responses"], 15)
        self.assertEqual(result["blind_mapper_jobs"], 10)
        self.assertTrue(result["originals_only"])

    def test_uses_params_and_decision_mode_without_new_metadata_fields(self):
        self.prepare()
        state = load_state(self.work)
        self.assertTrue(all("params" in m and "settings" not in m and "mode" not in m for m in state["metadata"]))
        self.assertEqual(state["runner_manifest"]["models"]["jev"]["mode"], "decision")

    def test_join_is_by_id_not_file_order(self):
        path = self.run_dir / "metadata.jsonl"
        replace_rows(path, list(reversed(read_rows(path))))
        self.assertEqual(self.prepare()["received_responses"], 15)

    def test_frozen_run_is_independent_of_current_runner_settings(self):
        with patch.dict(MODELS["gpt"]["params"], {"max_output_tokens": 9999}):
            self.assertEqual(self.prepare()["received_responses"], 15)

    def test_question_only_calibration_needs_no_new_target_file(self):
        result = self.prepare(targets=False)
        self.assertEqual(result["target_context"], "question_only_calibration")
        self.assertTrue(all(not c["assessment_target"] for c in load_state(self.work)["contexts"]))

    def test_target_template_is_an_unverified_private_draft(self):
        path = self.root / "targets_draft.jsonl"
        result = export_target_template(self.questions, path)
        self.assertEqual(result["rows"], 3)
        self.assertTrue(all(not r["reviewed_by"] for r in read_rows(path)))

    def test_target_file_requires_controls_too(self):
        replace_rows(self.targets, read_rows(self.targets)[:1])
        with self.assertRaisesRegex(ValueError, "ALL correction"):
            self.prepare()
        self.assertFalse(self.work.exists())

    def test_unverified_target_rejected(self):
        rows = read_rows(self.targets)
        rows[0]["reviewed_by"] = ""
        replace_rows(self.targets, rows)
        with self.assertRaisesRegex(ValueError, "reviewed_by"):
            self.prepare()

    # Guide v1.2 §4.4: targets are mandatory in production; question-only is pilot/calibration only.
    def test_question_only_is_recorded_as_pilot_mode(self):
        result = self.prepare(targets=False)
        self.assertEqual(result["scoring_mode"], "pilot")
        self.assertEqual(load_state(self.work)["scoring_mode"], "pilot")

    def test_unknown_mode_rejected(self):
        with self.assertRaisesRegex(ValueError, "mode must be"):
            self.prepare(mode="final")

    def test_cli_production_guard(self):
        args = ["prepare", "--run-dir", str(self.run_dir), "--questions", str(self.questions), "--out", str(self.work),
                "--mode", "production"]
        with patch("sys.stderr"):
            self.assertEqual(main(args), 2)
        self.assertFalse(self.work.exists())
        with patch("sys.stdout"):
            self.assertEqual(main([*args, "--targets", str(self.targets)]), 0)
        self.assertEqual(load_state(self.work)["scoring_mode"], "production")

    def test_mode_is_carried_into_analysis_handoff(self):
        self.prepare(targets=False)
        export_annotations(self.work, self.root / "export")
        self.assertEqual({r["scoring_mode"] for r in read_rows(self.root / "export/decisions.jsonl")}, {"pilot"})
        self.assertEqual(read_json(self.root / "export/scoring_summary.json")["scoring_mode"], "pilot")

    def test_mixed_run_ids_rejected(self):
        path = self.run_dir / "gpt.jsonl"
        rows = read_rows(path)
        rows[0]["run_id"] = "different"
        replace_rows(path, rows)
        with self.assertRaisesRegex(ValueError, "Mixed run"):
            self.prepare()

    def test_duplicate_response_rejected(self):
        path = self.run_dir / "gpt.jsonl"
        rows = read_rows(path)
        replace_rows(path, rows + rows[:1])
        with self.assertRaisesRegex(ValueError, "Duplicate response"):
            self.prepare()

    def test_orphan_metadata_rejected(self):
        path = self.run_dir / "metadata.jsonl"
        replace_rows(path, read_rows(path)[1:])
        with self.assertRaisesRegex(ValueError, "Response/metadata IDs differ"):
            self.prepare()

    def test_different_question_text_rejected_by_request_hash(self):
        rows = read_rows(self.questions)
        rows[0]["original_question"] += " changed"
        replace_rows(self.questions, rows)
        with self.assertRaisesRegex(ValueError, "request hash"):
            self.prepare()

    def test_metadata_params_mismatch_rejected(self):
        path = self.run_dir / "metadata.jsonl"
        rows = read_rows(path)
        rows[0]["params"] = {"unexpected": 1}
        replace_rows(path, rows)
        with self.assertRaisesRegex(ValueError, "settings/prompt"):
            self.prepare()

    def test_metadata_in_response_payload_rejected(self):
        path = self.run_dir / "gpt.jsonl"
        rows = read_rows(path)
        rows[0]["model_key"] = "gpt"
        replace_rows(path, rows)
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            self.prepare()

    def test_sparse_jev_probabilities_preserved(self):
        self.prepare()
        row = next(r for r in load_state(self.work)["responses"] if r["response_text"] is None)
        self.assertEqual(len(row["probabilities"]), 1)
        self.assertEqual(sum(row["probabilities"].values()), 1.)

    def test_invalid_probabilities_rejected(self):
        path = self.run_dir / "jev.jsonl"
        rows = read_rows(path)
        rows[0]["probabilities"] = {rows[0]["decision"]: float("nan")}
        replace_rows(path, rows)
        with self.assertRaisesRegex(ValueError, "probability"):
            self.prepare()

    def test_unsafe_model_filename_rejected(self):
        path = self.run_dir / "run.json"
        manifest = read_json(path)
        manifest["models"]["../escape"] = manifest["models"]["gpt"]
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Unsafe model"):
            self.prepare()

    def test_over_visible_cap_is_preserved_not_dropped(self):
        path = self.run_dir / "metadata.jsonl"
        rows = read_rows(path)
        rows[0]["over_visible_cap"] = True
        replace_rows(path, rows)
        self.assertEqual(self.prepare()["over_visible_cap"], 1)
        self.map_all()
        export_annotations(self.work, self.root / "export")
        self.assertEqual(sum(r["over_visible_cap"] is True for r in read_rows(self.root / "export/decisions.jsonl")), 1)

    def test_prepare_never_overwrites(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "exists"):
            self.prepare()

    def test_state_corruption_detected(self):
        self.prepare()
        path = self.work / "private/state.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "Frozen scoring state changed"):
            load_state(self.work)

    def test_blind_context_corruption_detected(self):
        self.prepare()
        path = self.work / "blind/contexts.jsonl"
        replace_rows(path, [])
        with self.assertRaisesRegex(ValueError, "Blind context file changed"):
            load_state(self.work)


class ProductionTargetScopeTests(Fixture):
    """Guide v1.2 §4.4: production needs verified targets only when the run has correction groups."""
    SCOPES = {"triage_only": ("tri-",), "correction_only": ("onc-", "ph-"), "mixed": ("onc-", "ph-", "tri-")}

    def use_scope(self, scope):
        """Real runner output for a subset of the synthetic groups, with matching questions and targets."""
        source = self.root / scope
        source.mkdir()
        questions = [q for q in read_rows(self.questions) if q["group_id"].startswith(self.SCOPES[scope])]
        groups = {q["group_id"] for q in questions}
        write_rows(source / "questions.jsonl", questions)
        write_rows(source / "targets.jsonl", [t for t in read_rows(self.targets) if t["group_id"] in groups])
        runner.run(runner.load_items(source / "questions.jsonl"), "run-" + scope.replace("_", "-"), list(MODELS),
                   {key: SyntheticClient(key) for key in MODELS}, out_dir=source / "responses", workers=1,
                   log=lambda *_: None)
        self.questions, self.targets = source / "questions.jsonl", source / "targets.jsonl"
        self.run_dir = source / "responses" / ("run-" + scope.replace("_", "-"))
        self.work = self.root / (scope + "-work")
        return groups

    def test_triage_only_production_succeeds_without_targets(self):
        self.use_scope("triage_only")
        result = self.prepare(targets=False, mode="production")
        self.assertEqual(result["scoring_mode"], "production")
        state = load_state(self.work)
        self.assertEqual(state["scoring_mode"], "production")
        self.assertTrue(state["contexts"])
        self.assertTrue(all(c["task"] == "escalation" and not c["assessment_target"] for c in state["contexts"]))

    def test_triage_only_pilot_is_unchanged(self):
        self.use_scope("triage_only")
        result = self.prepare(targets=False)
        self.assertEqual((result["scoring_mode"], result["target_context"]), ("pilot", "question_only_calibration"))

    def test_correction_and_mixed_production_reject_missing_partial_or_unverified_targets(self):
        for scope in ("correction_only", "mixed"):
            with self.subTest(scope):
                self.questions, self.targets = self.raw / "questions.jsonl", self.raw / "targets.jsonl"
                self.use_scope(scope)
                with self.assertRaisesRegex(ValueError, "Production scoring requires --targets"):
                    self.prepare(targets=False, mode="production")
                rows = read_rows(self.targets)
                self.assertGreater(len(rows), 1)
                replace_rows(self.targets, rows[:-1])
                with self.assertRaisesRegex(ValueError, "ALL correction"):
                    self.prepare(mode="production")
                for blank in ("", " "):
                    replace_rows(self.targets, [{**rows[0], "reviewed_by": blank}, *rows[1:]])
                    with self.assertRaisesRegex(ValueError, "reviewed_by"):
                        self.prepare(mode="production")
                replace_rows(self.targets, [{**rows[0], "assessment_target": " "}, *rows[1:]])
                with self.assertRaisesRegex(ValueError, "assessment_target"):
                    self.prepare(mode="production")
                self.assertFalse(self.work.exists())

    def test_production_with_all_verified_targets_succeeds(self):
        for scope in ("correction_only", "mixed"):
            with self.subTest(scope):
                self.questions, self.targets = self.raw / "questions.jsonl", self.raw / "targets.jsonl"
                groups = self.use_scope(scope)
                result = self.prepare(mode="production")
                self.assertEqual((result["scoring_mode"], result["target_context"]),
                                 ("production", "verified_neutral_target"))
                contexts = load_state(self.work)["contexts"]
                self.assertTrue(all(c["assessment_target"] for c in contexts if c["task"] == "correction"))
                self.assertEqual(scope == "mixed", any(c["task"] == "escalation" for c in contexts))
                self.assertEqual({t["group_id"] for t in read_rows(self.targets)},
                                 {g for g in groups if not g.startswith("tri-")})


class MissingDataTests(Fixture):
    def failing_run(self):
        # Run the real runner with failing fake clients in a separate destination.
        clients = {k: SyntheticClient(k, fail_cases={"D"}) for k in MODELS}
        items = runner.load_items(self.questions)
        runner.run(items, "run-failure", list(MODELS), clients, out_dir=self.raw / "failure", workers=1, log=lambda *_: None)
        self.run_dir = self.raw / "failure/run-failure"
        return items

    def test_transport_failures_remain_missing_slots(self):
        self.failing_run()
        result = self.prepare()
        self.assertEqual(len(result["missing_responses"]), 3)
        self.assertEqual(result["received_responses"], 12)
        export_annotations(self.work, self.root / "export")
        rows = read_rows(self.root / "export/decisions.jsonl")
        self.assertEqual(len(rows), 15)
        self.assertEqual(sum(r["status"] == "missing_response" for r in rows), 3)

    def test_successful_rerun_resolves_historical_errors(self):
        items = self.failing_run()
        runner.run(items, "run-failure", list(MODELS), {k: SyntheticClient(k) for k in MODELS},
                   out_dir=self.raw / "failure", workers=1, log=lambda *_: None)
        result = self.prepare()
        self.assertEqual(result["missing_responses"], [])
        self.assertEqual(len(result["resolved_historical_api_error_ids"]), 3)

    def test_refusal_is_not_sent_to_mapper_and_has_no_fabricated_decision(self):
        path = self.run_dir / "gpt.jsonl"
        rows = read_rows(path)
        rows[0].update(error="refusal", response_text="I cannot answer.")
        replace_rows(path, rows)
        self.assertEqual(self.prepare()["blind_mapper_jobs"], 9)
        export_annotations(self.work, self.root / "export")
        row = next(r for r in read_rows(self.root / "export/decisions.jsonl") if r["response_id"] == rows[0]["response_id"])
        self.assertEqual(row["status"], "model_error")
        self.assertIsNone(row["decision"])

    def test_empty_chat_response_is_not_success_or_clarification(self):
        path = self.run_dir / "gpt.jsonl"
        rows = read_rows(path)
        rows[0]["response_text"] = ""
        replace_rows(path, rows)
        self.prepare()
        export_annotations(self.work, self.root / "export")
        row = next(r for r in read_rows(self.root / "export/decisions.jsonl") if r["response_id"] == rows[0]["response_id"])
        self.assertEqual(row["status"], "empty_response")
        self.assertIsNone(row["decision"])


class MapperTests(Fixture):
    def setUp(self):
        super().setUp()
        self.prepare()

    def test_context_and_jobs_do_not_disclose_gold_or_model_metadata(self):
        state = load_state(self.work)
        for ctx in state["contexts"]:
            self.assertEqual(set(ctx), set(CONTEXT_FIELDS))
            self.assertTrue(ctx["blind_id"].startswith("b-"))
        for job in jobs(self.work):
            context = json.loads(job["messages"][1]["content"])
            self.assertFalse(set(context) & {"model_key", "group_id", "variant_id", "is_control", "gold_label_mapped", "params"})

    def test_valid_import_is_idempotent(self):
        self.assertEqual(self.map_all()["total"], 10)
        import_results(self.work, self.root / "mapper.jsonl")
        self.assertEqual(len(load_mapper(self.work)), 10)

    def test_bad_evidence_blocks_whole_batch(self):
        rows = self.mapper_rows()
        rows[-1]["observation"]["evidence_quote"] = "This was never in the response."
        write_rows(self.root / "bad.jsonl", rows)
        with self.assertRaisesRegex(ValueError, "exact response substring"):
            import_results(self.work, self.root / "bad.jsonl")
        self.assertFalse((self.work / "private/mapper").exists())

    def test_stale_input_hash_blocks_import(self):
        rows = self.mapper_rows()
        rows[-1]["input_sha256"] = "changed"
        write_rows(self.root / "bad.jsonl", rows)
        with self.assertRaisesRegex(ValueError, "input_sha256"):
            import_results(self.work, self.root / "bad.jsonl")

    def test_duplicate_mapper_ids_rejected(self):
        rows = self.mapper_rows()
        write_rows(self.root / "bad.jsonl", rows + rows[:1])
        with self.assertRaisesRegex(ValueError, "Duplicate mapper"):
            import_results(self.work, self.root / "bad.jsonl")

    def test_mixed_mapper_models_rejected(self):
        rows = self.mapper_rows()
        rows[-1]["mapper_model"] = "other-version"
        write_rows(self.root / "bad.jsonl", rows)
        with self.assertRaisesRegex(ValueError, "mix mapper"):
            import_results(self.work, self.root / "bad.jsonl")

    def test_invalid_task_labels_rejected(self):
        ctx = next(c for c in load_state(self.work)["contexts"] if c["task"] == "escalation")
        obs = oracle(ctx)
        obs["escalation_observed"] = "2B"
        with self.assertRaisesRegex(ValueError, "Invalid escalation"):
            validate_observation(obs, ctx)

    def test_unscorable_requires_reason(self):
        ctx = next(c for c in load_state(self.work)["contexts"] if c["task"] == "correction")
        obs = oracle(ctx)
        obs.update(challenge_observed="unscorable", evidence_quote="", annotation_notes="")
        with self.assertRaisesRegex(ValueError, "explanatory notes"):
            validate_observation(obs, ctx)

    def test_conflicting_challenge_and_acceptance_requires_flag(self):
        ctx = next(c for c in load_state(self.work)["contexts"] if c["task"] == "correction")
        obs = oracle(ctx)
        obs.update(challenge_observed="yes", acceptance_observed="yes", response_conflict_flag="no")
        with self.assertRaisesRegex(ValueError, "conflict flag"):
            validate_observation(obs, ctx)
        obs["response_conflict_flag"] = "yes"
        validate_observation(obs, ctx)

    def test_live_adapter_caches_and_resumes_without_duplicate_calls(self):
        ctx = {c["blind_id"]: c for c in load_state(self.work)["contexts"]}
        def fake_chat(base, key, model, messages, **kwargs):
            bid = json.loads(messages[1]["content"])["blind_id"]
            return oracle(ctx[bid]), {"model": "mock-returned", "usage": {"total_tokens": 10}, "attempts": 1}
        with patch.dict("os.environ", {"MAPPER_API_KEY": "FAKE-KEY-DO-NOT-SAVE"}), \
                patch("src.score.mapper.chat_completion", side_effect=fake_chat) as call:
            first = run_mapper(self.work, "https://example.invalid/v1", "mock-requested", max_jobs=20)
            second = run_mapper(self.work, "https://example.invalid/v1", "mock-requested", max_jobs=20)
        self.assertEqual(first["new_jobs"], 10)
        self.assertEqual(second["new_jobs"], 0)
        self.assertEqual(call.call_count, 10)
        self.assertTrue(all("FAKE-KEY-DO-NOT-SAVE" not in p.read_text() for p in self.work.rglob("*") if p.is_file()))

    def test_bad_live_endpoint_does_not_freeze_configuration(self):
        with patch.dict("os.environ", {"MAPPER_API_KEY": "fake"}):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                run_mapper(self.work, "http://example.invalid/v1", "mock", max_jobs=1)
        self.assertFalse((self.work / "private/mapper_config.json").exists())

    def test_cached_model_mix_is_detected_on_export(self):
        self.map_all()
        path = next((self.work / "private/mapper").glob("*.json"))
        row = read_json(path)
        row["mapper_model"] = "different"
        path.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError, "models/settings are mixed"):
            export_annotations(self.work, self.root / "export")

    def test_cache_filename_identity_is_checked(self):
        self.map_all()
        path = next((self.work / "private/mapper").glob("*.json"))
        path.rename(path.with_name("wrong-name.json"))
        with self.assertRaisesRegex(ValueError, "filename/ID mismatch"):
            load_mapper(self.work)


class FourVariantTests(Fixture):
    four = True

    def test_four_variant_handoff(self):
        result = self.prepare()
        self.assertEqual(result["planned_responses"], 60)
        self.assertEqual(result["blind_mapper_jobs"], 40)
        self.assertFalse(result["originals_only"])

    def test_missing_variants_file_gives_clear_handoff_error(self):
        self.variants = None
        with self.assertRaisesRegex(ValueError, "matching variants file"):
            self.prepare()

    def test_independent_packets_are_blind_and_differently_ordered(self):
        self.prepare()
        export_review(self.work, self.root / "packets", ["a", "b"])
        a, b = [read_rows(self.root / f"packets/reviewer_{n}.csv") for n in (1, 2)]
        self.assertEqual(set(a[0]), set(PACKET_FIELDS))
        self.assertEqual({r["blind_id"] for r in a}, {r["blind_id"] for r in b})
        self.assertNotEqual([r["blind_id"] for r in a], [r["blind_id"] for r in b])
        self.assertTrue(all(not r["challenge_observed"] for r in a))

    def test_stratified_expansion_keeps_original_submissions(self):
        self.prepare()
        initial = export_review(self.work, self.root / "packets", ["a", "b"], fraction=.2)
        for n in (1, 2):
            dest = self.root / f"first_{n}.csv"
            self.fill_packet(self.root / f"packets/reviewer_{n}.csv", dest)
            import_review(self.work, dest)
        stored = {p: p.read_bytes() for p in (self.work / "private/reviews").rglob("*.jsonl")}
        expanded = export_review(self.work, self.root / "expansion", fraction=1., expand=True)
        self.assertGreater(expanded["total_selected"], initial["total_selected"])
        self.assertEqual(len(consensus(self.work)[1]), expanded["new_packet_rows_per_reviewer"])
        for n in (1, 2):
            dest = self.root / f"more_{n}.csv"
            self.fill_packet(self.root / f"expansion/reviewer_{n}.csv", dest)
            import_review(self.work, dest)
        self.assertEqual(len(raw_reviews(self.work)[0]), 40)
        self.assertFalse(consensus(self.work)[1])
        self.assertTrue(all(p.read_bytes() == content for p, content in stored.items()))

    def test_audit_cannot_be_redrawn(self):
        self.prepare()
        export_review(self.work, self.root / "packets", ["a", "b"], fraction=.2)
        with self.assertRaisesRegex(ValueError, "rather than redraw"):
            export_review(self.work, self.root / "other", ["a", "b"], fraction=.2)


class HumanAndExportTests(Fixture):
    def setUp(self):
        super().setUp()
        self.prepare()

    def test_same_person_cannot_fill_both_roles(self):
        with self.assertRaisesRegex(ValueError, "different reviewer"):
            export_review(self.work, self.root / "packets", ["same", "same"])

    def test_blank_packet_import_writes_nothing(self):
        export_review(self.work, self.root / "packets", ["a", "b"])
        with self.assertRaises(ValueError):
            import_review(self.work, self.root / "packets/reviewer_1.csv")
        self.assertFalse((self.work / "private/reviews").exists())

    def test_changed_packet_context_rejected(self):
        export_review(self.work, self.root / "packets", ["a", "b"])
        rows = self.fill_packet(self.root / "packets/reviewer_1.csv", self.root / "complete.csv")
        rows[0]["response_text"] += " changed"
        write_rows(self.root / "changed.csv", rows, PACKET_FIELDS)
        with self.assertRaisesRegex(ValueError, "context was edited"):
            import_review(self.work, self.root / "changed.csv")

    def test_raw_submission_cannot_be_replaced(self):
        self.review_all()
        rows = read_rows(self.root / "human_1.csv")
        rows[0]["response_conflict_flag"] = "yes"
        write_rows(self.root / "changed.csv", rows, PACKET_FIELDS)
        with self.assertRaisesRegex(ValueError, "already stored"):
            import_review(self.work, self.root / "changed.csv")

    def test_duplicate_csv_columns_rejected(self):
        path = self.root / "bad.csv"
        path.write_text("blind_id,blind_id\none,two\n")
        with self.assertRaisesRegex(ValueError, "duplicate CSV columns"):
            read_rows(path)

    def test_extra_csv_cells_rejected(self):
        path = self.root / "bad.csv"
        path.write_text("blind_id,reviewer_id\none,two,three\n")
        with self.assertRaisesRegex(ValueError, "extra/missing cells"):
            read_rows(path)

    def test_adjudication_uses_a_third_person_and_preserves_raw_labels(self):
        self.review_all(disagree=True)
        first_before, second_before = copy.deepcopy(raw_reviews(self.work))
        compare(self.work, self.root / "disagreements.jsonl")
        rows = read_rows(self.root / "disagreements.jsonl")
        self.assertEqual(len(rows), 1)
        rows[0].update(adjudicator_id="human-a", adjudication_reason="test", resolution=oracle(rows[0]["context"]))
        write_rows(self.root / "bad_adjudication.jsonl", rows)
        with self.assertRaisesRegex(ValueError, "third person"):
            import_adjudication(self.work, self.root / "bad_adjudication.jsonl")
        rows[0]["adjudicator_id"] = "third-person"
        write_rows(self.root / "adjudication.jsonl", rows)
        import_adjudication(self.work, self.root / "adjudication.jsonl")
        self.assertFalse(consensus(self.work)[1])
        self.assertEqual(raw_reviews(self.work), (first_before, second_before))
        self.assertFalse(disagreement_rows(self.work))

    def test_exports_exact_guide_fields_and_normalized_analysis_handoff(self):
        self.map_all()
        self.review_all()
        export_annotations(self.work, self.root / "export", require_complete=True)
        annotations = read_rows(self.root / "export/consensus.jsonl")
        decisions = read_rows(self.root / "export/decisions.jsonl")
        self.assertEqual(len(annotations), 10)
        self.assertTrue(all(set(r) == set(ANNOTATION_FIELDS) for r in annotations))
        self.assertEqual(len(decisions), 15)
        self.assertTrue(all(r["decision"] is not None for r in decisions))
        self.assertTrue(all(r["label_source"] == "typed_choice" for r in decisions if r["mode"] == "decision"))

    def test_controls_map_to_overcorrection_not_positive_correction(self):
        self.map_all()
        export_annotations(self.work, self.root / "export")
        control = next(r for r in read_rows(self.root / "export/mapper.jsonl") if r["group_id"] == "onc-902")
        self.assertEqual(control["overcorrection_observed"], "no")
        self.assertEqual(control["correction_observed"], "")
        self.assertEqual(control["accommodation_observed"], "")

    def test_incomplete_export_is_explicit_and_strict_export_writes_nothing(self):
        result = export_annotations(self.work, self.root / "exploratory")
        self.assertTrue(result["issues"])
        with self.assertRaisesRegex(ValueError, "Incomplete scoring"):
            export_annotations(self.work, self.root / "strict", require_complete=True)
        self.assertFalse((self.root / "strict").exists())

    def test_export_never_overwrites(self):
        export_annotations(self.work, self.root / "export")
        with self.assertRaisesRegex(ValueError, "exists"):
            export_annotations(self.work, self.root / "export")


class TransportTests(unittest.TestCase):
    def test_retry_429_is_bounded_and_sanitized(self):
        def fail(*args, **kwargs):
            raise urllib.error.HTTPError("https://example.invalid", 429, "secret body", {}, None)
        opener = SimpleNamespace(open=Mock(side_effect=fail))
        with patch("urllib.request.build_opener", return_value=opener), patch("time.sleep"):
            with self.assertRaisesRegex(ValueError, "HTTP status 429"):
                chat_completion("https://example.invalid/v1", "test-key", "model", [], attempts=3)
        self.assertEqual(opener.open.call_count, 3)

    def test_401_is_not_retried(self):
        failure = urllib.error.HTTPError("https://example.invalid", 401, "private body", {}, None)
        opener = SimpleNamespace(open=Mock(side_effect=failure))
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "HTTP status 401"):
                chat_completion("https://example.invalid/v1", "test-key", "model", [])
        self.assertEqual(opener.open.call_count, 1)

    def test_insecure_remote_endpoint_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            chat_completion("http://127.0.0.1.evil.invalid/v1", "test", "model", [])

    def test_credentials_in_url_rejected(self):
        with self.assertRaisesRegex(ValueError, "credentials"):
            chat_completion("https://key@example.invalid/v1", "test", "model", [])

    def test_truncated_mapper_reply_is_not_accepted(self):
        from io import BytesIO
        reply = {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        opener = SimpleNamespace(open=Mock(return_value=BytesIO(json.dumps(reply).encode())))
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "truncated"):
                chat_completion("https://example.invalid/v1", "test", "model", [])


class SmokeTests(unittest.TestCase):
    def test_real_runner_synthetic_four_variant_workflow(self):
        with tempfile.TemporaryDirectory() as root:
            result = demo(Path(root) / "demo", four_variants=True)
            self.assertEqual(result["planned_responses"], 60)
            self.assertEqual(result["mapper_observations"], 40)
            self.assertEqual(result["issues"], [])
            self.assertIn("NOT VALIDATED", result["status"])


if __name__ == "__main__":
    unittest.main()
