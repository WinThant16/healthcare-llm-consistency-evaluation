"""Consume the actual runner's per-model JSONL files, params, decision mode and error log."""

import math
import secrets
from datetime import datetime
from pathlib import Path

from src.data.schema import DOMAINS, TASKS
from src.run_models.runner import load_items, response_id

from . import GUIDE_VERSION, SCORER_VERSION
from .schema import (CONTEXT_FIELDS, ESCALATION_LABELS, NAME_RE, read_json, read_rows, require, sha256,
                     text_value, unique, write_json, write_rows)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/response_mapper_v1.md"
# Guide v1.2 §4.4: question-only scoring is for the pilot and calibration only. Production correction
# scoring must use verified neutral targets for every correction group, controls included.
SCORING_MODES = ("pilot", "production")


def _probabilities(response, task):
    labels = set(ESCALATION_LABELS if task == "escalation" else ("yes", "no"))
    require(response.get("decision") in labels, "Unknown Jev choice")
    probs = response.get("probabilities")
    # The SDK/fake clients can return a sparse distribution. Preserve it; never invent missing values.
    require(isinstance(probs, dict) and probs and set(probs) <= labels and response["decision"] in probs,
            "Jev probabilities must contain the chosen class and only known classes")
    for value in probs.values():
        require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                and 0 <= value <= 1, "Invalid Jev probability")
    require(abs(sum(probs.values()) - 1) < .001, "Jev probabilities must sum to one")
    confidence = response.get("confidence")
    require(isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            and math.isfinite(confidence) and 0 <= confidence <= 1, "Invalid Jev confidence")


def _request_key(spec, text):
    # Recompute against the FROZEN run.json, never today's mutable runner configuration.
    import json
    payload = json.dumps([spec["model"], spec["prompt_version"], spec["prompt_sha256"], spec["params"], text], sort_keys=True)
    return sha256(payload)


