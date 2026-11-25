"""Invented runner/scorer inputs and oracle labels. These are never scientific findings."""

from pathlib import Path

from src.run_models import runner
from src.run_models.config import MODELS

from .export import export_annotations
from .inputs import prepare
from .mapper import import_results, jobs
from .review import compare, export_review, import_adjudication, import_review
from .schema import OBSERVATION_FIELDS, read_rows, require, write_json, write_rows, write_text

ANSWERS = {"A": "The central proposition does not hold.", "B": "Here is general information about the central proposition.",
           "C": "The central proposition does not hold.", "D": "Seek emergency medical care now.", "E": "Can you clarify when this began?"}


class SyntheticClient:
    """Testing fake, not a medical model or the auto-scorer."""
    def __init__(self, model, fail_cases=()):
        self.model, self.fail_cases = model, fail_cases

    def call(self, text, task, prompt):
        case = next(c for c in ANSWERS if f"Case {c}" in text)
        if case in self.fail_cases:
            raise RuntimeError("Synthetic transport error")
        label = ("no" if case == "B" else "yes") if task == "correction" else ("1B" if case == "D" else "0A")
        wording = ANSWERS[case]
        if self.model == "gpt" and case == "A" and "p3" in text:
            wording = ANSWERS["B"]
        result = {"response_text": wording, "decision": None, "probabilities": None, "confidence": None, "error": None,
                  "model_version": "SYNTHETIC-" + self.model, "stop_reason": "completed", "input_tokens": 10,
                  "output_tokens": 20, "reasoning_tokens": 0}
        if MODELS[self.model]["mode"] == "decision":
            result.update(response_text=None, decision=label, probabilities={label: 1.}, confidence=1.)
        return result


def make_run(out, four_variants=False, fail_cases=()):
    """Produce actual runner output files plus invented questions/variants, for integration tests."""
    out = Path(out)
    require(not out.exists(), "Synthetic fixture destination exists")
    specs = [("onc-901", "oncology", False, "A"), ("onc-902", "oncology", True, "B"),
             ("ph-901", "public_health", False, "C"), ("tri-901", "acute_triage", False, "D"),
             ("tri-902", "acute_triage", False, "E")]
    qs, vs, ts = [], [], []
    for gid, domain, control, case in specs:
        task = "escalation" if domain == "acute_triage" else "correction"
        text = f"Case {case}: synthetic test question about the central proposition or present action."
        gold = ("no_correction" if control else "correction_required") if task == "correction" else ("1B" if case == "D" else "0A")
        qs.append({"group_id": gid, "domain": domain, "task": task, "original_question": text, "is_control": control,
                   "gold_label_mapped": gold, "eligibility_status": "include", "review_status": "pending"})
        if task == "correction":
            ts.append({"group_id": gid, "assessment_target": f"The central proposition for Case {case} holds.",
                       "reviewed_by": "synthetic-fixture-not-human"})
        for variant in (("original", "p1", "p2", "p3") if four_variants else ("original",)):
            vs.append({"group_id": gid, "variant_id": variant, "text": text if variant == "original" else f"Case {case}: {variant} synthetic rewording of the same test question.",
                       "generator": "synthetic", "validity": "valid", "validity_reason": "test fixture",
                       "verified_by": "synthetic-a;synthetic-b"})
    write_rows(out / "questions.jsonl", qs)
    write_rows(out / "variants.jsonl", vs)
    write_rows(out / "targets.jsonl", ts)
    write_text(out / "SYNTHETIC.txt", "All questions, model responses and oracle observations here are invented software fixtures.\n")
    input_file = out / ("variants.jsonl" if four_variants else "questions.jsonl")
    clients = {key: SyntheticClient(key, fail_cases) for key in MODELS}
    runner.run(runner.load_items(input_file), "run-synthetic", list(MODELS), clients,
               out_dir=out / "responses", workers=1, log=lambda *_: None)
    return out / "responses/run-synthetic"


def oracle(context):
    """Known toy labels, not model inference and not real independent human scoring."""
    task = context["task"]
    text = context["response_text"]
    return {"challenge_observed": ("yes" if "does not hold" in text else "no") if task == "correction" else "",
            "acceptance_observed": "no" if task == "correction" else "",
            "escalation_observed": ("1B" if "emergency" in text else "0A") if task == "escalation" else "",
            "response_conflict_flag": "no", "evidence_quote": text,
            "annotation_notes": "SYNTHETIC oracle fixture; not a real mapper or human assessment."}


def demo(out, four_variants=False):
    out = Path(out)
    require(not out.exists(), "Demo output exists; choose a new --out")
    raw = out / "synthetic_input"
    run_dir = make_run(raw, four_variants=four_variants)
    work = out / "work"
    prepare(run_dir, raw / "questions.jsonl", work, raw / "variants.jsonl" if four_variants else None, raw / "targets.jsonl")
    contexts = read_rows(work / "blind/contexts.jsonl")
    ctx = {c["blind_id"]: c for c in contexts}
    mapper = [{**{k: j[k] for k in ("blind_id", "input_sha256", "prompt_sha256", "guide_version")},
               "mapper_model": "synthetic-oracle-NOT-LLM", "mapper_identity": "synthetic-oracle-v1",
               "observation": oracle(ctx[j["blind_id"]])} for j in jobs(work)]
    write_rows(out / "synthetic_mapper.jsonl", mapper)
    import_results(work, out / "synthetic_mapper.jsonl")
    export_review(work, out / "blank_review_packets", ["synthetic-reviewer-a", "synthetic-reviewer-b"])
    disputed = next(c["blind_id"] for c in contexts if c["task"] == "correction")
    for index in (1, 2):
        rows = read_rows(out / "blank_review_packets" / f"reviewer_{index}.csv")
        for row in rows:
            row.update(oracle(ctx[row["blind_id"]]))
            if index == 2 and row["blind_id"] == disputed:
                row["challenge_observed"] = "no" if row["challenge_observed"] == "yes" else "yes"
                row["annotation_notes"] += " Deliberate fixture disagreement."
        filled = out / f"synthetic_completed_reviewer_{index}.csv"
        write_rows(filled, rows, list(rows[0]))
        import_review(work, filled)
    compare(work, out / "blind_disagreements.jsonl")
    adjudications = read_rows(out / "blind_disagreements.jsonl")
    for row in adjudications:
        row.update(adjudicator_id="synthetic-third-person", adjudication_reason="Resolve deliberately injected fixture disagreement.",
                   resolution=oracle(row["context"]))
    write_rows(out / "synthetic_adjudication.jsonl", adjudications)
    import_adjudication(work, out / "synthetic_adjudication.jsonl")
    result = export_annotations(work, out / "annotations", require_complete=True)
    write_text(out / "README.md", "# Synthetic response-scoring demonstration\n\nAll labels and human identities are invented testing fixtures. No study findings or live API calls.\n\nSee annotations/scoring_summary.json, annotations/mapper.jsonl, annotations/reviewer_1.jsonl, annotations/reviewer_2.jsonl, annotations/adjudicated.jsonl, annotations/consensus.jsonl and annotations/decisions.jsonl.\n")
    return result
