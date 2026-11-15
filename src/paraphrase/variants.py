"""Variant rows (codebase.md §5.2), paths and validation for the paraphrase stage.

``variants.jsonl`` holds exactly the §5.2 fields, so the runner and the scorers can read it without
knowing anything about this stage. Everything else (style, prompt version, attempts, automatic checks,
reviewer decisions) lives in side files keyed by ``(group_id, variant_id, text_sha)``.

Layout (``{set}`` is ``pilot`` or ``full``):

- ``data/processed/paraphrase/{set}/variants.jsonl``: git-ignored (source-derived text)
- ``data/processed/paraphrase/{set}/generation_log.jsonl``: one row per model call, git-ignored
- ``data/processed/paraphrase/{set}/checks.jsonl``: rule flags and judge verdicts, git-ignored
- ``data/processed/paraphrase/{set}/review.csv``: the side-by-side review sheet, git-ignored
- ``data/processed/paraphrase/cache.jsonl``: API response cache shared by both sets, git-ignored
- ``data/decisions/paraphrase/{set}.csv``: reviewer decisions, IDs and hashes only, **committed**
"""

import hashlib
from pathlib import Path

from src.data.schema import read_jsonl, write_jsonl
from src.paraphrase.prompts import STYLES, VARIANT_IDS

VARIANT_FIELDS = ["group_id", "variant_id", "text", "generator", "validity", "validity_reason", "verified_by"]
VALIDITY = {"valid", "invalid", "needs_review"}
SETS = {"pilot": Path("data/processed/pilot.jsonl"), "full": Path("data/processed/questions.jsonl")}
WORK_DIR = Path("data/processed/paraphrase")
DECISION_DIR = Path("data/decisions/paraphrase")


def paths(set_name, work_dir=WORK_DIR, decision_dir=DECISION_DIR):
    d = Path(work_dir) / set_name
    return {
        "dir": d,
        "variants": d / "variants.jsonl",
        "log": d / "generation_log.jsonl",
        "checks": d / "checks.jsonl",
        "review": d / "review.csv",
        "decisions": Path(decision_dir) / f"{set_name}.csv",
    }


def text_sha(text):
    """Short hash identifying the exact text a decision was made on. Safe to commit (no source text)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def make_variant(group_id, variant_id, text, generator, validity="needs_review", validity_reason=None,
                 verified_by=None):
    return {"group_id": group_id, "variant_id": variant_id, "text": text, "generator": generator,
            "validity": validity, "validity_reason": validity_reason, "verified_by": verified_by}


def original_variant(question_row):
    # The original is the source text itself, so it needs no meaning check.
    return make_variant(question_row["group_id"], "original", question_row["original_question"], "source",
                        validity="valid", validity_reason="source question", verified_by="source")


def load_variants(path):
    """``{(group_id, variant_id): row}``, preserving file order."""
    path = Path(path)
    if not path.exists():
        return {}
    return {(r["group_id"], r["variant_id"]): r for r in read_jsonl(path)}


def save_variants(variants, path, group_order=None):
    """Write rows sorted by group (in ``group_order`` if given) and then original, p1, p2, p3."""
    rank = {g: i for i, g in enumerate(group_order or [])}
    vrank = {v: i for i, v in enumerate(VARIANT_IDS)}
    rows = sorted(variants.values(), key=lambda r: (rank.get(r["group_id"], len(rank)), r["group_id"],
                                                     vrank[r["variant_id"]]))
    write_jsonl(rows, path)


FINAL_VARIANTS = Path("data/processed/variants.jsonl")  # codebase.md §5.2, the path the runner reads


def write_final(files, group_ids, out=FINAL_VARIANTS):
    """Write the validated set to the documented ``variants.jsonl``. Refuses unless every group has all four
    variants and every paraphrase is valid, so the runner can never read half-reviewed text."""
    variants = load_variants(files["variants"])
    errors = validate_variants(list(variants.values()), group_ids, require_complete=True)
    if errors:
        raise ValueError(f"not writing {out}: {len(errors)} problems, first: {errors[0]}")
    save_variants(variants, out, group_ids)
    return len(variants)


def validate_variants(rows, group_ids=None, require_complete=False):
    """Return a list of error strings. With ``require_complete``, every group must have all four variants
    and every variant must be ``valid`` (the production gate)."""
    errors = []
    seen = {}
    for i, r in enumerate(rows, start=1):
        if list(r) != VARIANT_FIELDS:
            errors.append(f"line {i}: fields must be exactly {VARIANT_FIELDS}")
            continue
        if r["variant_id"] not in VARIANT_IDS:
            errors.append(f"line {i}: variant_id {r['variant_id']!r} not in {VARIANT_IDS}")
        if r["validity"] not in VALIDITY:
            errors.append(f"line {i}: validity {r['validity']!r} not in {sorted(VALIDITY)}")
        if not isinstance(r["text"], str) or not r["text"].strip():
            errors.append(f"line {i}: text must be a non-empty string")
        if r["validity"] in {"invalid", "needs_review"} and not r["validity_reason"]:
            errors.append(f"line {i}: validity_reason required when validity is {r['validity']!r}")
        if r["validity"] == "valid" and r["variant_id"] != "original":
            reviewers = {x.strip().lower() for x in (r["verified_by"] or "").split(";") if x.strip()}
            if len(reviewers) < 2:
                errors.append(f"line {i}: a valid paraphrase needs two distinct reviewers in verified_by")
        key = (r["group_id"], r["variant_id"])
        if key in seen:
            errors.append(f"line {i}: duplicate {key} (first on line {seen[key]})")
        seen[key] = i
    if require_complete:
        groups = group_ids if group_ids is not None else sorted({g for g, _ in seen})
        for g in groups:
            for v in VARIANT_IDS:
                if (g, v) not in seen:
                    errors.append(f"{g}: missing variant {v}")
        not_valid = [k for k, i in seen.items() if rows[i - 1]["validity"] != "valid"]
        if not_valid:
            errors.append(f"{len(not_valid)} variants are not valid yet, e.g. {not_valid[:3]}")
    return errors


__all__ = ["STYLES", "VARIANT_IDS", "VARIANT_FIELDS", "VALIDITY", "SETS", "paths", "text_sha", "make_variant",
           "original_variant", "load_variants", "save_variants", "validate_variants"]