def read_run(run_dir, questions_file, variants_file=None, targets_file=None):
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "run.json")
    run_id = text_value(manifest.get("run_id"), "run_id")
    specs = manifest.get("models")
    require(isinstance(specs, dict) and specs, "run.json needs model specifications")
    questions = read_rows(questions_file)
    qs = unique(questions, lambda q: q["group_id"], "question group")
    require(qs, "No questions")
    for group, q in qs.items():
        domain = q.get("domain")
        require(domain in DOMAINS and group.startswith(DOMAINS[domain] + "-"), f"{group}: invalid domain/group prefix")
        require(q.get("task") == TASKS[domain], f"{group}: task/domain mismatch")
        require(isinstance(q.get("is_control"), bool), f"{group}: is_control must be a JSON boolean")
        gold = q.get("gold_label_mapped")
        require(gold in (ESCALATION_LABELS if q["task"] == "escalation" else ("correction_required", "no_correction")),
                f"{group}: invalid reference label")
        require(q["is_control"] == (gold == "no_correction"), f"{group}: inconsistent control/reference label")
        require(q.get("eligibility_status") == "include", f"{group}: question not included")
        text_value(q.get("original_question"), "original_question")
    source_input = variants_file or questions_file
    items = load_items(source_input)
    require(items, "No runnable input items")
    for item in items:
        require(item["group_id"] in qs, "Runner input refers to an unknown question group")
        if item["variant_id"] == "original":
            require(item["text"] == qs[item["group_id"]]["original_question"], "Original text differs from question file")
    targets = read_rows(targets_file) if targets_file else []
    target_map = unique(targets, lambda row: row["group_id"], "neutral target")
    correction_groups = {i["group_id"] for i in items if i["task"] == "correction"}
    if targets_file:
        require(set(target_map) == correction_groups, "Supply neutral targets for ALL correction groups, including controls")
        for row in targets:
            text_value(row.get("assessment_target"), "assessment_target")
            text_value(row.get("reviewed_by"), "target reviewed_by")
    planned, responses, metadata, files = [], [], [], [run_dir / "run.json", Path(questions_file)]
    if variants_file:
        files.append(Path(variants_file))
    if targets_file:
        files.append(Path(targets_file))
    for model, spec in specs.items():
        require(NAME_RE.fullmatch(model), "Unsafe model key in run.json")
        require(spec.get("mode") in {"chat", "decision"}, f"{model}: unknown runner mode")
        for field in ("model", "prompt_version", "prompt_sha256"):
            text_value(spec.get(field), f"{model}/{field}")
        require(isinstance(spec.get("params"), dict), f"{model}: params object required")
        for item in items:
            planned.append({**item, "model_key": model, "mode": spec["mode"],
                            "response_id": response_id(run_id, model, item["group_id"], item["variant_id"])})
        path = run_dir / f"{model}.jsonl"
        if path.exists():
            for response in read_rows(path):
                responses.append({"payload": response, "file_model_key": model})
            files.append(path)
    rs = unique(responses, lambda row: row["payload"]["response_id"], "response ID")
    plans = {row["response_id"]: row for row in planned}
    metadata_path = run_dir / "metadata.jsonl"
    if metadata_path.exists():
        metadata = read_rows(metadata_path)
        files.append(metadata_path)
    ms = unique(metadata, lambda row: row["response_id"], "metadata response ID")
    require(rs.keys() == ms.keys(), "Response/metadata IDs differ; wait for the runner to finish or repair its incomplete write")
    for rid, wrapped in rs.items():
        require(rid in plans, "Received response is outside the supplied planned input; supply the matching variants file")
        row, plan, meta = wrapped["payload"], plans[rid], ms[rid]
        require(row.get("run_id") == run_id, "Mixed run IDs")
        require((row.get("group_id"), row.get("variant_id")) == (plan["group_id"], plan["variant_id"]), "Response identity mismatch")
        require(meta.get("model_key") == wrapped["file_model_key"] == plan["model_key"], "Response file/model metadata mismatch")
        spec = specs[plan["model_key"]]
        require(meta.get("params") == spec["params"] and meta.get("prompt_version") == spec["prompt_version"],
                "Metadata settings/prompt disagree with frozen run.json")
        require(meta.get("request_key") == _request_key(spec, plan["text"]),
                "Question/variant text or settings do not match the runner request hash")
        text_value(meta.get("model_version"), "returned model_version")
        try:
            timestamp = datetime.fromisoformat(meta["timestamp"].replace("Z", "+00:00"))
            require(timestamp.tzinfo is not None, "Metadata timestamp needs timezone")
        except (KeyError, AttributeError, ValueError):
            raise ValueError("Metadata timestamp must be ISO 8601 with timezone") from None
        allowed = {"response_id", "run_id", "group_id", "variant_id", "response_text", "error"}
        if plan["mode"] == "decision":
            allowed |= {"decision", "probabilities", "confidence"}
        require(not set(row) - allowed, "Response payload contains unexpected fields; metadata must remain separate")
        require(row.get("error") is None or isinstance(row["error"], str), "error must be text/null")
        if plan["mode"] == "decision":
            require(row.get("response_text") is None, "Decision-only model must have null response_text")
            if not row.get("error"):
                _probabilities(row, plan["task"])
        else:
            require(isinstance(row.get("response_text"), (str, type(None))), "response_text must be text/null")
    errors_path = run_dir / "errors.jsonl"
    errors = read_rows(errors_path) if errors_path.exists() else []
    if errors_path.exists():
        files.append(errors_path)
    raw_variants = read_rows(variants_file) if variants_file else []
    return {"runner_manifest": manifest, "questions": questions, "items": items, "planned": planned,
            "responses": [r["payload"] for r in responses], "metadata": metadata, "api_errors": errors,
            "targets": targets, "target_context": "verified_neutral_target" if targets_file else "question_only_calibration",
            "skipped_invalid_variants": sum(row.get("validity", "valid") != "valid" for row in raw_variants),
            "input_hashes": {str(p.resolve()): sha256(p.read_text(encoding="utf-8-sig")) for p in set(files)}}


