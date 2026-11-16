"""Write guide-compatible annotations and a private normalized handoff for future analysis."""

from pathlib import Path

from . import GUIDE_VERSION
from .inputs import coverage, load_state
from .mapper import validate_cached
from .review import adjudicated, consensus, plans, raw_reviews
from .schema import (primary_label, require, to_annotation, unusable_observation, write_json, write_rows)


def export_annotations(work, out, require_complete=False):
    state = load_state(work)
    mapper = validate_cached(work)
    human, pending = consensus(work)
    first, second = raw_reviews(work)
    adjudications = adjudicated(work)
    responses = {r["response_id"]: r for r in state["responses"]}
    questions = {q["group_id"]: q for q in state["questions"]}
    metadata = {r["response_id"]: r for r in state["metadata"]}
    bid_by_rid = {r["response_id"]: r["blind_id"] for r in state["crosswalk"]}
    annotation_sets = {name: [] for name in ("mapper", "reviewer_1", "reviewer_2", "adjudicated", "consensus")}
    decisions, missing_mapper = [], []
    for plan in state["planned"]:
        rid = plan["response_id"]
        response = responses.get(rid)
        q = questions[plan["group_id"]]
        bid = bid_by_rid.get(rid)
        obs, label, source, status = None, None, "missing_response", "missing_response"
        if response:
            error = response.get("error")
            if plan["mode"] == "decision":
                source = "typed_choice"
                label = response.get("decision") if not error else None
                status = "model_error" if error else "scored"
            else:
                for name, records in (("mapper", mapper), ("reviewer_1", first), ("reviewer_2", second), ("adjudicated", adjudications)):
                    if bid in records:
                        row = records[bid]
                        actor = row.get("reviewer_id") or "mapper:" + row["mapper_model"]
                        annotation_sets[name].append(to_annotation(response, q, row["observation"], actor))
                if error or not (response.get("response_text") or "").strip():
                    source = status = "model_error" if error else "empty_response"
                    obs = unusable_observation(q["task"], f"{status}: {error or 'no usable text'}")
                    actor = "system"
                    annotation_sets["mapper"].append(to_annotation(response, q, obs, actor))
                elif bid in human:
                    obs, source, actor = human[bid]["observation"], human[bid]["label_source"], human[bid]["reviewer_id"]
                elif bid in mapper:
                    obs, source, actor = mapper[bid]["observation"], "mapper", "mapper:" + mapper[bid]["mapper_model"]
                else:
                    status = source = "unmapped"
                    actor = ""
                if bid and bid not in mapper:
                    missing_mapper.append(bid)
                if obs:
                    raw = primary_label(obs, q["task"])
                    label = None if raw == "unscorable" else raw
                    if status not in {"model_error", "empty_response"}:
                        status = "unscorable" if label is None else "scored"
                    annotation_sets["consensus"].append(to_annotation(response, q, obs, actor))
        decisions.append({"response_id": rid, "run_id": state["runner_manifest"]["run_id"],
                          **{k: plan[k] for k in ("model_key", "mode", "group_id", "variant_id", "task")},
                          "domain": q["domain"], "is_control": q["is_control"], "decision": label,
                          "label_source": source, "status": status, "audit_pending": bid in pending,
                          "guide_version": GUIDE_VERSION, "target_context": state["target_context"],
                          "over_visible_cap": metadata.get(rid, {}).get("over_visible_cap"),
                          "probabilities": response.get("probabilities") if response else None,
                          "confidence": response.get("confidence") if response else None})
    issues = []
    if pending:
        issues.append(f"{len(pending)} selected human reviews/adjudications pending")
    if missing_mapper:
        issues.append(f"{len(missing_mapper)} chat responses lack mapper observations")
    if any(r["status"] == "missing_response" for r in decisions):
        issues.append("Runner still has missing planned responses")
    require(not require_complete or not issues, "Incomplete scoring: " + "; ".join(issues))
    out = Path(out)
    require(not out.exists(), "Annotation output directory exists; choose a new export destination")
    for name, rows in annotation_sets.items():
        write_rows(out / (name + ".jsonl"), sorted(rows, key=lambda row: row["response_id"]))
    write_rows(out / "decisions.jsonl", decisions)
    summary = {**coverage(state), "status": "CALIBRATION / NOT VALIDATED STUDY FINDINGS", "issues": issues,
               "mapper_observations": len(mapper), "human_consensus_observations": len(human),
               "pending_human_blind_ids": pending, "missing_mapper_blind_ids": missing_mapper,
               "text_flags_not_applicable_to_decision_models": True,
               "note": "require-complete checks coverage only, not scorer reliability or protocol approval. GIR/kappa/recall belong to analysis."}
    write_json(out / "scoring_summary.json", summary)
    return summary
