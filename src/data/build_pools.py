"""Build candidate pools from raw data and validate them.

Usage (from repo root):
    python -m src.data.build_pools

Writes data/processed/candidates/{oncology,public_health,acute_triage}.jsonl and prints label counts.
Exits non-zero if any pool fails validation.
"""

import sys
from collections import Counter
from pathlib import Path

from src.data import cancer_myth, care_bench, uphill
from src.data.schema import validate_rows, write_jsonl

OUT_DIR = Path("data/processed/candidates")


def summarize(rows):
    labels = Counter((r["gold_label_mapped"], r["eligibility_status"]) for r in rows)
    return ", ".join(f"{gold}/{status}={n}" for (gold, status), n in sorted(labels.items()))


def main():
    uphill_rows, uphill_stats = uphill.load(return_stats=True)
    pools = {
        "oncology": cancer_myth.load(),
        "public_health": uphill_rows,
        "acute_triage": care_bench.load(),
    }
    failed = False
    for domain, rows in pools.items():
        problems = validate_rows(rows, "candidates")
        if problems:
            failed = True
            print(f"FAIL {domain}: {len(problems)} problem(s), first: line {problems[0][0]}: {problems[0][1]}")
            continue
        path = OUT_DIR / f"{domain}.jsonl"
        write_jsonl(rows, path)
        print(f"OK   {path}: {len(rows)} rows ({summarize(rows)})")
    print(f"     uphill: {uphill_stats}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
