"""Eligibility screening round trip.

1. Export review sheets (git-ignored; they contain source text):
       python -m src.data.screening export [--oversample 2.0]
   writes data/processed/screening/{domain}.csv: the top-priority candidates per gold label
   (target × oversample), with any existing decisions prefilled and advisory ``auto_flags``
   (src/data/prescreen.py) that never change a decision.
2. Reviewers fill ``eligibility_status`` (include / exclude / hold), ``eligibility_reason``, ``reviewed_by``,
   ``review_status`` and, for positive correction items, ``target_false_premise``.
3. Import the filled sheet(s):
       python -m src.data.screening import path/to/oncology.csv [...]
   Validated decisions are merged into data/decisions/{domain}.csv, which IS committed. Rows with an empty
   ``eligibility_status`` are skipped as not yet reviewed. To keep source text out of the public repo, a
   premise is stored only when the reviewer wrote or edited it, and never for excluded rows.

Independent second review (protocol: 20–25% double-annotated):
       python -m src.data.screening export --second [--fraction 0.25]   # blind sheets, no first-round decisions
       python -m src.data.screening import --second path/to/second/*.csv
       python -m src.data.screening compare                             # agreement + disagreement log
   Second-round decisions go to data/decisions/second/{domain}.csv and never overwrite first-round decisions.
   ``compare`` writes data/decisions/disagreements.csv for adjudication. The adjudicator imports the final
   decision as a normal (first-round) import with ``review_status=adjudicated``.

Existing decision files can be brought in line with the storage rules with:
       python -m src.data.screening normalize
"""

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

from src.data.prescreen import flags_for
from src.data.sampling import SEED, TARGETS, priority_order, secondary_key
from src.data.schema import ELIGIBILITY, REVIEW_STATUS, read_jsonl

POOL_DIR = Path("data/processed/candidates")
SHEET_DIR = Path("data/processed/screening")
DECISION_DIR = Path("data/decisions")
SECOND = "second"
DISAGREEMENTS = "disagreements.csv"

DECISION_FIELDS = ["candidate_id", "eligibility_status", "eligibility_reason", "target_false_premise",
                   "reviewed_by", "review_status"]
SHEET_FIELDS = ["rank", "candidate_id", "gold_label_mapped", "stratum", "auto_flags", "original_question",
                "source_evidence", "source_meta"] + DECISION_FIELDS[1:]


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


def write_decisions(decisions, domain, pool, decision_dir=DECISION_DIR):
    """Write a domain's decisions in pool order."""
    path = Path(decision_dir) / f"{domain}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    order = {cid: i for i, cid in enumerate(pool)}
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DECISION_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(decisions.values(), key=lambda d: order[d["candidate_id"]]))
    return path


def storable(d, row):
    """The decision as it should be committed: a premise only if the reviewer wrote or edited it.

    Prefilled premises (UPHILL claims) come back from the pool when decisions are applied, so storing them
    only copies source text into the public repo. Premises on excluded rows are never needed.
    """
    out = {k: d.get(k, "") for k in DECISION_FIELDS}
    premise = out["target_false_premise"]
    if out["eligibility_status"] == "exclude" or premise == (row["target_false_premise"] or ""):
        out["target_false_premise"] = ""
    return out


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
    if row["task"] == "escalation" and d.get("target_false_premise"):
        errors.append("escalation items must not have a target_false_premise")
    return errors


def _refuse_overwrite(paths, force):
    existing = [str(p) for p in paths if Path(p).exists()]
    if existing and not force:
        raise FileExistsError(
            "Refusing to overwrite review sheets that may hold unsaved edits: " + ", ".join(existing)
            + ". Import them first, then re-run with --force.")


def second_review_ids(domain, decisions, fraction, seed=SEED):
    """Deterministic, gold-stratified subset of first-round-reviewed candidates for independent review."""
    by_status = {}
    for cid, d in decisions.items():
        by_status.setdefault(d["eligibility_status"], []).append(cid)
    chosen = set()
    for cids in by_status.values():
        ranked = sorted(cids, key=lambda c: hashlib.sha256(f"{seed}:second:{c}".encode()).hexdigest())
        chosen.update(ranked[: math.ceil(len(ranked) * fraction)])
    return chosen