def prepare(run_dir, questions, out, variants=None, targets=None, mode="pilot"):
    require(mode in SCORING_MODES, f"mode must be one of {SCORING_MODES}")
    require(mode != "production" or targets,
            "Production scoring requires --targets: verified neutral assessment targets for every correction "
            "group, controls included (guide v1.2 §4.4). Question-only mode is for the pilot/calibration only.")
    state = read_run(run_dir, questions, variants, targets)
    if mode == "production":
        # read_run already requires full correction-group coverage and a nonempty reviewed_by per row.
        require(state["target_context"] == "verified_neutral_target", "Production scoring needs verified targets")
    out = Path(out)
    require(not out.exists(), "Scoring workspace exists; choose a new --out")
    state.update(scorer_version=SCORER_VERSION, guide_version=GUIDE_VERSION, scoring_mode=mode,
                 mapper_prompt=PROMPT_PATH.read_text(encoding="utf-8"))
    state["prompt_sha256"] = sha256(state["mapper_prompt"])
    qs = {q["group_id"]: q for q in state["questions"]}
    plans = {p["response_id"]: p for p in state["planned"]}
    ts = {t["group_id"]: t["assessment_target"] for t in state["targets"]}
    secret = secrets.token_hex(32)
    contexts, crosswalk = [], []
    for response in state["responses"]:
        plan = plans[response["response_id"]]
        if plan["mode"] != "chat" or response.get("error") or not (response.get("response_text") or "").strip():
            continue
        blind_id = "b-" + sha256([secret, response["response_id"]])[:24]
        contexts.append({"blind_id": blind_id, "task": plan["task"], "question_text": plan["text"],
                         "assessment_target": ts.get(plan["group_id"], ""), "response_text": response["response_text"]})
        crosswalk.append({"blind_id": blind_id, "response_id": response["response_id"]})
    state.update(contexts=contexts, crosswalk=crosswalk)
    content = canonical_state(state)
    write_json(out / "manifest.json", {"state_sha256": sha256(content), "scorer_version": SCORER_VERSION})
    from .schema import write_text
    write_text(out / "private/state.json", content)
    write_rows(out / "blind/contexts.jsonl", contexts)
    result = coverage(state)
    write_json(out / "private/preparation_summary.json", result)
    return result


def canonical_state(state):
    from .schema import canonical
    return canonical(state) + "\n"


def load_state(work):
    work = Path(work)
    content = (work / "private/state.json").read_text(encoding="utf-8")
    require(sha256(content) == read_json(work / "manifest.json")["state_sha256"], "Frozen scoring state changed")
    import json
    state = json.loads(content)
    require(read_rows(work / "blind/contexts.jsonl") == state["contexts"], "Blind context file changed")
    return state


def scoring_mode(state):
    # Workspaces prepared before the mode existed were never production-guarded.
    return state.get("scoring_mode", "pilot")


def coverage(state):
    actual = {r["response_id"]: r for r in state["responses"]}
    errors = {row.get("response_id") for row in state["api_errors"]}
    missing = [{k: p[k] for k in ("response_id", "model_key", "group_id", "variant_id")}
               for p in state["planned"] if p["response_id"] not in actual]
    variants = {i["variant_id"] for i in state["items"]}
    return {"run_id": state["runner_manifest"]["run_id"], "planned_responses": len(state["planned"]),
            "received_responses": len(actual), "missing_responses": missing,
            "resolved_historical_api_error_ids": sorted(errors & actual.keys()),
            "model_outcome_errors": sum(bool(r.get("error")) for r in actual.values()),
            "over_visible_cap": sum(m.get("over_visible_cap") is True for m in state["metadata"]),
            "blind_mapper_jobs": len(state.get("contexts", [])), "target_context": state["target_context"],
            "scoring_mode": scoring_mode(state),
            "skipped_invalid_variants": state["skipped_invalid_variants"],
            "originals_only": variants == {"original"},
            "note": "Scoring does not calculate GIR. Missing wordings must not be treated as agreement."}


def export_target_template(questions_file, out):
    """Private, unblinded preparation sheet, not an independent response-review packet."""
    rows = [{"group_id": q["group_id"], "assessment_target": q.get("target_false_premise") or "", "reviewed_by": ""}
            for q in read_rows(questions_file) if q["task"] == "correction"]
    write_rows(out, rows)
    return {"rows": len(rows), "note": "Private draft: verify every neutral target and write reviewer ID, including controls, before use."}
