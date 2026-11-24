"""Versioned file adapter: analysis never imports or modifies the unmerged scoring package."""

import csv
import hashlib
import json
from pathlib import Path

VARIANTS = ("original", "p1", "p2", "p3")
PARAPHRASES = VARIANTS[1:]
INVALIDATION_REQUIRED = ("run_id", "group_id", "variant_id", "reason", "invalidated_by")
INVALIDATION_OPTIONAL = ("invalidated_at", "notes")
CARE_LABELS = ("0A", "0B", "1A", "1B")
DOMAINS = {"oncology": "correction", "public_health": "correction", "acute_triage": "escalation"}
STATUSES = {"scored", "unscorable", "unmapped", "model_error", "empty_response", "missing_response"}
DEFAULT_POLICY = {
    "policy_version": "analysis-v1-proposed", "control_gir": "separate", "bootstrap_samples": 2000,
    "seed": 566, "incomplete_groups": "exclude_and_bound", "accuracy": "report_both",
    "pending_audit": "exclude", "kappa_threshold": .80, "mapper_recall_threshold": .85,
    "minimum_audit_fraction": .20,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256((value if isinstance(value, str) else canonical(value)).encode()).hexdigest()


def read_json(path):
    with open(path, encoding="utf-8-sig") as stream:
        return json.load(stream)


def read_rows(path):
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if path.suffix.lower() == ".csv":
            reader = csv.DictReader(stream)
            require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), "Duplicate/missing CSV columns")
            rows = list(reader)
            require(all(None not in r and all(v is not None for v in r.values()) for r in rows), "Malformed CSV cells")
        else:
            rows = [json.loads(line) for line in stream if line.strip()]
    require(all(isinstance(r, dict) for r in rows), f"{path}: rows must be JSON objects")
    return rows


def index(rows, key, name):
    result = {}
    for row in rows:
        value = key(row)
        require(value not in result, f"Duplicate {name}: {value}")
        result[value] = row
    return result


def text(value, name):
    require(isinstance(value, str) and value.strip(), f"{name} must be nonempty text")
    return value


def policy_from(path=None, overrides=None):
    policy = dict(DEFAULT_POLICY)
    if path:
        supplied = read_json(path)
        require(isinstance(supplied, dict) and not set(supplied) - set(policy), "Unknown analysis policy fields")
        policy.update(supplied)
    if overrides:
        policy.update({k: v for k, v in overrides.items() if v is not None})
    require(policy["control_gir"] in {"separate", "pooled"}, "control_gir must be separate or pooled")
    require(policy["incomplete_groups"] == "exclude_and_bound", "Unsupported incomplete-group policy")
    require(policy["accuracy"] == "report_both", "Unsupported accuracy policy")
    require(policy["pending_audit"] in {"exclude", "mapper_exploratory"}, "Unsupported pending-audit policy")
    require(isinstance(policy["bootstrap_samples"], int) and not isinstance(policy["bootstrap_samples"], bool)
            and policy["bootstrap_samples"] >= 100, "At least 100 bootstrap samples required")
    require(isinstance(policy["seed"], int) and not isinstance(policy["seed"], bool), "Seed must be integer")
    for key in ("kappa_threshold", "mapper_recall_threshold", "minimum_audit_fraction"):
        value = policy[key]
        require(isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1, f"Invalid {key}")
    text(policy["policy_version"], "policy_version")
    return policy


def load_workspace(path):
    """Optional PR #21 raw-observation adapter, bound to its frozen state hash."""
    if not path:
        return None, []
    root = Path(path)
    manifest_file = root / "manifest.json"
    state_file = root / "private/state.json"
    content = state_file.read_text(encoding="utf-8")
    require(digest(content) == read_json(manifest_file)["state_sha256"], "Scoring workspace state changed")
    state = json.loads(content)
    require(state.get("scorer_version") == "response-scoring-v1", "Unsupported scoring workspace version; use guide-format files instead")
    return state, [manifest_file, state_file]