def export(oversample=2.0, pool_dir=POOL_DIR, sheet_dir=SHEET_DIR, decision_dir=DECISION_DIR, force=False,
           second=False, fraction=0.25):
    """Write review sheets. ``second=True`` writes blind sheets for independent review: a stratified
    ``fraction`` of first-round-reviewed candidates, with no first-round decisions or premises shown."""
    sheet_dir = Path(sheet_dir) / SECOND if second else Path(sheet_dir)
    _refuse_overwrite([sheet_dir / f"{domain}.csv" for domain in TARGETS], force)
    paths = []
    for domain, targets in TARGETS.items():
        decisions = load_decisions(domain, decision_dir)
        pool = load_pool(domain, pool_dir)
        if second:
            keep = second_review_ids(domain, decisions, fraction)
            rows, decisions = [r for r in pool if r["candidate_id"] in keep], {}
        else:
            rows = apply_decisions(pool, decisions)
        flags = flags_for(rows)
        ordered = priority_order(rows)
        sheet = []
        for gold, target in targets.items():
            limit = None if second else math.ceil(target * oversample)
            for rank, row in enumerate(ordered.get(gold, [])[:limit], start=1):
                d = decisions.get(row["candidate_id"], {})
                sheet.append({
                    "rank": rank,
                    "candidate_id": row["candidate_id"],
                    "gold_label_mapped": row["gold_label_mapped"],
                    "stratum": secondary_key(row),
                    "auto_flags": ";".join(flags[row["candidate_id"]]),
                    "original_question": row["original_question"],
                    "source_evidence": row["source_evidence"],
                    "source_meta": json.dumps(row["source_meta"], ensure_ascii=False),
                    "eligibility_status": d.get("eligibility_status", ""),
                    "eligibility_reason": d.get("eligibility_reason") or row["eligibility_reason"] or "",
                    "target_false_premise": row["target_false_premise"] or "",
                    "reviewed_by": d.get("reviewed_by", ""),
                    "review_status": d.get("review_status", ""),
                })
        path = sheet_dir / f"{domain}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=SHEET_FIELDS)
            writer.writeheader()
            writer.writerows(sheet)
        print(f"wrote {path}: {len(sheet)} rows")
        paths.append(path)
    return paths


def import_sheets(sheet_paths, pool_dir=POOL_DIR, decision_dir=DECISION_DIR, second=False):
    """Validate filled sheets and merge them into data/decisions/ (or data/decisions/second/).

    Returns the number of errors. Second-round rows need ``reviewed_by``, which must differ from the
    first-round reviewer.
    """
    pools = {domain: {r["candidate_id"]: r for r in load_pool(domain, pool_dir)} for domain in TARGETS}
    first = {domain: load_decisions(domain, decision_dir) for domain in TARGETS} if second else {}
    target_dir = Path(decision_dir) / SECOND if second else Path(decision_dir)
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
                if second:
                    reviewer = d.get("reviewed_by", "")
                    if not reviewer:
                        row_errors.append("reviewed_by required for second review")
                    elif reviewer == first[domain].get(cid, {}).get("reviewed_by"):
                        row_errors.append(f"second reviewer {reviewer!r} also did the first review")
                errors += [f"{sheet_path}:{line} {cid}: {e}" for e in row_errors]
                if not row_errors:
                    incoming[domain][cid] = storable(d, pools[domain][cid])

    if errors:
        print(f"{len(errors)} error(s); nothing written:")
        for e in errors:
            print("  " + e)
        return len(errors)

    for domain, new in incoming.items():
        if not new:
            continue
        merged = load_decisions(domain, target_dir)
        merged.update(new)
        path = write_decisions(merged, domain, pools[domain], target_dir)
        print(f"updated {path}: {len(new)} new/changed, {len(merged)} total")
    return 0


def normalize(pool_dir=POOL_DIR, decision_dir=DECISION_DIR):
    """Rewrite existing decision files under the storage rules. Returns the number of rows changed."""
    changed_total = 0
    for directory in (Path(decision_dir), Path(decision_dir) / SECOND):
        for domain in TARGETS:
            decisions = load_decisions(domain, directory)
            if not decisions:
                continue
            pool = {r["candidate_id"]: r for r in load_pool(domain, pool_dir)}
            out, changed = {}, 0
            for cid, d in decisions.items():
                clean = storable(d, pool[cid])
                if pool[cid]["task"] == "escalation" and clean["target_false_premise"]:
                    print(f"  {cid}: dropped premise on escalation item: {clean['target_false_premise']!r}")
                    clean["target_false_premise"] = ""
                changed += clean != {k: d.get(k, "") for k in DECISION_FIELDS}
                out[cid] = clean
            path = write_decisions(out, domain, pool, directory)
            print(f"normalized {path}: {changed} of {len(out)} rows changed")
            changed_total += changed
    return changed_total


