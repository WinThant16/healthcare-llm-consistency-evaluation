"""Side-by-side review sheet for the two-person meaning check (annotation guide §6, §7.1 step 3).

Each paraphrase gets two independent reviews (``reviewer_1`` / ``validity_1`` / ``reason_1`` and the same
for reviewer 2). Fill your own columns without looking at the other reviewer's. Where both agree, that is
the decision. Where they disagree, or either says ``needs_review``, a third person (or both together, after
discussion) fills ``final_validity`` and ``final_reason``.

Rules, enforced on import (any bad row blocks the whole import, so nothing is half-written):

- ``validity_*`` and ``final_validity`` are ``valid``, ``invalid`` or ``needs_review``.
- A filled validity needs the matching reviewer ID; the two reviewer IDs must differ.
- ``invalid`` and ``needs_review`` need a reason.
- ``valid`` needs two distinct reviewers. IDs are stored lowercase (``sahil``, ``win``) and compared
  case-insensitively, like the screening decisions.
- The sheet's ``text_sha`` must match the current paraphrase, so a sheet exported before a regeneration
  can't overwrite the new text's status.

Decisions are stored in ``data/decisions/paraphrase/{set}.csv`` with IDs, hashes and decisions only (no
question or paraphrase text), so that file can be committed. **Don't quote source text in reasons.**
"""

import csv
from pathlib import Path

from src.paraphrase import prompts
from src.paraphrase.pipeline import load_checks, write_csv
from src.paraphrase.variants import STYLES, VALIDITY, VARIANT_IDS, load_variants, save_variants, text_sha

REVIEWER_FIELDS = ["reviewer_1", "validity_1", "reason_1", "reviewer_2", "validity_2", "reason_2",
                   "final_validity", "final_reason"]
SHEET_FIELDS = ["group_id", "variant_id", "style", "task_kind", "gold_label", "target_false_premise",
                "original_question", "paraphrase", "text_sha", "rule_flags", "judge_verdict", "judge_issues",
                "judge_explanation", *REVIEWER_FIELDS]
DECISION_FIELDS = ["group_id", "variant_id", "text_sha", *[f for f in REVIEWER_FIELDS],
                   "effective_validity", "effective_reason"]


def load_decisions(path):
    """``{(group_id, variant_id, text_sha): row}``."""
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {(r["group_id"], r["variant_id"], r["text_sha"]): r for r in csv.DictReader(f)}


def export_sheet(questions, files, include_decided=False, force=False):
    """Write ``review.csv`` with every paraphrase that has no final decision for its current text."""
    out = Path(files["review"])
    if out.exists() and not force:
        raise FileExistsError(f"{out} exists and may hold unsaved reviews. Import it first, then re-export "
                              "with --force.")
    variants = load_variants(files["variants"])
    check_rows = load_checks(files["checks"])
    decisions = load_decisions(files["decisions"])
    rows = []
    for q in questions:
        g = q["group_id"]
        for vid in STYLES:
            v = variants.get((g, vid))
            if v is None:
                continue
            sha = text_sha(v["text"])
            d = decisions.get((g, vid, sha), {})
            if d.get("effective_validity") in {"valid", "invalid"} and not include_decided:
                continue
            c = check_rows.get((g, vid, sha), {})
            rows.append({
                "group_id": g, "variant_id": vid, "style": STYLES[vid], "task_kind": prompts.task_kind(q),
                "gold_label": q["gold_label_mapped"], "target_false_premise": q.get("target_false_premise") or "",
                "original_question": q["original_question"], "paraphrase": v["text"], "text_sha": sha,
                "rule_flags": ";".join(c.get("rule_flags", [])) if c else "not checked",
                "judge_verdict": c.get("judge_verdict") or "", "judge_issues": ";".join(c.get("judge_issues") or []),
                "judge_explanation": c.get("judge_explanation") or "",
                **{f: d.get(f, "") for f in REVIEWER_FIELDS},
            })
    write_csv(out, SHEET_FIELDS, rows)
    return len(rows)


