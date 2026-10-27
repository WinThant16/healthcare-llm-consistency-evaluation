import csv
from pathlib import Path

import pytest

from src.data import sample, screening
from src.data.sampling import TARGETS, priority_order
from src.data.schema import candidate_id, make_row, validate_rows, write_jsonl


def triage_row(i, gold, source):
    sid = f"case_{i}"
    return make_row(candidate_id=candidate_id("acute_triage", sid), domain="acute_triage", task="escalation",
                    is_control=False, source_dataset="care_bench", source_id=sid, original_question=f"Q{i}",
                    source_label_raw=gold, gold_label_mapped=gold, source_evidence="grounding",
                    eligibility_status="pending", review_status="pending", source_meta={"source_dataset": source})


def correction_row(domain, i, control, stratum):
    sid = f"src-{i}"
    meta = {"source_db": stratum} if domain == "public_health" else {"cancer": stratum}
    raw = "true" if control else ("false" if domain == "public_health" else {"category": stratum})
    return make_row(candidate_id=candidate_id(domain, sid), domain=domain, task="correction", is_control=control,
                    source_dataset="uphill" if domain == "public_health" else "cancer_myth", source_id=sid,
                    original_question=f"Q{i}", source_label_raw=raw,
                    gold_label_mapped="no_correction" if control else "correction_required",
                    target_false_premise=None if control or domain == "oncology" else f"claim {i}",
                    source_evidence="evidence", eligibility_status="pending", review_status="pending",
                    source_meta=meta)


@pytest.fixture
def dirs(tmp_path):
    pools = {
        "oncology": [correction_row("oncology", i, i % 4 == 0, f"cat{i % 3}") for i in range(120)],
        "public_health": [correction_row("public_health", i, i % 4 == 0, ["monant", "pubhealth"][i % 2])
                          for i in range(120)],
        "acute_triage": [triage_row(i, g, f"src{i % 3}") for i, g in
                         enumerate([g for g in ("0A", "0B", "1A", "1B") for _ in range(25)])],
    }
    pool_dir, decision_dir = tmp_path / "pools", tmp_path / "decisions"
    for domain, rows in pools.items():
        write_jsonl(rows, pool_dir / f"{domain}.jsonl")
    return pools, pool_dir, decision_dir, tmp_path


def write_sheet(path, decisions):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=screening.DECISION_FIELDS)
        writer.writeheader()
        writer.writerows(decisions)


def include_all(pools, premise_for_oncology=True):
    out = []
    for rows in pools.values():
        for r in rows:
            premise = "premise" if premise_for_oncology and r["domain"] == "oncology" and not r["is_control"] else ""
            out.append({"candidate_id": r["candidate_id"], "eligibility_status": "include", "eligibility_reason": "",
                        "target_false_premise": premise, "reviewed_by": "win;advait",
                        "review_status": "independently_reviewed"})
    return out


def test_priority_order_is_deterministic_and_interleaved(dirs):
    rows = dirs[0]["acute_triage"]
    first = priority_order(rows)
    assert [r["candidate_id"] for r in first["0A"]] == [r["candidate_id"] for r in priority_order(list(reversed(rows)))["0A"]]
    top3 = {r["source_meta"]["source_dataset"] for r in first["0A"][:3]}
    assert len(top3) == 3


def test_full_round_trip_writes_valid_final_dataset(dirs):
    pools, pool_dir, decision_dir, tmp = dirs
    write_sheet(tmp / "sheet.csv", include_all(pools))
    assert screening.import_sheets([tmp / "sheet.csv"], pool_dir, decision_dir) == 0
    questions, report, short = sample.build(pool_dir, decision_dir)
    assert not short
    assert len(questions) == sum(sum(t.values()) for t in TARGETS.values()) == 240
    assert validate_rows(questions, "final") == []
    assert {q["group_id"] for q in questions if q["domain"] == "acute_triage"} == {f"tri-{i:03d}" for i in range(1, 81)}
    again, _, _ = sample.build(pool_dir, decision_dir)
    assert [(q["group_id"], q["candidate_id"]) for q in again] == [(q["group_id"], q["candidate_id"]) for q in questions]


def test_shortfall_reported(dirs):
    pools, pool_dir, decision_dir, tmp = dirs
    decisions = include_all(pools)
    for d in decisions:
        if d["candidate_id"].startswith("tri-") and int(d["candidate_id"].split("_")[-1]) >= 75:
            d.update(eligibility_status="exclude", eligibility_reason="duplicate")
    write_sheet(tmp / "sheet.csv", decisions)
    assert screening.import_sheets([tmp / "sheet.csv"], pool_dir, decision_dir) == 0
    _, report, short = sample.build(pool_dir, decision_dir)
    assert short
    assert report["acute_triage"]["1B"] == (0, 20)
    assert report["acute_triage"]["0A"] == (25, 20)


def test_import_rejects_bad_decisions_and_writes_nothing(dirs):
    pools, pool_dir, decision_dir, tmp = dirs
    positive_id = next(r["candidate_id"] for r in pools["oncology"] if not r["is_control"])
    bad = [d for d in include_all(pools, premise_for_oncology=False) if d["candidate_id"] == positive_id]
    bad.append({"candidate_id": "onc-nope", "eligibility_status": "include"})
    bad.append({"candidate_id": pools["acute_triage"][0]["candidate_id"], "eligibility_status": "hold"})
    write_sheet(tmp / "sheet.csv", bad)
    assert screening.import_sheets([tmp / "sheet.csv"], pool_dir, decision_dir) == 3
    assert not Path(decision_dir).exists()


def test_import_merges_batches_and_skips_blank_rows(dirs):
    pools, pool_dir, decision_dir, tmp = dirs
    all_decisions = include_all(pools)
    write_sheet(tmp / "a.csv", all_decisions[:10] + [{"candidate_id": all_decisions[10]["candidate_id"]}])
    write_sheet(tmp / "b.csv", all_decisions[5:8])
    assert screening.import_sheets([tmp / "a.csv"], pool_dir, decision_dir) == 0
    assert screening.import_sheets([tmp / "b.csv"], pool_dir, decision_dir) == 0
    assert len(screening.load_decisions("oncology", decision_dir)) == 10


def test_export_prefills_existing_decisions(dirs, monkeypatch):
    pools, pool_dir, decision_dir, tmp = dirs
    write_sheet(tmp / "sheet.csv", include_all(pools))
    screening.import_sheets([tmp / "sheet.csv"], pool_dir, decision_dir)
    paths = screening.export(1.0, pool_dir, tmp / "sheets", decision_dir)
    with open(paths[2], encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 80 and all(r["eligibility_status"] == "include" for r in rows)