def cohen_kappa(pairs):
    """Cohen's kappa for a list of (label_a, label_b) pairs; None if undefined."""
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def compare(pool_dir=POOL_DIR, decision_dir=DECISION_DIR):
    """Compare first- and second-round decisions; write the disagreement log. Returns per-domain stats."""
    stats, log = {}, []
    for domain in TARGETS:
        first = load_decisions(domain, decision_dir)
        second = load_decisions(domain, Path(decision_dir) / SECOND)
        both = sorted(set(first) & set(second))
        if not both:
            continue
        pool = {r["candidate_id"]: r for r in load_pool(domain, pool_dir)}
        pairs = [(first[c]["eligibility_status"], second[c]["eligibility_status"]) for c in both]
        for cid, (a, b) in zip(both, pairs):
            premise_a = first[cid]["target_false_premise"] or pool[cid]["target_false_premise"] or ""
            premise_b = second[cid]["target_false_premise"] or pool[cid]["target_false_premise"] or ""
            premise_differs = a == b == "include" and premise_a.strip().lower() != premise_b.strip().lower()
            if a != b or premise_differs:
                log.append({
                    "candidate_id": cid, "domain": domain, "gold_label_mapped": pool[cid]["gold_label_mapped"],
                    "reviewer_1": first[cid]["reviewed_by"], "status_1": a, "reason_1": first[cid]["eligibility_reason"],
                    "reviewer_2": second[cid]["reviewed_by"], "status_2": b, "reason_2": second[cid]["eligibility_reason"],
                    "premise_differs": premise_differs, "adjudicated_status": "", "adjudicator": "", "notes": "",
                })
        kappa = cohen_kappa(pairs)
        stats[domain] = {"double_reviewed": len(both), "of_reviewed": len(first),
                         "agreement": sum(a == b for a, b in pairs) / len(pairs),
                         "kappa": None if kappa is None else round(kappa, 3)}
    path = Path(decision_dir) / DISAGREEMENTS
    if stats:
        fields = ["candidate_id", "domain", "gold_label_mapped", "reviewer_1", "status_1", "reason_1",
                  "reviewer_2", "status_2", "reason_2", "premise_differs", "adjudicated_status", "adjudicator",
                  "notes"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(log)
    return stats, log


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="write review sheets to data/processed/screening/")
    exp.add_argument("--oversample", type=float, default=2.0, help="sheet rows per gold label = target × this")
    exp.add_argument("--force", action="store_true", help="overwrite existing sheets (import them first!)")
    exp.add_argument("--second", action="store_true", help="blind sheets for independent second review")
    exp.add_argument("--fraction", type=float, default=0.25, help="share of reviewed rows for second review")
    imp = sub.add_parser("import", help="merge filled sheets into data/decisions/")
    imp.add_argument("sheets", nargs="+")
    imp.add_argument("--second", action="store_true", help="import into data/decisions/second/")
    sub.add_parser("normalize", help="apply premise storage rules to existing decision files")
    sub.add_parser("compare", help="first vs. second review: agreement, kappa, disagreement log")
    args = parser.parse_args(argv)
    if args.command == "export":
        try:
            export(args.oversample, force=args.force, second=args.second, fraction=args.fraction)
        except FileExistsError as e:
            print(e)
            return 1
        return 0
    if args.command == "normalize":
        normalize()
        return 0
    if args.command == "compare":
        stats, log = compare()
        if not stats:
            print("No second-round decisions yet.")
            return 0
        for domain, s in stats.items():
            print(f"{domain:14s} {s['double_reviewed']}/{s['of_reviewed']} double-reviewed, "
                  f"agreement {s['agreement']:.2f}, kappa {s['kappa']}")
        print(f"wrote {DECISION_DIR / DISAGREEMENTS}: {len(log)} disagreement(s) to adjudicate")
        return 0
    return 1 if import_sheets(args.sheets, second=args.second) else 0


if __name__ == "__main__":
    sys.exit(main())
