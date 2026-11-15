import csv
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src.data import screening
from src.data.review_app import Sheets, make_handler
from src.data.schema import write_jsonl
from tests.test_sampling import correction_row, triage_row


@pytest.fixture
def sheets(tmp_path):
    pools = {
        "oncology": [correction_row("oncology", i, i % 4 == 0, f"cat{i % 3}") for i in range(20)],
        "public_health": [correction_row("public_health", i, i % 4 == 0, "monant") for i in range(20)],
        "acute_triage": [triage_row(i, g, "src") for i, g in enumerate(["0A", "0B", "1A", "1B"] * 5)],
    }
    pool_dir, sheet_dir, decision_dir = tmp_path / "pools", tmp_path / "sheets", tmp_path / "decisions"
    for domain, rows in pools.items():
        write_jsonl(rows, pool_dir / f"{domain}.jsonl")
    screening.export(pool_dir=pool_dir, sheet_dir=sheet_dir, decision_dir=decision_dir)
    return Sheets(sheet_dir, "advait", pool_dir), pools, pool_dir, decision_dir


def pick(pools, domain, control):
    return next(r for r in pools[domain] if r["is_control"] == control)


def saved(s, domain, cid):
    with open(s.paths[domain], newline="", encoding="utf-8-sig") as f:
        return next(r for r in csv.DictReader(f) if r["candidate_id"] == cid)


def test_save_writes_row_and_reviewer(sheets):
    s, pools, pool_dir, decision_dir = sheets
    cid = pick(pools, "oncology", False)["candidate_id"]
    assert s.save("oncology", cid, {"eligibility_status": "include", "target_false_premise": "a premise"}) == []
    row = saved(s, "oncology", cid)
    assert (row["eligibility_status"], row["target_false_premise"], row["reviewed_by"]) == \
        ("include", "a premise", "advait")
    assert screening.import_sheets([s.paths["oncology"]], pool_dir=pool_dir, decision_dir=decision_dir) == 0
    assert screening.load_decisions("oncology", decision_dir)[cid]["target_false_premise"] == "a premise"


@pytest.mark.parametrize("domain, control, update, fragment", [
    ("oncology", False, {"eligibility_status": "include"}, "target_false_premise required"),
    ("oncology", True, {"eligibility_status": "include", "target_false_premise": "x"}, "controls must not"),
    ("acute_triage", False, {"eligibility_status": "include", "target_false_premise": "x"}, "escalation items"),
    ("oncology", False, {"eligibility_status": "exclude"}, "eligibility_reason required"),
    ("oncology", False, {"eligibility_status": "maybe"}, "eligibility_status"),
])
def test_save_rejects_rows_import_would_reject(sheets, domain, control, update, fragment):
    s, pools, *_ = sheets
    cid = pick(pools, domain, control)["candidate_id"]
    before = s.paths[domain].read_bytes()
    errors = s.save(domain, cid, update)
    assert any(fragment in e for e in errors), errors
    assert s.paths[domain].read_bytes() == before


def test_clear_row_and_unknown_ids(sheets):
    s, pools, *_ = sheets
    cid = pick(pools, "public_health", False)["candidate_id"]
    assert s.save("public_health", cid, {"eligibility_status": "exclude", "eligibility_reason": "duplicate"}) == []
    assert s.save("public_health", cid, {"target_false_premise": "claim"}) == []
    row = saved(s, "public_health", cid)
    assert (row["eligibility_status"], row["reviewed_by"]) == ("", "")
    assert s.save("public_health", "nope", {}) and s.save("nowhere", cid, {})


def test_rows_carry_pool_facts(sheets):
    s, pools, *_ = sheets
    rows = {r["candidate_id"]: r for r in s.rows("public_health")}
    positive = pick(pools, "public_health", False)
    assert rows[positive["candidate_id"]]["prefilled_premise"] == positive["target_false_premise"]
    assert rows[pick(pools, "public_health", True)["candidate_id"]]["is_control"] is True


def test_http_round_trip(sheets):
    s, pools, *_ = sheets
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(s, "test"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert b"Screening Review" in urllib.request.urlopen(base + "/").read()
        rows = json.loads(urllib.request.urlopen(base + "/api/rows/acute_triage").read())
        cid = rows[0]["candidate_id"]
        body = json.dumps({"domain": "acute_triage", "candidate_id": cid, "eligibility_status": "hold",
                           "eligibility_reason": "disputed_label"}).encode()
        urllib.request.urlopen(urllib.request.Request(base + "/api/save", body, method="POST"))
        assert saved(s, "acute_triage", cid)["eligibility_status"] == "hold"
        bad = json.dumps({"domain": "acute_triage", "candidate_id": cid, "eligibility_status": "exclude"}).encode()
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(urllib.request.Request(base + "/api/save", bad, method="POST"))
        assert err.value.code == 400
    finally:
        server.shutdown()
