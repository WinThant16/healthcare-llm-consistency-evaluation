"""Optional contract checks: skipped on main, automatically run when PR #21 is present.

Production src/analyze never imports src.score. Only these optional integration tests do so.
"""

import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from src.analyze.inputs import canonical, digest, read_json, read_rows
from src.analyze.report import analyze

try:
    HAS_SCORER = importlib.util.find_spec("src.score.demo") is not None
except ModuleNotFoundError as exc:
    if exc.name != "src.score":
        raise
    HAS_SCORER = False

@unittest.skipUnless(HAS_SCORER, "Optional integration: response-scoring PR #21 is not present on this checkout")
class ScoringContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.demo = importlib.import_module("src.score.demo").demo

    def run_analysis(self, source, name="analysis", raw=True):
        return analyze(source / "annotations", source / "synthetic_input/questions.jsonl", self.root / name,
                       scoring_work=source / "work" if raw else None,
                       models=None if raw else ["gpt", "claude", "jev"], bootstrap_samples=100)

    def test_raw_four_wording_contract(self):
        source = self.root / "score"
        self.demo(source, four_variants=True)
        result = self.run_analysis(source)
        self.assertEqual(result["coverage"]["planned_responses"], 60)
        self.assertEqual(result["coverage"]["complete_model_groups"], 15)
        metrics = read_json(self.root / "analysis/metrics.json")
        self.assertEqual(metrics["agreement"]["adapter"], "raw_scoring_workspace")
        self.assertLess(metrics["agreement"]["human_human"]["correction"]["cohen_kappa"], 1)
        row = next(r for r in metrics["summary"] if r["model_key"] == "gpt" and r["domain"] == "oncology" and r["behavior"] == "correction_positive")
        self.assertEqual(row["gir_percent"]["estimate"], 100)

    def test_originals_only_contract_does_not_invent_gir(self):
        source = self.root / "score"
        self.demo(source)
        self.run_analysis(source)
        metrics = read_json(self.root / "analysis/metrics.json")
        self.assertTrue(all(r["gir_percent"]["estimate"] is None for r in metrics["summary"]))
        self.assertEqual(metrics["coverage"]["scoring_export_planned_responses"], 15)
        self.assertEqual(metrics["coverage"]["missing_variant_records"], 45)

    def test_export_only_and_raw_adapter_match_primary_metrics(self):
        source = self.root / "score"
        self.demo(source, four_variants=True)
        self.run_analysis(source, name="raw")
        self.run_analysis(source, name="guide", raw=False)
        raw = read_json(self.root / "raw/metrics.json")
        guide = read_json(self.root / "guide/metrics.json")
        self.assertEqual(raw["summary"], guide["summary"])
        self.assertEqual(raw["agreement"]["human_human"], guide["agreement"]["human_human"])
        self.assertEqual(raw["agreement"]["mapper_human"], guide["agreement"]["mapper_human"])

    def test_stale_human_export_is_rejected(self):
        source = self.root / "score"
        self.demo(source, four_variants=True)
        path = next((source / "work/private/reviews/reviewer_2").glob("*.jsonl"))
        rows = read_rows(path)
        target = next(r for r in rows if r["observation"]["escalation_observed"])
        target["observation"]["response_conflict_flag"] = "yes"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaisesRegex(ValueError, "stale relative to human review"):
            self.run_analysis(source)
        self.assertFalse((self.root / "analysis").exists())

    def test_changed_workspace_state_is_rejected(self):
        source = self.root / "score"
        self.demo(source)
        path = source / "work/private/state.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "workspace state changed"):
            self.run_analysis(source)

    def test_returned_model_alias_drift_is_not_silently_pooled(self):
        source = self.root / "score"
        self.demo(source, four_variants=True)
        path = source / "work/private/state.json"
        state = read_json(path)
        state["metadata"][0]["model_version"] = "synthetic-new-version"
        content = canonical(state) + "\n"
        path.write_text(content)
        manifest_path = source / "work/manifest.json"
        manifest = read_json(manifest_path)
        manifest["state_sha256"] = digest(content)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "model version changed"):
            self.run_analysis(source)


if __name__ == "__main__":
    unittest.main()
