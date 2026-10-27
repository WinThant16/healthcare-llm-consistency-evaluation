"""Stratified sampler: candidate pools + screening decisions → the 240-group master dataset.

Usage (from repo root):
    python -m src.data.sample            # report shortfalls, write only if every target is met
    python -m src.data.sample --report   # report only

For each domain and gold label, takes the first ``TARGETS`` rows with ``eligibility_status == include`` in
``priority_order``, then assigns group IDs (``onc-001`` …) in a seeded shuffle so IDs don't reveal labels.

Writes:
- data/processed/questions.jsonl (git-ignored: source text), validated at the ``final`` stage
- data/questions_manifest.csv (committed: IDs and labels only), enough to rebuild the dataset exactly
"""

import argparse
import csv
import hashlib
import sys
from pathlib import Path

from src.data.sampling import SEED, TARGETS, priority_order, secondary_key
from src.data.schema import DOMAINS, validate_rows, write_jsonl
from src.data.screening import DECISION_DIR, POOL_DIR, apply_decisions, load_decisions, load_pool

QUESTIONS = Path("data/processed/questions.jsonl")
MANIFEST = Path("data/questions_manifest.csv")


def select(rows, targets, seed=SEED):
    """Return ``(selected_rows, report)`` where report maps gold → (included_available, target)."""
    ordered = priority_order(rows, seed)
    selected, report = [], {}
    for gold, target in targets.items():
        included = [r for r in ordered.get(gold, []) if r["eligibility_status"] == "include"]
        report[gold] = (len(included), target)
        selected += included[:target]
    return selected, report


def assign_group_ids(selected, domain, seed=SEED):
    shuffled = sorted(selected, key=lambda r: hashlib.sha256(f"{seed}:gid:{r['candidate_id']}".encode()).hexdigest())
    return [{**r, "group_id": f"{DOMAINS[domain]}-{i:03d}"} for i, r in enumerate(shuffled, start=1)]


def build(pool_dir=POOL_DIR, decision_dir=DECISION_DIR, seed=SEED):
    """Return ``(questions, report, short)`` across all domains."""
    questions, report, short = [], {}, False
    for domain, targets in TARGETS.items():
        rows = apply_decisions(load_pool(domain, pool_dir), load_decisions(domain, decision_dir))
        selected, domain_report = select(rows, targets, seed)
        report[domain] = domain_report
        short |= any(have < need for have, need in domain_report.values())
        questions += assign_group_ids(selected, domain, seed)
    return questions, report, short


def write_manifest(questions, path=MANIFEST):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["group_id", "candidate_id", "domain", "task", "gold_label_mapped", "is_control", "stratum"])
        for q in sorted(questions, key=lambda q: q["group_id"]):
            writer.writerow([q["group_id"], q["candidate_id"], q["domain"], q["task"], q["gold_label_mapped"],
                             q["is_control"], secondary_key(q)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", action="store_true", help="print availability only; write nothing")
    args = parser.parse_args(argv)

    questions, report, short = build()
    for domain, gold_report in report.items():
        cells = ", ".join(f"{g}: {have}/{need}{' SHORT' if have < need else ''}" for g, (have, need) in gold_report.items())
        print(f"{domain:14s} included/target  {cells}")
    if args.report:
        return 0
    if short:
        print("Not written: screen more candidates (python -m src.data.screening export --oversample 3).")
        return 1

    problems = validate_rows(questions, "final")
    if problems:
        print(f"FAIL: {len(problems)} problem(s), first: line {problems[0][0]}: {problems[0][1]}")
        return 1
    QUESTIONS.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(sorted(questions, key=lambda q: q["group_id"]), QUESTIONS)
    write_manifest(questions)
    print(f"wrote {QUESTIONS} ({len(questions)} groups) and {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
