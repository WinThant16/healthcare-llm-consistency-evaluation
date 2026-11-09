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
       python -m src.data.screening compare                             # agreement + logs to adjudicate
       python -m src.data.screening adjudicate                          # filled logs → adjudicated decisions
   Second-round decisions go to data/decisions/second/{domain}.csv and never overwrite first-round decisions.
   ``compare`` writes two logs, keeping any adjudication columns already filled in:
   data/decisions/disagreements.csv (eligibility disagreements) and
   data/decisions/premise_differences.csv (both included, premises worded differently: a meaning check).
   ``adjudicate`` turns filled logs into data/decisions/adjudicated/{domain}.csv. First- and second-round
   decisions are never overwritten (annotation guide §7.3). Downstream steps use the effective decision:
   adjudicated, then first round (marked ``independently_reviewed`` when the second round agrees).

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
ADJUDICATED = "adjudicated"
DISAGREEMENTS = "disagreements.csv"
PREMISE_DIFFERENCES = "premise_differences.csv"
UNEDITED = "[unedited source claim]"

ADJUDICATION_FIELDS = ["adjudicated_status", "adjudicated_reason", "adjudicated_premise", "adjudicator", "notes"]
DISAGREEMENT_FIELDS = ["candidate_id", "domain", "gold_label_mapped", "reviewer_1", "status_1", "reason_1",
                       "reviewer_2", "status_2", "reason_2"] + ADJUDICATION_FIELDS
PREMISE_ADJUDICATION_FIELDS = ["same_meaning", "adjudicated_premise", "adjudicator", "notes"]
PREMISE_FIELDS = ["candidate_id", "domain", "reviewer_1", "premise_1", "reviewer_2", "premise_2"] + \
    PREMISE_ADJUDICATION_FIELDS

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


def load_effective_decisions(domain, decision_dir=DECISION_DIR):
    """Decisions downstream steps should use: adjudicated > first round.

    First-round rows whose second review agrees on eligibility get ``review_status=independently_reviewed``
    unless they already carry a status. Files on disk are never changed.
    """
    first = load_decisions(domain, decision_dir)
    second = load_decisions(domain, Path(decision_dir) / SECOND)
    adjudicated = load_decisions(domain, Path(decision_dir) / ADJUDICATED)
    effective = {}
    for cid, d in first.items():
        d = dict(d)
        s = second.get(cid)
        if s and s["eligibility_status"] == d["eligibility_status"] and not d.get("review_status"):
            d["review_status"] = "independently_reviewed"
        effective[cid] = d
    effective.update(adjudicated)
    return effective


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


def second_review_ids(decisions, gold_by_id, fraction, seed=SEED):
    """Deterministic subset of first-round-reviewed candidates for independent review, stratified by
    (first-round status, gold label) so small classes such as triage ``1B`` stay represented."""
    strata = {}
    for cid, d in decisions.items():
        strata.setdefault((d["eligibility_status"], gold_by_id[cid]), []).append(cid)
    chosen = set()
    for cids in strata.values():
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
        pool = load_pool(domain, pool_dir)
        if second:
            gold_by_id = {r["candidate_id"]: r["gold_label_mapped"] for r in pool}
            keep = second_review_ids(load_decisions(domain, decision_dir), gold_by_id, fraction)
            rows, decisions = [r for r in pool if r["candidate_id"] in keep], {}
        else:
            decisions = load_effective_decisions(domain, decision_dir)
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
                if d.get("review_status") == "adjudicated":
                    row_errors.append("use 'screening adjudicate' for adjudicated decisions (keeps the originals)")
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
    for directory in (Path(decision_dir), Path(decision_dir) / SECOND, Path(decision_dir) / ADJUDICATED):
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


def _same_premise(a, b):
    norm = lambda t: " ".join("".join(c for c in t.lower() if c.isalnum() or c.isspace()).split())
    return norm(a) == norm(b)


