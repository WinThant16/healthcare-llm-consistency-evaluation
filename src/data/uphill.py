"""UPHILL loader → public_health correction candidates (protocol v1.1 amendment §1).

Keeps Level 2 (``Unequivocal Presupposition``) queries for fact-checked claims only:
``false`` → positive (``correction_required``), ``true`` → control (``no_correction``).
``mixture`` and ``fabricated`` claims are dropped. Exact-duplicate claims keep the first occurrence.

Screening caveats (data/README.md): many claims are news/policy rather than personal health, and some
PubHealth labels rate the news story rather than the claim. Rows start ``pending`` for manual screening.
"""

import re

import pandas as pd

from src.data.schema import candidate_id, make_row

QUERIES = "data/raw/uphill/queries.csv"
LEVEL = "Unequivocal Presupposition"
GOLD = {"false": "correction_required", "true": "no_correction"}


def clean_claim(text):
    """Strip scraping debris (stray quotes/brackets, whitespace) from a claim."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text.strip("\"'“”‘’[] ").rstrip("“”\"").strip()


def _text(value):
    return "" if pd.isna(value) else str(value).strip()


def load(path=QUERIES, return_stats=False):
    df = pd.read_csv(path, dtype=str)
    level = df[df.presupposition_level == LEVEL]
    kept = level[level.claim_veracity.isin(GOLD)].copy()
    kept["claim_key"] = kept.claim.map(lambda c: clean_claim(c).lower())
    deduped = kept.drop_duplicates("claim_key", keep="first")

    rows = []
    for r in deduped.itertuples(index=False):
        source_id = f"uphill-{r.claim_id}"
        gold = GOLD[r.claim_veracity]
        evidence = _text(r.veracity_explanation) or _text(r.main_text) or f"{r.source_db} fact-check label: {r.claim_veracity}"
        rows.append(make_row(
            candidate_id=candidate_id("public_health", source_id),
            domain="public_health",
            task="correction",
            is_control=gold == "no_correction",
            source_dataset="uphill",
            source_id=source_id,
            original_question=r.query_with_presupposition.strip(),
            source_label_raw=r.claim_veracity,
            gold_label_mapped=gold,
            target_false_premise=clean_claim(r.claim) if gold == "correction_required" else None,
            source_evidence=evidence,
            eligibility_status="pending",
            review_status="pending",
            source_meta={
                "claim": clean_claim(r.claim),
                "source_db": r.source_db,
                "subjects": _text(r.subjects),
                "date_published": _text(r.date_published),
                "fact_checkers": _text(r.fact_checkers),
                "sources": _text(r.sources),
            },
        ))
    if return_stats:
        stats = {
            "level2_claims": len(level),
            "dropped_mixture_or_fabricated": len(level) - len(kept),
            "dropped_duplicates": len(kept) - len(deduped),
            "kept": len(rows),
        }
        return rows, stats
    return rows