def _effective(r):
    """Return ``(validity, reason, verified_by)`` for one filled sheet row, or raise ValueError."""
    v1, v2, fv = r["validity_1"], r["validity_2"], r["final_validity"]
    rev1, rev2 = r["reviewer_1"].strip().lower(), r["reviewer_2"].strip().lower()  # IDs are lowercase, like `win`
    for v, rev, reason, n in ((v1, rev1, r["reason_1"], 1), (v2, rev2, r["reason_2"], 2)):
        if v and v not in VALIDITY:
            raise ValueError(f"validity_{n} {v!r} not in {sorted(VALIDITY)}")
        if v and not rev:
            raise ValueError(f"validity_{n} is filled but reviewer_{n} is empty")
        if v in {"invalid", "needs_review"} and not reason:
            raise ValueError(f"reason_{n} required when validity_{n} is {v!r}")
    if rev1 and rev2 and rev1 == rev2:
        raise ValueError("reviewer_1 and reviewer_2 must be different people")
    reviewers = ";".join(x for x in (rev1, rev2) if x)
    if fv:
        if fv not in VALIDITY:
            raise ValueError(f"final_validity {fv!r} not in {sorted(VALIDITY)}")
        if fv in {"invalid", "needs_review"} and not r["final_reason"]:
            raise ValueError(f"final_reason required when final_validity is {fv!r}")
        if fv == "valid" and not (v1 and v2):
            raise ValueError("final_validity 'valid' needs both independent reviews filled first")
        return fv, r["final_reason"] or (r["reason_1"] or r["reason_2"] or None), reviewers
    if v1 and v2:
        if v1 == v2:
            return v1, (r["reason_1"] or r["reason_2"] or None), reviewers
        return "needs_review", f"reviewers disagree ({v1} vs {v2}); fill final_validity", reviewers
    return "needs_review", "awaiting second reviewer", reviewers


def apply_decisions(questions, files):
    """Re-apply stored decisions to ``variants.jsonl`` wherever the text hash still matches (for example
    after ``variants.jsonl`` was rebuilt from the cache). Returns the number of variants updated."""
    variants = load_variants(files["variants"])
    decisions = load_decisions(files["decisions"])
    n = 0
    for (g, vid), v in variants.items():
        d = decisions.get((g, vid, text_sha(v["text"])))
        if d is None:
            continue
        reviewers = ";".join(x.strip().lower() for x in (d["reviewer_1"], d["reviewer_2"]) if x) or None
        new = {"validity": d["effective_validity"], "validity_reason": d["effective_reason"] or None,
               "verified_by": reviewers}
        if any(v[k] != val for k, val in new.items()):
            v.update(new)
            n += 1
    if n:
        save_variants(variants, files["variants"], [q["group_id"] for q in questions])
    return n


def import_sheets(sheet_paths, questions, files, log=print):
    variants = load_variants(files["variants"])
    decisions = load_decisions(files["decisions"])
    updates, errors = [], []
    for path in sheet_paths:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for i, r in enumerate(csv.DictReader(f), start=2):
                r = {k: (v or "").strip() for k, v in r.items()}
                for k in ("reviewer_1", "reviewer_2"):
                    r[k] = r.get(k, "").lower()  # stored lowercase, like the screening decisions (`win`)
                if not any(r.get(k) for k in ("validity_1", "validity_2", "final_validity")):
                    continue  # nothing filled in yet
                where = f"{path}:{i} {r.get('group_id')}/{r.get('variant_id')}"
                key = (r.get("group_id"), r.get("variant_id"))
                if key not in variants or key[1] not in STYLES:
                    errors.append(f"{where}: no such paraphrase")
                    continue
                if text_sha(variants[key]["text"]) != r.get("text_sha"):
                    errors.append(f"{where}: text_sha doesn't match the current paraphrase (stale sheet?)")
                    continue
                missing = [k for k in REVIEWER_FIELDS if k not in r]
                if missing:
                    errors.append(f"{where}: sheet is missing columns {missing}")
                    continue
                try:
                    updates.append((key, r, _effective(r)))
                except ValueError as e:
                    errors.append(f"{where}: {e}")
    if errors:
        for e in errors:
            log(f"  ERROR {e}")
        raise ValueError(f"{len(errors)} invalid rows; nothing was imported")
    for (g, vid), r, (validity, reason, reviewers) in updates:
        decisions[(g, vid, r["text_sha"])] = {"group_id": g, "variant_id": vid, "text_sha": r["text_sha"],
                                              **{k: r[k] for k in REVIEWER_FIELDS},
                                              "effective_validity": validity, "effective_reason": reason or ""}
        variants[(g, vid)].update(validity=validity, validity_reason=reason, verified_by=reviewers or None)
    rank = {v: i for i, v in enumerate(VARIANT_IDS)}
    write_csv(files["decisions"], DECISION_FIELDS,
              sorted(decisions.values(), key=lambda d: (d["group_id"], rank[d["variant_id"]], d["text_sha"])),
              committed=True)
    save_variants(variants, files["variants"], [q["group_id"] for q in questions])
    counts = {}
    for _, _, (validity, _, _) in updates:
        counts[validity] = counts.get(validity, 0) + 1
    log(f"  imported {len(updates)} reviewed rows: {counts}")
    return counts
