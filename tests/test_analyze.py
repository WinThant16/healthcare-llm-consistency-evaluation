"""Offline analysis tests: no src.score import, source datasets, API keys or new packages."""

import contextlib
import copy
import io
import json
import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.analyze.__main__ import main
from src.analyze.agreement import analyze_agreement, categorical_agreement
from src.analyze.bootstrap import group_bootstrap, percentile, stable_seed
from src.analyze.demo import MODELS, make_fixture
from src.analyze.gir import make_groups, summarize
from src.analyze.inputs import DEFAULT_POLICY, load_data, policy_from, read_json, read_rows
from src.analyze.report import analyze


def replace_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class BootstrapTests(unittest.TestCase):
    def test_interpolated_percentile(self):
        self.assertEqual(percentile([1, 2, 3, 4], .25), 1.75)
        self.assertIsNone(percentile([], .5))

    def test_invalid_percentile_rejected(self):
        with self.assertRaises(ValueError):
            percentile([1], 2)

    def test_all_consistent_has_zero_interval(self):
        result = group_bootstrap([0, 0, 0], lambda gs: 100 * sum(gs) / len(gs), samples=100)
        self.assertEqual(result["estimate"], 0)
        self.assertEqual(result["ci95"], [0, 0])

    def test_all_inconsistent_has_100_interval(self):
        result = group_bootstrap([1, 1, 1], lambda gs: 100 * sum(gs) / len(gs), samples=100)
        self.assertEqual(result["estimate"], 100)
        self.assertEqual(result["ci95"], [100, 100])

    def test_empty_statistic_is_undefined(self):
        result = group_bootstrap([], lambda gs: 1 / 0, samples=100)
        self.assertIsNone(result["estimate"])
        self.assertEqual(result["ci95"], [None, None])

    def test_question_resampling_preserves_all_variants(self):
        groups = [(0, 0, 0, 0), (1, 1, 1, 1)]
        observed = []
        def statistic(sample):
            self.assertTrue(all(len(set(group)) == 1 for group in sample))
            value = sum(sum(g) for g in sample) / (4 * len(sample))
            observed.append(value)
            return value
        result = group_bootstrap(groups, statistic, samples=1000)
        self.assertEqual(result["estimate"], .5)
        self.assertTrue(set(observed) <= {0, .5, 1})
        self.assertEqual(result["ci95"], [0, 1])

    def test_reproducible_without_changing_global_rng(self):
        before = random.getstate()
        a = group_bootstrap([0, 1, 1], lambda gs: sum(gs) / len(gs), samples=100, seed=2)
        b = group_bootstrap([0, 1, 1], lambda gs: sum(gs) / len(gs), samples=100, seed=2)
        self.assertEqual(a, b)
        self.assertEqual(before, random.getstate())

    def test_seed_is_stable_and_stratum_specific(self):
        self.assertEqual(stable_seed(1, "gpt"), stable_seed(1, "gpt"))
        self.assertNotEqual(stable_seed(1, "gpt"), stable_seed(1, "claude"))

    def test_invalid_replicates_and_nonfinite_statistic_rejected(self):
        with self.assertRaises(ValueError):
            group_bootstrap([1], sum, samples=10)
        with self.assertRaises(ValueError):
            group_bootstrap([1], lambda gs: float("nan"), samples=100)