def load_invalidations(path, run_id, questions, decisions):
    """Explicit post-collection paraphrase invalidations (guide v1.2 §9.1 rule 5).

    Each row names one collected paraphrase slot of THIS run. It excludes the group from primary GIR for
    every model. Decision records are kept, and no replacement paraphrase is ever accepted in their place:
    analysis reads a single run, so later responses to a new paraphrase cannot be mixed in.
    """
    if not path:
        return {}
    rows = read_rows(path)
    collected = {(gid, vid) for _, gid, vid in decisions}
    result = {}
    for row in rows:
        unknown = set(row) - set(INVALIDATION_REQUIRED) - set(INVALIDATION_OPTIONAL)
        require(not unknown, f"Unknown paraphrase-invalidation fields: {sorted(unknown)}")
        for field in INVALIDATION_REQUIRED:
            text(row.get(field), f"invalidation {field}")
        gid, vid = row["group_id"], row["variant_id"]
        require(row["run_id"] == run_id,
                f"{gid}/{vid}: invalidation names run {row['run_id']!r}, not the analyzed run; invalidations "
                "apply to the collected run and are never carried to a rerun with substituted paraphrases")
        require(gid in questions, f"{gid}: invalidation for an unknown question group")
        require(vid in PARAPHRASES, f"{gid}/{vid}: only paraphrases p1/p2/p3 can be invalidated; an unusable "
                "original is an eligibility exclusion, not a paraphrase invalidation")
        require((gid, vid) in collected, f"{gid}/{vid}: not collected in this run; post-collection invalidations "
                "must refer to collected paraphrases (fix paraphrases before collection, guide §6)")
        require((gid, vid) not in result, f"Duplicate invalidation for {gid}/{vid}")
        result[gid, vid] = {k: row[k] for k in (*INVALIDATION_REQUIRED, *INVALIDATION_OPTIONAL) if k in row}
    return result


