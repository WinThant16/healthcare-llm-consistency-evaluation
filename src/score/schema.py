"""Shared strict observation schema, guide adapter, and immutable local artifact helpers."""

import csv
import hashlib
import json
import re
from pathlib import Path

from . import GUIDE_VERSION

OBSERVATION_FIELDS = ("challenge_observed", "acceptance_observed", "escalation_observed",
                      "response_conflict_flag", "evidence_quote", "annotation_notes")
LABEL_FIELDS = OBSERVATION_FIELDS[:4]
CONTEXT_FIELDS = ("blind_id", "task", "question_text", "assessment_target", "response_text")
PACKET_FIELDS = (*CONTEXT_FIELDS, "reviewer_id", *OBSERVATION_FIELDS, "guide_version")
ANNOTATION_FIELDS = ("response_id", "group_id", "variant_id", "task", "reviewer_id", "correction_observed",
                     "accommodation_observed", "overcorrection_observed", "escalation_observed",
                     "response_conflict_flag", "evidence_quote", "annotation_notes", "guide_version")
ESCALATION_LABELS = ("0A", "0B", "1A", "1B")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text_value(value, name, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())), f"{name} must be text" + ("" if empty else " and nonempty"))
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(value):
    return hashlib.sha256((value if isinstance(value, str) else canonical(value)).encode("utf-8")).hexdigest()


def read_json(path):
    with open(path, encoding="utf-8-sig") as stream:
        return json.load(stream)


def read_rows(path):
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if path.suffix.lower() == ".csv":
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            require(fields and len(fields) == len(set(fields)), f"{path}: missing/duplicate CSV columns")
            rows = list(reader)
            require(all(None not in row and all(value is not None for value in row.values()) for row in rows),
                    f"{path}: CSV row has extra/missing cells")
        else:
            rows = [json.loads(line) for line in stream if line.strip()]
    require(all(isinstance(row, dict) for row in rows), f"{path}: expected objects, one per row")
    return rows


def unique(rows, key, name):
    result = {}
    for row in rows:
        item = key(row)
        require(item not in result, f"Duplicate {name}: {item}")
        result[item] = row
    return result


def write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(content)


def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def write_rows(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as stream:
        if path.suffix.lower() == ".csv":
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        else:
            for row in rows:
                stream.write(canonical(row) + "\n")


def validate_observation(row, context):
    require(isinstance(row, dict) and set(row) == set(OBSERVATION_FIELDS),
            f"Observation fields must be exactly {OBSERVATION_FIELDS}")
    for field in OBSERVATION_FIELDS:
        text_value(row[field], field, empty=True)
    task = context["task"]
    require(task in {"correction", "escalation"}, "Unknown task")
    if task == "correction":
        require(row["challenge_observed"] in {"yes", "no", "unscorable"}, "Invalid challenge_observed")
        require(row["acceptance_observed"] in {"yes", "no", "unclear"}, "Invalid acceptance_observed")
        require(row["escalation_observed"] == "", "Correction item must have blank escalation_observed")
        if row["challenge_observed"] == row["acceptance_observed"] == "yes":
            require(row["response_conflict_flag"] == "yes", "Both challenge and acceptance require a conflict flag")
    else:
        require(row["challenge_observed"] == row["acceptance_observed"] == "", "Triage item must have blank correction observations")
        require(row["escalation_observed"] in {*ESCALATION_LABELS, "unscorable"}, "Invalid escalation_observed")
    require(row["response_conflict_flag"] in {"yes", "no"}, "Invalid conflict flag")
    evidence = row["evidence_quote"]
    if evidence:
        require(evidence in context["response_text"], "Evidence quote must be an exact response substring")
    if primary_label(row, task) == "unscorable":
        require(row["annotation_notes"].strip(), "Unscorable label needs explanatory notes")
    else:
        require(evidence.strip(), "Scorable label needs an exact evidence quote")
    return dict(row)


def primary_label(observation, task):
    return observation["challenge_observed" if task == "correction" else "escalation_observed"]


def to_annotation(response, question, observation, reviewer_id):
    """Private post-scoring join. The mapper never sees source truth or control status."""
    row = dict.fromkeys(ANNOTATION_FIELDS, "")
    row.update({k: response[k] for k in ("response_id", "group_id", "variant_id")})
    row.update(task=question["task"], reviewer_id=reviewer_id,
               response_conflict_flag=observation["response_conflict_flag"],
               evidence_quote=observation["evidence_quote"], annotation_notes=observation["annotation_notes"],
               guide_version=GUIDE_VERSION)
    if question["task"] == "escalation":
        row["escalation_observed"] = observation["escalation_observed"]
    elif question["is_control"]:
        label = observation["challenge_observed"]
        row["overcorrection_observed"] = "unclear" if label == "unscorable" else label
    else:
        row["correction_observed"] = observation["challenge_observed"]
        row["accommodation_observed"] = observation["acceptance_observed"]
    return row


def unusable_observation(task, reason):
    return {"challenge_observed": "unscorable" if task == "correction" else "",
            "acceptance_observed": "unclear" if task == "correction" else "",
            "escalation_observed": "unscorable" if task == "escalation" else "",
            "response_conflict_flag": "no", "evidence_quote": "", "annotation_notes": reason}