class AgreementMathTests(unittest.TestCase):
    def test_hand_calculated_binary_kappa_and_recall(self):
        result = categorical_agreement([("yes", "yes")] * 4 + [("no", "no")] * 4 + [("yes", "no"), ("no", "yes")])
        self.assertAlmostEqual(result["agreement"], .8)
        self.assertAlmostEqual(result["cohen_kappa"], .6)
        self.assertEqual(result["class_recall"], {"no": .8, "yes": .8})

    def test_empty_and_single_class_kappa_are_not_perfect(self):
        self.assertIsNone(categorical_agreement([])["cohen_kappa"])
        self.assertIsNone(categorical_agreement([("yes", "yes")] * 5)["cohen_kappa"])

    def test_absent_reference_class_is_not_100_recall(self):
        result = categorical_agreement([("yes", "yes")], ("yes", "no"))
        self.assertIsNone(result["class_recall"]["no"])

    def test_missing_and_unscorable_predictions_are_false_negatives(self):
        result = categorical_agreement([("yes", "yes"), ("yes", "missing_mapper"), ("yes", "unscorable")])
        self.assertEqual(result["class_recall"]["yes"], 1 / 3)

    def test_four_classes_are_nominal_not_ordinal(self):
        pairs = [("0A", "0B"), ("0B", "0B"), ("1A", "1B"), ("1B", "1B")]
        mapping = {"0A": "w", "0B": "a", "1A": "z", "1B": "p"}
        self.assertEqual(categorical_agreement(pairs)["cohen_kappa"],
                         categorical_agreement([(mapping[a], mapping[b]) for a, b in pairs])["cohen_kappa"])


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.annotations, self.questions = make_fixture(self.root / "inputs")
        self.policy = {**DEFAULT_POLICY, "bootstrap_samples": 100}

    def data(self):
        return load_data(self.annotations, self.questions, list(MODELS))

    def decide(self, change):
        path = self.annotations / "decisions.jsonl"
        rows = read_rows(path)
        change(rows)
        replace_rows(path, rows)

    def report(self, **kwargs):
        return analyze(self.annotations, self.questions, self.root / "analysis", models=list(MODELS), bootstrap_samples=100, **kwargs)


class InputTests(Fixture):
    def test_explicit_expected_models_are_required(self):
        with self.assertRaisesRegex(ValueError, "Declare --models"):
            load_data(self.annotations, self.questions)

    def test_actual_scoring_contract_loads_without_scorer_package(self):
        data = self.data()
        self.assertEqual(len(data["by_id"]), 107)
        self.assertEqual(len(data["questions"]), 9)

    def test_duplicate_response_id_rejected(self):
        self.decide(lambda rows: rows.append(rows[0]))
        with self.assertRaisesRegex(ValueError, "Duplicate response ID"):
            self.data()

    def test_duplicate_model_group_variant_rejected(self):
        def change(rows):
            extra = dict(rows[0], response_id="other-id")
            rows.append(extra)
        self.decide(change)
        with self.assertRaisesRegex(ValueError, "Duplicate model/group/variant"):
            self.data()

    def test_mixed_runs_rejected(self):
        self.decide(lambda rows: rows[0].update(run_id="another"))
        with self.assertRaisesRegex(ValueError, "mix run IDs"):
            self.data()

    def test_mixed_guide_versions_rejected(self):
        self.decide(lambda rows: rows[0].update(guide_version="another"))
        with self.assertRaisesRegex(ValueError, "annotation-guide"):
            self.data()

    def test_mixed_model_modes_rejected(self):
        self.decide(lambda rows: rows[1].update(mode="decision"))
        with self.assertRaisesRegex(ValueError, "modes changed"):
            self.data()

    def test_status_cannot_hide_a_decision(self):
        self.decide(lambda rows: rows[0].update(status="unscorable"))
        with self.assertRaisesRegex(ValueError, "status/decision"):
            self.data()

    def test_source_and_decision_labels_cannot_disagree(self):
        self.decide(lambda rows: rows[0].update(is_control=True))
        with self.assertRaisesRegex(ValueError, "source/decision mismatch"):
            self.data()

    def test_unknown_care_label_rejected(self):
        def change(rows):
            next(r for r in rows if r["task"] == "escalation")["decision"] = "2B"
        self.decide(change)
        with self.assertRaisesRegex(ValueError, "status/decision"):
            self.data()

    def test_explicit_null_unscorable_is_allowed(self):
        self.decide(lambda rows: rows[0].update(status="unscorable", decision=None))
        self.data()

    def test_excluded_source_question_rejected_not_silently_dropped(self):
        qs = read_rows(self.questions)
        qs[0]["eligibility_status"] = "exclude"
        replace_rows(self.questions, qs)
        with self.assertRaisesRegex(ValueError, "not included"):
            self.data()

    def test_unknown_policy_field_rejected(self):
        path = self.root / "policy.json"
        path.write_text(json.dumps({"silently_drop_controls": True}))
        with self.assertRaisesRegex(ValueError, "Unknown analysis policy"):
            policy_from(path)

    def test_invalid_policy_replicates_rejected(self):
        with self.assertRaisesRegex(ValueError, "100"):
            policy_from(overrides={"bootstrap_samples": 10})


