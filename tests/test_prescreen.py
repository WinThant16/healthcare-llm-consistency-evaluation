from src.data.prescreen import flags_for
from src.data.schema import make_row


def row(cid, question, domain="acute_triage", gold="1A", meta=None, evidence="grounding"):
    return make_row(candidate_id=cid, domain=domain, original_question=question, gold_label_mapped=gold,
                    source_meta=meta or {}, source_evidence=evidence)


def test_flags():
    long_q = "I have had a sharp pain in my left side for three days now"
    rows = [
        row("a", long_q + ", see the attached photo"),
        row("b", "Call me at 555-123-4567 about " + long_q),
        row("c", "Chest hurts"),
        row("d", long_q), row("e", long_q.upper()),
        row("f", long_q + " again", gold="1A"), row("g", long_q + " again!", gold="0B"),
        row("h", long_q + " ok", domain="public_health", gold="correction_required",
            meta={"claim": "Obamacare bill cuts Medicaid", "source_db": "pubhealth"},
            evidence="The story failed to quantify benefits."),
    ]
    flags = flags_for(rows)
    assert "mentions_image" in flags["a"]
    assert "possible_identifier" in flags["b"]
    assert flags["c"] == ["very_short"]
    assert flags["d"] == flags["e"] == ["duplicate_text"]
    assert flags["f"] == flags["g"] == ["duplicate_conflicting_labels"]
    assert flags["h"] == ["news_or_policy_claim", "label_may_rate_story_not_claim"]
