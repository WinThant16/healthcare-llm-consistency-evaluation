"""Scorer-shaped synthetic files. No import of src.score, APIs or clinical datasets."""

import copy
from pathlib import Path

from .inputs import VARIANTS
from .report import analyze, create_private_directory, write_json, write_rows, write_text

GUIDE = "synthetic-analysis-contract-v1"
MODELS = {"gpt": "chat", "claude": "chat", "jev": "decision"}


def annotation(decision, label, actor):
    row = {"response_id": decision["response_id"], "group_id": decision["group_id"], "variant_id": decision["variant_id"],
           "task": decision["task"], "reviewer_id": actor, "correction_observed": "", "accommodation_observed": "",
           "overcorrection_observed": "", "escalation_observed": "", "response_conflict_flag": "no",
           "evidence_quote": "Synthetic evidence (not a real model response).",
           "annotation_notes": "SYNTHETIC oracle fixture; no real independent review.", "guide_version": GUIDE}
    if decision["task"] == "escalation":
        row["escalation_observed"] = label
    elif decision["is_control"]:
        row["overcorrection_observed"] = "unclear" if label == "unscorable" else label
    else:
        row.update(correction_observed=label, accommodation_observed="no")
    return row


def make_fixture(out, originals_only=False):
    out = create_private_directory(out)
    specs = [("onc-001", "oncology", "correction_required"), ("onc-002", "oncology", "correction_required"),
             ("onc-003", "oncology", "no_correction"), ("ph-001", "public_health", "correction_required"),
             ("ph-002", "public_health", "no_correction"),
             *[(f"tri-{n:03d}", "acute_triage", label) for n, label in enumerate(("0A", "0B", "1A", "1B"), 1)]]
    questions = [{"group_id": gid, "domain": domain, "task": "escalation" if domain == "acute_triage" else "correction",
                  "is_control": gold == "no_correction", "gold_label_mapped": gold, "eligibility_status": "include",
                  "original_question": "SYNTHETIC question; not source-derived clinical data."} for gid, domain, gold in specs]
    decisions, annotations = [], {name: [] for name in ("mapper", "reviewer_1", "reviewer_2", "adjudicated", "consensus")}
    for model, mode in MODELS.items():
        for q in questions:
            for variant in (("original",) if originals_only else VARIANTS):
                gid, task = q["group_id"], q["task"]
                label = q["gold_label_mapped"] if task == "escalation" else ("no" if q["is_control"] else "yes")
                if model == "gpt" and gid == "onc-001" and variant == "p3":
                    label = "no"  # one inconsistent group among two oncology positives
                if model == "claude" and gid == "ph-001":
                    label = "no"  # consistently wrong, not inconsistent
                if (model, gid, variant) == ("claude", "tri-002", "p2"):
                    continue  # absence is still visible in the expected four-slot grid
                status = "scored"
                if (model, gid, variant) == ("gpt", "tri-004", "p1"):
                    label, status = None, "model_error"
                row = {"response_id": f"synthetic:{model}:{gid}:{variant}", "run_id": "run-synthetic-analysis",
                       "model_key": model, "mode": mode, "group_id": gid, "variant_id": variant,
                       "task": task, "domain": q["domain"], "is_control": q["is_control"], "decision": label,
                       "status": status, "label_source": "typed_choice" if mode == "decision" else "human_agreed",
                       "audit_pending": False, "guide_version": GUIDE, "target_context": "verified_neutral_target",
                       "over_visible_cap": None if mode == "decision" else False,
                       "probabilities": {label: 1.} if mode == "decision" else None,
                       "confidence": 1. if mode == "decision" else None}
                if (model, gid, variant) == ("gpt", "onc-001", "original"):
                    row["label_source"] = "human_adjudicated"
                decisions.append(row)
                if mode == "decision" or status != "scored":
                    continue
                for name, actor in (("mapper", "mapper:synthetic"), ("reviewer_1", "synthetic-human-a"),
                                    ("reviewer_2", "synthetic-human-b"), ("consensus", "synthetic-human-a;synthetic-human-b")):
                    observed = label
                    if name == "mapper" and (model, gid, variant) == ("gpt", "onc-002", "p1"):
                        observed = "no"  # independent mapper mismatch for recall validation
                    if name == "reviewer_2" and (model, gid, variant) == ("gpt", "onc-001", "original"):
                        observed = "no"  # raw disagreement is retained after third-person resolution
                    annotations[name].append(annotation(row, observed, actor))
                if row["label_source"] == "human_adjudicated":
                    annotations["adjudicated"].append(annotation(row, label, "synthetic-third-person"))
    write_rows(out / "questions.jsonl", questions)
    annotation_dir = create_private_directory(out / "annotations")
    write_rows(annotation_dir / "decisions.jsonl", decisions)
    for name, rows in annotations.items():
        write_rows(annotation_dir / (name + ".jsonl"), rows)
    write_json(annotation_dir / "scoring_summary.json", {"run_id": "run-synthetic-analysis", "issues": [],
               "planned_responses": len(questions) * len(MODELS) * (1 if originals_only else 4),
               "status": "SYNTHETIC CALIBRATION"})
    write_text(out / "SYNTHETIC.txt", "Invented inputs and human/mapper labels for software verification only. No scientific findings.\n")
    return annotation_dir, out / "questions.jsonl"


def demo(out, originals_only=False, plots=False, bootstrap_samples=500):
    out = create_private_directory(out)
    annotations, questions = make_fixture(out / "synthetic_input", originals_only)
    return analyze(annotations, questions, out / "analysis", models=list(MODELS), plots=plots,
                   bootstrap_samples=bootstrap_samples, study_label="SYNTHETIC ANALYSIS DEMONSTRATION — NOT MODEL RESULTS")