def _merge_log(path, rows, keep_fields):
    """Carry already-filled ``keep_fields`` over from an existing log so re-running never loses work.

    Old rows that no longer appear but have filled fields are kept, so nothing is silently dropped.
    Returns ``(rows, kept_stale_count)``.
    """
    old = {}
    if Path(path).exists():
        with open(path, newline="", encoding="utf-8") as f:
            old = {r["candidate_id"]: r for r in csv.DictReader(f)}
    current = {r["candidate_id"] for r in rows}
    for row in rows:
        prev = old.get(row["candidate_id"], {})
        for field in keep_fields:
            if prev.get(field):
                row[field] = prev[field]
    stale = [r for cid, r in old.items() if cid not in current and any(r.get(f) for f in keep_fields)]
    return rows + stale, len(stale)


def _write_log(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def compare(pool_dir=POOL_DIR, decision_dir=DECISION_DIR):
    """Compare first- and second-round decisions and update both adjudication logs.

    Returns ``(stats, disagreements, premise_differences)``. Kappa uses raw first- and second-round
    decisions, so adjudication never changes it.
    """
    stats, log, premise_log = {}, [], []
    for domain in TARGETS:
        first = load_decisions(domain, decision_dir)
        second = load_decisions(domain, Path(decision_dir) / SECOND)
        both = sorted(set(first) & set(second))
        if not both:
            continue
        pool = {r["candidate_id"]: r for r in load_pool(domain, pool_dir)}
        pairs = [(first[c]["eligibility_status"], second[c]["eligibility_status"]) for c in both]
        for cid, (a, b) in zip(both, pairs):
            if a != b:
                log.append({
                    "candidate_id": cid, "domain": domain, "gold_label_mapped": pool[cid]["gold_label_mapped"],
                    "reviewer_1": first[cid]["reviewed_by"], "status_1": a, "reason_1": first[cid]["eligibility_reason"],
                    "reviewer_2": second[cid]["reviewed_by"], "status_2": b, "reason_2": second[cid]["eligibility_reason"],
                })
            elif a == "include" and pool[cid]["gold_label_mapped"] == "correction_required":
                stored_a, stored_b = first[cid]["target_false_premise"], second[cid]["target_false_premise"]
                prefill = pool[cid]["target_false_premise"] or ""
                if not _same_premise(stored_a or prefill, stored_b or prefill):
                    premise_log.append({
                        "candidate_id": cid, "domain": domain,
                        "reviewer_1": first[cid]["reviewed_by"], "premise_1": stored_a or UNEDITED,
                        "reviewer_2": second[cid]["reviewed_by"], "premise_2": stored_b or UNEDITED,
                    })
        kappa = cohen_kappa(pairs)
        stats[domain] = {"double_reviewed": len(both), "of_reviewed": len(first),
                         "agreement": sum(a == b for a, b in pairs) / len(pairs),
                         "kappa": None if kappa is None else round(kappa, 3)}
    if stats:
        log, stale = _merge_log(Path(decision_dir) / DISAGREEMENTS, log, ADJUDICATION_FIELDS)
        premise_log, stale_p = _merge_log(Path(decision_dir) / PREMISE_DIFFERENCES, premise_log,
                                          PREMISE_ADJUDICATION_FIELDS)
        if stale or stale_p:
            print(f"kept {stale + stale_p} adjudicated row(s) that no longer differ; review or delete them by hand")
        _write_log(Path(decision_dir) / DISAGREEMENTS, log, DISAGREEMENT_FIELDS)
        _write_log(Path(decision_dir) / PREMISE_DIFFERENCES, premise_log, PREMISE_FIELDS)
    return stats, log, premise_log


def adjudicate(pool_dir=POOL_DIR, decision_dir=DECISION_DIR):
    """Turn filled adjudication logs into data/decisions/adjudicated/{domain}.csv.

    - ``disagreements.csv``: rows with ``adjudicated_status`` become decisions. ``adjudicated_premise``
      defaults to the premise of the reviewer whose status was chosen.
    - ``premise_differences.csv``: ``same_meaning=no`` rows need ``adjudicated_premise``; ``yes`` keeps round 1.
    Returns ``(errors, pending)``; nothing is written if any filled row is invalid.
    """
    decision_dir = Path(decision_dir)
    pools = {domain: {r["candidate_id"]: r for r in load_pool(domain, pool_dir)} for domain in TARGETS}
    rounds = {domain: (load_decisions(domain, decision_dir), load_decisions(domain, decision_dir / SECOND))
              for domain in TARGETS}

    def read(name):
        path = decision_dir / name
        if not path.exists():
            return []
        with open(path, newline="", encoding="utf-8") as f:
            return [{k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]

    out = {domain: {} for domain in TARGETS}
    errors, pending = [], 0

    def add(domain, cid, d, where):
        row_errors = [] if d["reviewed_by"] else ["adjudicator required"]
        row_errors += check_decision(d, pools[domain][cid])
        errors.extend(f"{where} {cid}: {e}" for e in row_errors)
        if not row_errors:
            out[domain][cid] = storable(d, pools[domain][cid])

    for r in read(DISAGREEMENTS):
        status = r.get("adjudicated_status", "")
        if not status:
            pending += 1
            continue
        domain, cid = r["domain"], r["candidate_id"]
        first, second = (rnd.get(cid, {}) for rnd in rounds[domain])
        chosen = first if status == first.get("eligibility_status") else second
        add(domain, cid, {
            "candidate_id": cid, "eligibility_status": status,
            "eligibility_reason": r.get("adjudicated_reason") or chosen.get("eligibility_reason", ""),
            "target_false_premise": r.get("adjudicated_premise") or chosen.get("target_false_premise", ""),
            "reviewed_by": r.get("adjudicator", ""), "review_status": "adjudicated",
        }, DISAGREEMENTS)

    for r in read(PREMISE_DIFFERENCES):
        same = r.get("same_meaning", "").lower()
        if same not in {"yes", "no"}:
            pending += 1
            if same:
                errors.append(f"{PREMISE_DIFFERENCES} {r['candidate_id']}: same_meaning must be yes or no")
            continue
        if same == "yes":
            continue
        domain, cid = r["domain"], r["candidate_id"]
        if not r.get("adjudicated_premise"):
            errors.append(f"{PREMISE_DIFFERENCES} {cid}: adjudicated_premise required when same_meaning=no")
            continue
        first = rounds[domain][0].get(cid, {})
        add(domain, cid, {**{k: first.get(k, "") for k in DECISION_FIELDS},
                          "target_false_premise": r["adjudicated_premise"],
                          "reviewed_by": r.get("adjudicator", ""), "review_status": "adjudicated"},
            PREMISE_DIFFERENCES)

    if errors:
        print(f"{len(errors)} error(s); nothing written:")
        for e in errors:
            print("  " + e)
        return len(errors), pending
    for domain, decisions in out.items():
        if decisions:
            path = write_decisions(decisions, domain, pools[domain], decision_dir / ADJUDICATED)
            print(f"wrote {path}: {len(decisions)} adjudicated")
    return 0, pending


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
    sub.add_parser("compare", help="first vs. second review: agreement, kappa, adjudication logs")
    sub.add_parser("adjudicate", help="filled adjudication logs → data/decisions/adjudicated/")
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
        stats, log, premise_log = compare()
        if not stats:
            print("No second-round decisions yet.")
            return 0
        for domain, s in stats.items():
            print(f"{domain:14s} {s['double_reviewed']}/{s['of_reviewed']} double-reviewed, "
                  f"agreement {s['agreement']:.2f}, kappa {s['kappa']}")
        print(f"wrote {DECISION_DIR / DISAGREEMENTS}: {len(log)} eligibility disagreement(s)")
        print(f"wrote {DECISION_DIR / PREMISE_DIFFERENCES}: {len(premise_log)} premise wording difference(s)")
        return 0
    if args.command == "adjudicate":
        errors, pending = adjudicate()
        print(f"{pending} row(s) still awaiting adjudication")
        return 1 if errors else 0
    return 1 if import_sheets(args.sheets, second=args.second) else 0


if __name__ == "__main__":
    sys.exit(main())