class GIRTests(Fixture):
    def groups(self, policy=None):
        return make_groups(self.data(), policy or self.policy)

    def test_one_inconsistent_among_two_positive_groups_is_50_percent(self):
        summary, _ = summarize(self.groups(), self.policy)
        row = next(r for r in summary if r["model_key"] == "gpt" and r["domain"] == "oncology" and r["behavior"] == "correction_positive")
        self.assertEqual(row["gir_percent"]["estimate"], 50)
        self.assertEqual(row["complete_groups"], 2)

    def test_consistently_wrong_is_consistent_and_inaccurate(self):
        group = next(g for g in self.groups() if g["model_key"] == "claude" and g["group_id"] == "ph-001")
        self.assertFalse(group["observed_disagreement"])
        self.assertEqual(group["n_correct"], 0)
        self.assertTrue(group["complete"])

    def test_missing_variant_and_error_are_incomplete_not_agreement(self):
        groups = self.groups()
        for model, gid, reason in (("claude", "tri-002", "missing_variant_record"), ("gpt", "tri-004", "model_error")):
            row = next(g for g in groups if g["model_key"] == model and g["group_id"] == gid)
            self.assertFalse(row["complete"])
            self.assertEqual(row["n_scored"], 3)
            self.assertIn(reason, row["exclusion_reasons"])

    def test_missingness_bounds_count_incomplete_groups(self):
        summary, _ = summarize(self.groups(), self.policy)
        row = next(r for r in summary if r["model_key"] == "claude" and r["domain"] == "acute_triage")
        self.assertEqual(row["gir_missingness_bounds_percent"], [0, 25])

    def test_incomplete_observed_disagreement_contributes_to_lower_bound(self):
        data = self.data()
        del data["decisions"]["gpt", "onc-001", "p2"]
        summary, _ = summarize(make_groups(data, self.policy), self.policy)
        row = next(r for r in summary if r["model_key"] == "gpt" and r["domain"] == "oncology" and r["behavior"] == "correction_positive")
        self.assertEqual(row["gir_missingness_bounds_percent"], [50, 50])
        self.assertEqual(row["gir_percent"]["estimate"], 0)  # remaining complete group only

    def test_accuracy_denominators_are_reported_separately(self):
        summary, _ = summarize(self.groups(), self.policy)
        row = next(r for r in summary if r["model_key"] == "claude" and r["domain"] == "acute_triage")
        self.assertEqual(row["scorable_accuracy_percent"]["estimate"], 100)
        self.assertEqual(row["all_planned_accuracy_percent"]["estimate"], 93.75)

    def test_default_controls_separate_and_explicit_pooling(self):
        separate, _ = summarize(self.groups(), self.policy)
        self.assertIn("overcorrection_control", {r["behavior"] for r in separate})
        policy = {**self.policy, "control_gir": "pooled"}
        pooled, _ = summarize(self.groups(policy), policy)
        row = next(r for r in pooled if r["model_key"] == "gpt" and r["domain"] == "oncology")
        self.assertEqual(row["behavior"], "challenge_pooled")
        self.assertAlmostEqual(row["gir_percent"]["estimate"], 100 / 3)

    def test_originals_only_has_no_estimable_gir(self):
        annotations, questions = make_fixture(self.root / "originals", originals_only=True)
        data = load_data(annotations, questions, list(MODELS))
        summary, _ = summarize(make_groups(data, self.policy), self.policy)
        self.assertTrue(all(r["complete_groups"] == 0 and r["gir_percent"]["estimate"] is None for r in summary))

    def test_entire_missing_model_is_not_dropped(self):
        data = self.data()
        data["decisions"] = {key: row for key, row in data["decisions"].items() if row["model_key"] != "jev"}
        summary, _ = summarize(make_groups(data, self.policy), self.policy)
        absent = [r for r in summary if r["model_key"] == "jev"]
        self.assertTrue(absent)
        self.assertTrue(all(r["gir_percent"]["estimate"] is None for r in absent))

    def test_models_are_never_pooled_into_one_question_group(self):
        self.assertEqual(len(self.groups()), 27)

    def test_pending_audits_excluded_by_default(self):
        self.decide(lambda rows: rows[1].update(audit_pending=True))
        group = next(g for g in self.groups() if g["model_key"] == "gpt" and g["group_id"] == "onc-001")
        self.assertIn("pending_human_adjudication", group["exclusion_reasons"])
        self.assertEqual(group["n_scored"], 3)

    def test_pending_mapper_values_require_explicit_exploratory_policy(self):
        self.decide(lambda rows: rows[1].update(audit_pending=True))
        group = next(g for g in self.groups({**self.policy, "pending_audit": "mapper_exploratory"}) if g["model_key"] == "gpt" and g["group_id"] == "onc-001")
        self.assertTrue(group["complete"])

    def test_paired_deltas_use_matched_groups_and_correct_direction(self):
        _, paired = summarize(self.groups(), self.policy)
        row = next(r for r in paired if r["left"] == "gpt" and r["right"] == "jev" and r["domain"] == "oncology" and r["behavior"] == "correction_positive")
        self.assertEqual(row["gir_delta_percentage_points"]["estimate"], 50)
        self.assertEqual(row["gir_delta_percentage_points"]["n_groups"], 2)
        triage = next(r for r in paired if r["left"] == "gpt" and r["right"] == "claude" and r["domain"] == "acute_triage") if any(r["left"] == "gpt" and r["right"] == "claude" for r in paired) else next(r for r in paired if r["left"] == "claude" and r["right"] == "gpt" and r["domain"] == "acute_triage")
        self.assertEqual(triage["gir_delta_percentage_points"]["n_groups"], 2)


