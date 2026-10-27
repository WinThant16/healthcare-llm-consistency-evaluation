"""Advisory flags shown on screening sheets to speed up eligibility review.

Flags never change a decision. They point reviewers at likely exclusions under the protocol's selection
rules (patient-facing, no identifiers, no required images, sufficient context, no duplicates) and at the
PubHealth caveat that some labels rate the news story rather than the claim (data/README.md).
"""

import re
from collections import Counter

NEWS_POLICY = re.compile(
    r"\b(video|facebook|tweet|viral|president|trump|obama|biden|clinton|senator|congress|governor|government|"
    r"lawmakers?|bill|law|court|county|shutdown|planned parenthood|campaign|lawsuit|insurers?|obamacare|"
    r"medicaid|medicare|gop)\b", re.I)
STORY_REVIEW = re.compile(r"\b(the story|this story|news release|journalis\w*|reporter|the article|headline)\b", re.I)
IMAGE_REF = re.compile(r"\b(photos?|pictures?|pics?|images?|attached|attachment|see (the )?(above|below))\b", re.I)
IDENTIFIER = re.compile(r"[\w.+-]+@[\w-]+\.\w+|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b|https?://", re.I)
MIN_WORDS = 8


def _norm(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


def flags_for(rows):
    """Return ``{candidate_id: [flag, ...]}`` for a pool of candidate rows."""
    dup_counts = Counter(_norm(r["original_question"]) for r in rows)
    golds = {}
    for r in rows:
        golds.setdefault(_norm(r["original_question"]), set()).add(r["gold_label_mapped"])
    out = {}
    for r in rows:
        question = r["original_question"]
        meta = r["source_meta"] or {}
        flags = []
        if r["domain"] == "public_health":
            if NEWS_POLICY.search(meta.get("claim", "")):
                flags.append("news_or_policy_claim")
            if meta.get("source_db") == "pubhealth" and STORY_REVIEW.search(r["source_evidence"]):
                flags.append("label_may_rate_story_not_claim")
        if IMAGE_REF.search(question):
            flags.append("mentions_image")
        if IDENTIFIER.search(question):
            flags.append("possible_identifier")
        if len(question.split()) < MIN_WORDS:
            flags.append("very_short")
        if len(golds[_norm(question)]) > 1:
            flags.append("duplicate_conflicting_labels")
        elif dup_counts[_norm(question)] > 1:
            flags.append("duplicate_text")
        out[r["candidate_id"]] = flags
    return out
