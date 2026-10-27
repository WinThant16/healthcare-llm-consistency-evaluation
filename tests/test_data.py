from pathlib import Path

import pytest

from src.data import cancer_myth, care_bench, uphill
from src.data.schema import make_row, validate_row, validate_rows


def positive(**overrides):
    values = dict(
        candidate_id="ph-uphill-1_m",
        domain="public_health",
        task="correction",
        is_control=False,
        source_dataset="uphill",
        source_id="uphill-1_m",
        original_question="There are studies which indicate that X. Could you help me?",
        source_label_raw="false",
        gold_label_mapped="correction_required",
        target_false_premise="X",
        source_evidence="Fact-check says X is false.",
        eligibility_status="pending",
        review_status="pending",
    )
    values.update(overrides)
    return make_row(**values)


def test_valid_candidate_passes():
    assert validate_row(positive()) == []


def test_make_row_rejects_unknown_field():
    with pytest.raises(KeyError):
        make_row(not_a_field=1)


@pytest.mark.parametrize("overrides, fragment", [
    ({"domain": "maternal_infant"}, "domain"),
    ({"task": "escalation"}, "task"),
    ({"gold_label_mapped": "1B"}, "gold_label_mapped"),
    ({"is_control": True}, "is_control must be true exactly"),
    ({"eligibility_status": "hold"}, "eligibility_reason required"),
    ({"group_id": "ph-001"}, "group_id must be null"),
    ({"candidate_id": "onc-uphill-1_m"}, "candidate_id must start"),
    ({"original_question": "  "}, "original_question"),
])
def test_invalid_candidates(overrides, fragment):
    errors = validate_row(positive(**overrides))
    assert any(fragment in e for e in errors), errors


def test_final_stage_rules():
    row = positive(eligibility_status="include", group_id="ph-001")
    assert validate_row(row, "final") == []
    assert any("group_id" in e for e in validate_row(positive(eligibility_status="include"), "final"))
    assert any("eligibility_status" in e for e in validate_row(positive(group_id="ph-001"), "final"))
    missing_premise = positive(eligibility_status="include", group_id="ph-001", target_false_premise=None)
    assert any("target_false_premise" in e for e in validate_row(missing_premise, "final"))


def test_escalation_rules():
    row = positive(candidate_id="tri-x", domain="acute_triage", task="escalation", source_dataset="care_bench",
                   source_id="x", gold_label_mapped="1B", target_false_premise=None)
    assert validate_row(row) == []
    assert validate_row({**row, "target_false_premise": "y"})


def test_duplicates_detected():
    problems = validate_rows([positive(), positive()])
    assert any("duplicate candidate_id" in e for _, e in problems)


def require(path):
    if not Path(path).exists():
        pytest.skip(f"{path} not present (run data/fetch_raw.sh)")


def test_cancer_myth_loader():
    require(cancer_myth.POSITIVES)
    rows = cancer_myth.load()
    assert validate_rows(rows) == []
    assert sum(not r["is_control"] for r in rows) == 585
    controls = [r for r in rows if r["is_control"]]
    assert len(controls) == 150
    assert all(r["source_evidence"].startswith(cancer_myth.NFP_EVIDENCE) for r in controls)


def test_uphill_loader():
    require(uphill.QUERIES)
    rows, stats = uphill.load(return_stats=True)
    assert validate_rows(rows) == []
    assert {r["source_label_raw"] for r in rows} == {"true", "false"}
    assert stats["kept"] == len(rows) == stats["level2_claims"] - stats["dropped_mixture_or_fabricated"] - stats["dropped_duplicates"]


def test_uphill_clean_claim():
    assert uphill.clean_claim("It] is nearly impossible ") == "It] is nearly impossible"
    assert uphill.clean_claim(" “More babies will die”  ") == "More babies will die"


def test_care_bench_loader():
    require(care_bench.PREFIXES)
    rows = care_bench.load()
    assert validate_rows(rows) == []
    counts = {g: sum(r["gold_label_mapped"] == g for r in rows) for g in ("0A", "0B", "1A", "1B")}
    assert counts == {"0A": 245, "0B": 55, "1A": 117, "1B": 22}
