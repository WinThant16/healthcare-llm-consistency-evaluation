"""CARE-Bench v1.1 loader → acute_triage escalation candidates.

Uses round 1 prefixes only (a single patient turn, so no missing context). Round 1 has just 22 ``1B`` cases
for a target of 20; whether to draw on multi-turn rounds is an open team decision (data/README.md).
"""

import json

from src.data.schema import candidate_id, make_row

PREFIXES = "data/raw/care-bench/evaluation_prefixes_public.jsonl"
CASES = "data/raw/care-bench/cases_public.jsonl"
ROUND = 1


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load(prefixes_path=PREFIXES, cases_path=CASES):
    cases = {c["case_id"]: c for c in _read(cases_path)}
    rows = []
    for p in _read(prefixes_path):
        if p["round_id"] != ROUND:
            continue
        turns = p["input_patient_turns"]
        if len(turns) != 1:
            raise ValueError(f"{p['prefix_id']}: expected one patient turn in round {ROUND}, got {len(turns)}")
        case = cases[p["case_id"]]
        rows.append(make_row(
            candidate_id=candidate_id("acute_triage", p["prefix_id"]),
            domain="acute_triage",
            task="escalation",
            is_control=False,
            source_dataset="care_bench",
            source_id=p["prefix_id"],
            original_question=turns[0].strip(),
            source_label_raw=p["gold_label"],
            gold_label_mapped=p["gold_label"][:2],
            source_evidence=case["source_grounding"],
            eligibility_status="pending",
            review_status="pending",
            source_meta={
                "case_id": p["case_id"],
                "split": p["split"],
                "source_dataset": p["source_dataset"],
                "source_case_id": p["source_case_id"],
                "specialty_or_domain": p["specialty_or_domain"],
                "age_group": p["case_age_group"],
                "speaker_role": p["case_speaker_role"],
                "information_state": p["information_state"],
                "round_role": p["round_role"],
                "source_anchor_facts": case["source_anchor_facts"],
            },
        ))
    return rows
