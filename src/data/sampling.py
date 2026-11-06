"""Sampling targets and the deterministic priority order shared by screening and sampling.

Every candidate gets a rank within its gold-label stratum. Ranks interleave secondary strata (myth category,
fact-check source, CARE-Bench source) so that the top of each list is already balanced. Screening reviews the
top-ranked rows first; the sampler then takes the first ``target`` *included* rows in the same order. With a
fixed seed and the committed decisions, the final selection is reproducible.
"""

import hashlib
from collections import defaultdict

SEED = "protocol-v1.1"

# Protocol v1.0 / v1.1 amendment §1: 240 groups.
TARGETS = {
    "oncology": {"correction_required": 60, "no_correction": 20},
    "public_health": {"correction_required": 60, "no_correction": 20},
    "acute_triage": {"0A": 20, "0B": 20, "1A": 20, "1B": 20},
}


def secondary_key(row):
    """Sub-stratum used to spread picks within a gold label."""
    domain = row["domain"]
    meta = row["source_meta"] or {}
    if domain == "oncology":
        raw = row["source_label_raw"]
        return raw.get("category") if isinstance(raw, dict) and "category" in raw else meta.get("cancer")
    if domain == "public_health":
        return meta.get("source_db")
    return meta.get("source_dataset")


def _hash(seed, text):
    return hashlib.sha256(f"{seed}:{text}".encode()).hexdigest()


def priority_order(rows, seed=SEED):
    """Return ``{gold_label: [row, ...]}`` in deterministic, sub-stratum-interleaved order."""
    by_gold = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_gold[row["gold_label_mapped"]][str(secondary_key(row))].append(row)

    ordered = {}
    for gold, groups in by_gold.items():
        queues = [sorted(g, key=lambda r: _hash(seed, r["candidate_id"])) for g in groups.values()]
        queues.sort(key=lambda q: _hash(seed, q[0]["candidate_id"]))
        out = []
        while any(queues):
            for q in queues:
                if q:
                    out.append(q.pop(0))
        ordered[gold] = out
    return ordered
