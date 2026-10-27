"""Eligibility screening round trip.

1. Export review sheets (git-ignored; they contain source text):
       python -m src.data.screening export [--oversample 2.0]
   writes data/processed/screening/{domain}.csv: the top-priority candidates per gold label
   (target × oversample), with any existing decisions prefilled.
2. Reviewers fill ``eligibility_status`` (include / exclude / hold), ``eligibility_reason``, ``reviewed_by``,
   ``review_status`` and, for positive correction items, ``target_false_premise``.
3. Import the filled sheet(s):
       python -m src.data.screening import path/to/oncology.csv [...]
   Validated decisions are merged into data/decisions/{domain}.csv, which holds IDs and decisions only and
   IS committed. Rows with an empty ``eligibility_status`` are skipped as not yet reviewed.
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

from src.data.sampling import TARGETS, priority_order, secondary_key
from src.data.schema import ELIGIBILITY, REVIEW_STATUS, read_jsonl

POOL_DIR = Path("data/processed/candidates")
SHEET_DIR = Path("data/processed/screening")
DECISION_DIR = Path("data/decisions")

DECISION_FIELDS = ["candidate_id", "eligibility_status", "eligibility_reason", "target_false_premise",
                   "reviewed_by", "review_status"]
SHEET_FIELDS = ["rank", "candidate_id", "gold_label_mapped", "stratum", "original_question", "source_evidence",
                "source_meta"] + DECISION_FIELDS[1:]


def load_pool(domain, pool_dir=POOL_DIR):
    path = Path(pool_dir) / f"{domain}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; run python -m src.data.build_pools")
    return read_jsonl(path)


def load_decisions(domain, decision_dir=DECISION_DIR):
    path = Path(decision_dir) / f"{domain}.csv"
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {d["candidate_id"]: d for d in csv.DictReader(f)}


def apply_decisions(rows, decisions):
    """Return copies of ``rows`` with decision fields overlaid. Empty decision cells keep pool values."""
    out = []
    for row in rows:
        d = decisions.get(row["candidate_id"])
        if d:
            row = dict(row)
            for field in DECISION_FIELDS[1:]:
                if field == "reviewed_by":
                    continue
                if d.get(field):
                    row[field] = d[field]
        out.append(row)
    return out


def check_decision(d, row):
    """Return errors for one decision against its pool row."""
    errors = []
    status = d.get("eligibility_status", "")
    if status not in ELIGIBILITY:
        errors.append(f"eligibility_status {status!r} not in {sorted(ELIGIBILITY)}")
    if status in {"exclude", "hold"} and not d.get("eligibility_reason"):
        errors.append(f"eligibility_reason required for {status!r}")
    if d.get("review_status") and d["review_status"] not in REVIEW_STATUS:
        errors.append(f"review_status {d['review_status']!r} not in {sorted(REVIEW_STATUS)}")
    if status == "include" and row["gold_label_mapped"] == "correction_required":
        if not (d.get("target_false_premise") or row["target_false_premise"]):
            errors.append("target_false_premise required to include a positive correction item")
    if row["is_control"] and d.get("target_false_premise"):
        errors.append("controls must not have a target_false_premise")
    return errors


def export(oversample=2.0, pool_dir=POOL_DIR, sheet_dir=SHEET_DIR, decision_dir=DECISION_DIR):
    paths = []
    for domain, targets in TARGETS.items():
        decisions = load_decisions(domain, decision_dir)
        ordered = priority_order(apply_decisions(load_pool(domain, pool_dir), decisions))
        sheet = []
        for gold, target in targets.items():
            for rank, row in enumerate(ordered.get(gold, [])[: math.ceil(target * oversample)], start=1):
                d = decisions.get(row["candidate_id"], {})
                sheet.append({
                    "rank": rank,
                    "candidate_id": row["candidate_id"],
                    "gold_label_mapped": row["gold_label_mapped"],
                    "stratum": secondary_key(row),
                    "original_question": row["original_question"],
                    "source_evidence": row["source_evidence"],
                    "source_meta": json.dumps(row["source_meta"], ensure_ascii=False),
                    "eligibility_status": d.get("eligibility_status", ""),
                    "eligibility_reason": d.get("eligibility_reason") or row["eligibility_reason"] or "",
                    "target_false_premise": row["target_false_premise"] or "",
                    "reviewed_by": d.get("reviewed_by", ""),
                    "review_status": d.get("review_status", ""),
                })
        path = Path(sheet_dir) / f"{domain}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=SHEET_FIELDS)
            writer.writeheader()
            writer.writerows(sheet)
        print(f"wrote {path}: {len(sheet)} rows")
        paths.append(path)
    return paths


def import_sheets(sheet_paths, pool_dir=POOL_DIR, decision_dir=DECISION_DIR):
    """Validate filled sheets and merge them into data/decisions/. Returns the number of errors."""
    pools = {domain: {r["candidate_id"]: r for r in load_pool(domain, pool_dir)} for domain in TARGETS}
    incoming = {domain: {} for domain in TARGETS}
    errors = []
    for sheet_path in sheet_paths:
        with open(sheet_path, newline="", encoding="utf-8-sig") as f:
            for line, d in enumerate(csv.DictReader(f), start=2):
                d = {k: (v or "").strip() for k, v in d.items() if k}
                if not d.get("eligibility_status"):
                    continue
                cid = d.get("candidate_id", "")
                domain = next((dom for dom, pool in pools.items() if cid in pool), None)
                if domain is None:
                    errors.append(f"{sheet_path}:{line}: unknown candidate_id {cid!r}")
                    continue
                row_errors = check_decision(d, pools[domain][cid])
                errors += [f"{sheet_path}:{line} {cid}: {e}" for e in row_errors]
                if not row_errors:
                    incoming[domain][cid] = {k: d.get(k, "") for k in DECISION_FIELDS}

    if errors:
        print(f"{len(errors)} error(s); nothing written:")
        for e in errors:
            print("  " + e)
        return len(errors)

    for domain, new in incoming.items():
        if not new:
            continue
        merged = load_decisions(domain, decision_dir)
        merged.update(new)
        path = Path(decision_dir) / f"{domain}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        order = {cid: i for i, cid in enumerate(pools[domain])}
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=DECISION_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(merged.values(), key=lambda d: order[d["candidate_id"]]))
        print(f"updated {path}: {len(new)} new/changed, {len(merged)} total")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="write review sheets to data/processed/screening/")
    exp.add_argument("--oversample", type=float, default=2.0, help="sheet rows per gold label = target × this")
    imp = sub.add_parser("import", help="merge filled sheets into data/decisions/")
    imp.add_argument("sheets", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "export":
        export(args.oversample)
        return 0
    return 1 if import_sheets(args.sheets) else 0


if __name__ == "__main__":
    sys.exit(main())
