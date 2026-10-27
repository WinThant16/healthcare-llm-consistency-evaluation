"""Question schema (codebase.md §5.1) and validator.

Two stages share one schema:
- ``candidates``: everything a loader emits into ``data/processed/candidates/``. No ``group_id`` yet,
  ``eligibility_status`` may be ``pending``, and ``target_false_premise`` may still be empty.
- ``final``: rows in ``data/processed/questions.jsonl`` after sampling and review. Every row has a
  ``group_id``, a decided eligibility, and a target premise for positive correction items.
"""

import json
import re
from pathlib import Path

DOMAINS = {"oncology": "onc", "public_health": "ph", "acute_triage": "tri"}
TASKS = {"oncology": "correction", "public_health": "correction", "acute_triage": "escalation"}
SOURCE_DATASETS = {"cancer_myth", "cancer_myth_nfp", "uphill", "care_bench"}
CORRECTION_LABELS = {"correction_required", "no_correction"}
ESCALATION_LABELS = {"0A", "0B", "1A", "1B"}
ELIGIBILITY = {"include", "exclude", "hold"}
REVIEW_STATUS = {"pending", "independently_reviewed", "adjudicated"}
STAGES = ("candidates", "final")

FIELDS = [
    "candidate_id",
    "group_id",
    "domain",
    "task",
    "is_control",
    "source_dataset",
    "source_id",
    "original_question",
    "source_label_raw",
    "gold_label_mapped",
    "target_false_premise",
    "source_evidence",
    "eligibility_status",
    "eligibility_reason",
    "review_status",
    "source_meta",
]

GROUP_ID_RE = re.compile(r"^(onc|ph|tri)-\d{3}$")


def candidate_id(domain, source_id):
    """Stable pool ID, e.g. ``onc-cm-412`` or ``tri-meb_v1_case_0001_r01``."""
    return f"{DOMAINS[domain]}-{source_id}"


def make_row(**values):
    """Build a row with every schema field present, in schema order."""
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise KeyError(f"unknown fields: {sorted(unknown)}")
    row = {f: None for f in FIELDS}
    row.update(values)
    return row


def validate_row(row, stage="candidates"):
    """Return a list of error strings; empty means valid."""
    errors = []
    missing = [f for f in FIELDS if f not in row]
    extra = [f for f in row if f not in FIELDS]
    if missing:
        errors.append(f"missing fields: {missing}")
    if extra:
        errors.append(f"unknown fields: {extra}")
    if missing:
        return errors

    def req_str(field):
        if not isinstance(row[field], str) or not row[field].strip():
            errors.append(f"{field} must be a non-empty string")

    for f in ("candidate_id", "source_id", "original_question", "source_evidence"):
        req_str(f)

    domain = row["domain"]
    if domain not in DOMAINS:
        errors.append(f"domain {domain!r} not in {sorted(DOMAINS)}")
    elif row["task"] != TASKS[domain]:
        errors.append(f"task {row['task']!r} invalid for domain {domain!r} (expected {TASKS[domain]!r})")
    elif isinstance(row["candidate_id"], str) and not row["candidate_id"].startswith(DOMAINS[domain] + "-"):
        errors.append(f"candidate_id must start with {DOMAINS[domain]!r}-")

    if row["source_dataset"] not in SOURCE_DATASETS:
        errors.append(f"source_dataset {row['source_dataset']!r} not in {sorted(SOURCE_DATASETS)}")
    if not isinstance(row["is_control"], bool):
        errors.append("is_control must be a bool")
    if row["source_label_raw"] is None:
        errors.append("source_label_raw is required")
    if row["source_meta"] is not None and not isinstance(row["source_meta"], dict):
        errors.append("source_meta must be an object or null")

    task = row["task"]
    gold = row["gold_label_mapped"]
    premise = row["target_false_premise"]
    if task == "correction":
        if gold not in CORRECTION_LABELS:
            errors.append(f"gold_label_mapped {gold!r} not in {sorted(CORRECTION_LABELS)}")
        elif row["is_control"] != (gold == "no_correction"):
            errors.append("is_control must be true exactly when gold_label_mapped == 'no_correction'")
        if gold == "no_correction" and premise is not None:
            errors.append("target_false_premise must be null for controls")
    elif task == "escalation":
        if gold not in ESCALATION_LABELS:
            errors.append(f"gold_label_mapped {gold!r} not in {sorted(ESCALATION_LABELS)}")
        if row["is_control"]:
            errors.append("escalation items have no controls; is_control must be false")
        if premise is not None:
            errors.append("target_false_premise must be null for escalation items")

    status = row["eligibility_status"]
    allowed_status = ELIGIBILITY | ({"pending"} if stage == "candidates" else set())
    if status not in allowed_status:
        errors.append(f"eligibility_status {status!r} not in {sorted(allowed_status)}")
    if status in {"exclude", "hold"} and not row["eligibility_reason"]:
        errors.append(f"eligibility_reason required when status is {status!r}")
    if row["review_status"] not in REVIEW_STATUS:
        errors.append(f"review_status {row['review_status']!r} not in {sorted(REVIEW_STATUS)}")

    group_id = row["group_id"]
    if stage == "candidates":
        if group_id is not None:
            errors.append("group_id must be null in candidate pools (assigned at sampling)")
    else:
        if not isinstance(group_id, str) or not GROUP_ID_RE.match(group_id):
            errors.append(f"group_id {group_id!r} must match {GROUP_ID_RE.pattern}")
        elif domain in DOMAINS and not group_id.startswith(DOMAINS[domain] + "-"):
            errors.append(f"group_id prefix does not match domain {domain!r}")
        if task == "correction" and gold == "correction_required" and status == "include":
            if not isinstance(premise, str) or not premise.strip():
                errors.append("target_false_premise required for included positive correction items")
    return errors


def validate_rows(rows, stage="candidates"):
    """Validate rows plus cross-row uniqueness. Returns ``[(line_no, error), ...]``."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    problems = []
    seen = {}
    for i, row in enumerate(rows, start=1):
        problems += [(i, e) for e in validate_row(row, stage)]
        keys = [("candidate_id", row.get("candidate_id")), ("source", (row.get("source_dataset"), row.get("source_id")))]
        if stage == "final":
            keys.append(("group_id", row.get("group_id")))
        for name, key in keys:
            if key in seen.setdefault(name, {}):
                problems.append((i, f"duplicate {name} {key!r} (first on line {seen[name][key]})"))
            else:
                seen[name][key] = i
    return problems


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