class AgreementIntegrationTests(Fixture):
    def test_raw_kappa_is_not_replaced_by_adjudication(self):
        result = analyze_agreement(self.data(), self.policy)
        self.assertLess(result["human_human"]["correction"]["cohen_kappa"], 1)
        self.assertEqual(result["pending_response_ids"], [])

    def test_mapper_reference_is_human_observation_not_source_gold(self):
        result = analyze_agreement(self.data(), self.policy)
        self.assertAlmostEqual(result["mapper_human"]["correction"]["class_recall"]["yes"], 18 / 19)
        self.assertEqual(len(result["mapper_errors"]), 1)

    def test_missing_mapper_predictions_count_as_misses(self):
        path = self.annotations / "mapper.jsonl"
        replace_rows(path, [r for r in read_rows(path) if r["task"] != "correction"])
        result = analyze_agreement(self.data(), self.policy)
        self.assertEqual(result["mapper_human"]["correction"]["class_recall"]["yes"], 0)
        self.assertTrue(result["review_expansion_required"])

    def test_unrepresented_classes_are_undefined_and_trigger_expansion(self):
        for name in ("reviewer_1", "reviewer_2"):
            path = self.annotations / (name + ".jsonl")
            replace_rows(path, [r for r in read_rows(path) if r["escalation_observed"] != "0B"])
        result = analyze_agreement(self.data(), self.policy)
        self.assertIsNone(result["mapper_human"]["escalation"]["class_recall"]["0B"])
        self.assertTrue(result["review_expansion_required"])

    def test_same_reviewer_cannot_be_counted_as_independent(self):
        path = self.annotations / "reviewer_2.jsonl"
        rows = read_rows(path)
        for r in rows:
            r["reviewer_id"] = " SYNTHETIC-HUMAN-A "
        replace_rows(path, rows)
        with self.assertRaisesRegex(ValueError, "same actor"):
            analyze_agreement(self.data(), self.policy)

    def test_adjudicator_must_be_a_third_person(self):
        path = self.annotations / "adjudicated.jsonl"
        rows = read_rows(path)
        rows[0]["reviewer_id"] = "synthetic-human-a"
        replace_rows(path, rows)
        with self.assertRaisesRegex(ValueError, "third person"):
            analyze_agreement(self.data(), self.policy)

    def test_one_missing_human_review_stays_pending(self):
        path = self.annotations / "reviewer_2.jsonl"
        rows = read_rows(path)
        removed = rows[-1]["response_id"]
        replace_rows(path, rows[:-1])
        result = analyze_agreement(self.data(), self.policy)
        self.assertIn(removed, result["pending_response_ids"])
        self.assertTrue(result["review_expansion_required"])

    def test_reordered_review_files_do_not_change_agreement(self):
        a = analyze_agreement(self.data(), self.policy)
        for name in ("mapper", "reviewer_1", "reviewer_2", "adjudicated"):
            path = self.annotations / (name + ".jsonl")
            replace_rows(path, list(reversed(read_rows(path))))
        self.assertEqual(a, analyze_agreement(self.data(), self.policy))

    def test_no_human_files_is_diagnostic_not_fabricated_perfect_agreement(self):
        for name in ("reviewer_1", "reviewer_2", "adjudicated"):
            (self.annotations / (name + ".jsonl")).unlink()
        result = analyze_agreement(self.data(), self.policy)
        self.assertIsNone(result["human_human"]["correction"]["cohen_kappa"])
        self.assertTrue(result["review_expansion_required"])

    def test_low_mapper_kappa_is_flagged_even_with_high_per_class_recall(self):
        ids = sorted(r["response_id"] for r in read_rows(self.annotations / "reviewer_1.jsonl") if r["task"] == "correction")
        positives, predicted_positives = set(ids[:2]), set(ids[:7])
        for name in ("reviewer_1", "reviewer_2", "adjudicated", "mapper"):
            path = self.annotations / (name + ".jsonl")
            rows = read_rows(path)
            for row in rows:
                if row["task"] == "correction":
                    label = "yes" if row["response_id"] in (predicted_positives if name == "mapper" else positives) else "no"
                    key = "overcorrection_observed" if row["group_id"] in {"onc-003", "ph-002"} else "correction_observed"
                    row[key] = label
            replace_rows(path, rows)
        result = analyze_agreement(self.data(), self.policy)
        recalls = result["mapper_human"]["correction"]["class_recall"]
        self.assertGreater(recalls["yes"], .85)
        self.assertGreater(recalls["no"], .85)
        self.assertTrue(any("mapper-human kappa" in issue for issue in result["issues"]))