def load_data(annotations, questions, models=None, scoring_work=None, invalidations=None):
    root = Path(annotations)
    decisions_file = root / "decisions.jsonl"
    raw_decisions = read_rows(decisions_file)
    qs = index(read_rows(questions), lambda q: q["group_id"], "question group")
    require(qs, "No source question groups")
    files = [decisions_file, Path(questions)]
    for group, q in qs.items():
        text(group, "group_id")
        require(q.get("domain") in DOMAINS and q.get("task") == DOMAINS[q["domain"]], f"{group}: invalid domain/task")
        require(isinstance(q.get("is_control"), bool), f"{group}: is_control must be boolean")
        allowed = CARE_LABELS if q["task"] == "escalation" else ("correction_required", "no_correction")
        require(q.get("gold_label_mapped") in allowed, f"{group}: invalid reference label")
        require(q["is_control"] == (q["gold_label_mapped"] == "no_correction"), f"{group}: inconsistent control/reference")
        require(q.get("eligibility_status") == "include", f"{group}: not included")
    workspace, extra = load_workspace(scoring_work)
    files += extra
    if workspace:
        declared = {k: spec["mode"] for k, spec in workspace["runner_manifest"]["models"].items()}
        require(models is None or set(models) == set(declared), "Explicit models disagree with scoring run")
        require({p["group_id"] for p in workspace["planned"]} == set(qs), "Question set differs from scoring workspace")
        models = sorted(declared)
    else:
        declared = {}
        require(models, "Declare --models explicitly, or supply --scoring-work (do not infer missing models from received rows)")
    require(len(models) == len(set(models)) and models, "Duplicate/empty model list")
    for model in models:
        text(model, "model key")
    row_by_id = index(raw_decisions, lambda row: row["response_id"], "response ID")
    rows = index(raw_decisions, lambda row: (row["model_key"], row["group_id"], row["variant_id"]), "model/group/variant")
    require(rows, "No decision rows")
    run_ids, guides = set(), set()
    for row in raw_decisions:
        rid = text(row.get("response_id"), "response_id")
        run_ids.add(text(row.get("run_id"), "run_id"))
        guides.add(text(row.get("guide_version"), "guide_version"))
        require(row["model_key"] in models and row["group_id"] in qs and row["variant_id"] in VARIANTS, f"{rid}: outside declared study scope")
        q = qs[row["group_id"]]
        require(row.get("domain") == q["domain"] and row.get("task") == q["task"]
                and type(row.get("is_control")) is bool and row["is_control"] == q["is_control"], f"{rid}: source/decision mismatch")
        mode = row.get("mode")
        require(mode in {"chat", "decision"}, f"{rid}: unsupported model mode")
        require(row["model_key"] not in declared or declared[row["model_key"]] == mode, "Model modes changed within a run")
        declared[row["model_key"]] = mode
        require(row.get("status") in STATUSES, f"{rid}: unsupported response status")
        require(isinstance(row.get("audit_pending"), bool), f"{rid}: audit_pending must be boolean")
        value = row.get("decision")
        labels = CARE_LABELS if row["task"] == "escalation" else ("yes", "no")
        require((value in labels) if row["status"] == "scored" else value is None, f"{rid}: status/decision mismatch")
        require(row.get("over_visible_cap") in (True, False, None), f"{rid}: invalid token-cap flag")
        if mode == "decision" and row["status"] == "scored":
            require(row.get("label_source") == "typed_choice", "Decision-only model must use a typed choice")
        text(row.get("label_source"), "label_source")
    require(len(run_ids) == len(guides) == 1, "Do not mix run IDs or annotation-guide versions")
    if workspace:
        require(run_ids == {workspace["runner_manifest"]["run_id"]}, "Scoring workspace run differs from decisions")
        expected = {p["response_id"]: p for p in workspace["planned"]}
        require(set(row_by_id) == set(expected), "Scoring export response IDs differ from frozen planned slots")
        for rid, row in row_by_id.items():
            p = expected[rid]
            require((row["model_key"], row["group_id"], row["variant_id"]) == (p["model_key"], p["group_id"], p["variant_id"]), "Frozen response identity differs")
        original_responses = {r["response_id"]: r for r in workspace["responses"]}
        for rid, row in row_by_id.items():
            if row["mode"] == "decision" and rid in original_responses:
                original = original_responses[rid]
                choice = original.get("decision") if not original.get("error") else None
                require(row["decision"] == choice, "Exported typed choice differs from original runner output")
        for model in models:
            versions = {m["model_version"] for m in workspace["metadata"] if m["model_key"] == model}
            require(len(versions) <= 1, f"{model}: returned model version changed within this run; analyze separate frozen runs")
    invalidated = load_invalidations(invalidations, next(iter(run_ids)), qs, rows)
    if invalidations:
        files.append(Path(invalidations))
    export_summary = None
    summary_file = root / "scoring_summary.json"
    if summary_file.exists():
        export_summary = read_json(summary_file)
        require(export_summary.get("run_id") in run_ids, "Scoring summary belongs to another run")
        files.append(summary_file)
    return {"annotations": root, "questions": qs, "decisions": rows, "by_id": row_by_id,
            "models": list(models), "modes": {m: declared.get(m) for m in models}, "run_id": next(iter(run_ids)),
            "guide_version": next(iter(guides)), "workspace": workspace, "work_path": Path(scoring_work) if scoring_work else None,
            "files": files, "scoring_summary": export_summary, "invalidations": invalidated}


def load_annotations(data, name):
    path = data["annotations"] / (name + ".jsonl")
    if not path.exists():
        return {}
    data["files"].append(path)
    rows = index(read_rows(path), lambda row: row["response_id"], name + " response")
    for rid, row in rows.items():
        require(rid in data["by_id"], f"{name}: unknown response ID")
        d = data["by_id"][rid]
        require(d["mode"] == "chat", "Decision-only models must not have text annotations")
        require(all(row.get(k) == d[k] for k in ("group_id", "variant_id", "task", "guide_version")), f"{name}: annotation identity/version mismatch")
        text(row.get("reviewer_id"), "reviewer_id")
    return rows


def provenance(files):
    return [{"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(set(files), key=str)]