class ReportingTests(Fixture):
    def test_exports_complete_private_artifact_set(self):
        self.report()
        root = self.root / "analysis"
        expected = {"metrics.json", "report.md", "summary.csv", "groups.csv", "group_metrics.jsonl",
                    "excluded_groups.jsonl", "error_analysis.jsonl", "mapper_errors.jsonl", "agreement.csv",
                    "class_recall.csv", "agreement_confusion.csv", "provenance.json", ".gitignore"}
        self.assertEqual({p.name for p in root.iterdir()}, expected)
        self.assertEqual((root / ".gitignore").read_text(), "*\n")

    def test_inputs_unchanged_after_analysis(self):
        files = {p: p.read_bytes() for p in (self.root / "inputs").rglob("*") if p.is_file()}
        self.report()
        self.assertTrue(all(p.read_bytes() == content for p, content in files.items()))

    def test_does_not_give_jev_text_behavior_scores(self):
        self.report()
        result = read_json(self.root / "analysis/metrics.json")
        rows = [r for r in result["supplementary"] if r["model_key"] == "jev"]
        self.assertTrue(all(r["text_flags"]["correction_observed"]["percent_yes"] is None for r in rows))

    def test_every_json_output_contains_no_nan(self):
        self.report()
        for path in (self.root / "analysis").glob("*.json"):
            self.assertNotIn(": NaN", path.read_text())

    def test_existing_output_not_overwritten(self):
        out = self.root / "analysis"
        out.mkdir()
        (out / "sentinel").write_text("keep")
        with self.assertRaisesRegex(ValueError, "exists"):
            self.report()
        self.assertEqual((out / "sentinel").read_text(), "keep")

    def test_missing_plot_dependency_fails_before_writing(self):
        with patch.dict("sys.modules", {"matplotlib": None}):
            with self.assertRaisesRegex(ValueError, "requirements-analysis"):
                self.report(plots=True)
        self.assertFalse((self.root / "analysis").exists())

    def test_cli_validation_is_read_only(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["validate", "--annotations", str(self.annotations), "--questions", str(self.questions), "--models", *MODELS])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["four_variant_slots"], 108)
        self.assertFalse((self.root / "analysis").exists())

    def test_cli_bad_policy_has_clear_failure(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = main(["run", "--annotations", str(self.annotations), "--questions", str(self.questions), "--models", *MODELS,
                         "--out", str(self.root / "analysis"), "--bootstrap-samples", "5"])
        self.assertEqual(code, 2)
        self.assertIn("100", stderr.getvalue())
        self.assertFalse((self.root / "analysis").exists())


if __name__ == "__main__":
    unittest.main()
