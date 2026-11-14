"""Generate paraphrases, regenerate rejected ones, and run the automatic meaning checks."""

import csv
import json
import time
from pathlib import Path

from src.data.schema import read_jsonl
from src.paraphrase import checks, prompts
from src.paraphrase.llm import parse_json_object
from src.paraphrase.variants import (STYLES, load_variants, make_variant, original_variant, save_variants,
                                     text_sha)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append(path, row):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_questions(path):
    rows = read_jsonl(path)
    missing = [r.get("candidate_id") for r in rows if not r.get("group_id")]
    if missing:
        raise ValueError(f"{path}: rows without group_id (run src.data.sample first): {missing[:3]}")
    return rows


def _log_row(question, variant_ids, attempt, result, client, error=None, reason=None):
    return {"group_id": question["group_id"], "variant_ids": list(variant_ids), "attempt": attempt,
            "prompt_version": prompts.GENERATE_VERSION, "styles": {v: STYLES[v] for v in variant_ids},
            "settings": client.settings, "model_version": (result or {}).get("model_version"),
            "tokens_in": (result or {}).get("tokens_in"), "tokens_out": (result or {}).get("tokens_out"),
            "latency_s": (result or {}).get("latency_s"), "cached": (result or {}).get("cached"),
            "stop_reason": (result or {}).get("stop_reason"), "redo_reason": reason, "error": error,
            "timestamp": _now()}


def _ask(client, system, user, expected_keys, tag):
    """Call the model and parse the JSON reply. One extra attempt (new cache key) on a malformed reply."""
    last_error = None
    for n in range(2):
        result = client.complete(system, user, tag=f"{tag}|parse{n}" if n else tag)
        try:
            obj = parse_json_object(result["text"])
            out = {k: obj.get(k) for k in expected_keys}
            bad = [k for k, v in out.items() if not isinstance(v, str) or not v.strip()]
            if bad:
                raise ValueError(f"missing or empty keys {bad}")
            return {k: v.strip() for k, v in out.items()}, result, None
        except ValueError as e:
            last_error = f"unparseable reply: {e}"
    return None, result, last_error


def generate(questions, files, client, prompt_dir=prompts.PROMPT_DIR, limit=None, log=print):
    """Write the original plus p1–p3 for every group that doesn't have them yet. Safe to re-run."""
    sections = prompts.load_sections(prompts.GENERATE_VERSION, prompt_dir)
    variants = load_variants(files["variants"])
    order = [q["group_id"] for q in questions]
    done = failed = 0
    for q in questions:
        g = q["group_id"]
        variants.setdefault((g, "original"), original_variant(q))
        if all((g, v) in variants for v in STYLES):
            continue
        if limit is not None and done + failed >= limit:
            break
        system, user = prompts.generation_messages(q, sections)
        out, result, error = _ask(client, system, user, list(STYLES), tag="generate")
        _append(files["log"], _log_row(q, STYLES, 1, result, client, error))
        if out is None:
            failed += 1
            log(f"  {g}: FAILED ({error})")
            continue
        generator = result.get("model_version") or client.model
        for vid, text in out.items():
            variants[(g, vid)] = make_variant(g, vid, text, generator, validity_reason="not yet reviewed")
        save_variants(variants, files["variants"], order)  # save after every group, so a crash loses nothing
        done += 1
        log(f"  {g}: ok{' (cached)' if result.get('cached') else ''}")
    save_variants(variants, files["variants"], order)
    return {"generated": done, "failed": failed}


def _attempts(log_path):
    """``{(group_id, variant_id): number of generation calls so far}`` from the generation log."""
    counts = {}
    if Path(log_path).exists():
        for r in read_jsonl(log_path):
            if r.get("error"):
                continue
            for v in r["variant_ids"]:
                counts[(r["group_id"], v)] = counts.get((r["group_id"], v), 0) + 1
    return counts


def redo(questions, files, client, prompt_dir=prompts.PROMPT_DIR, log=print):
    """Regenerate every paraphrase currently marked ``invalid``, giving the model the reviewer's reason.

    The new text gets ``needs_review`` and goes back through checks and two-person review. The old
    decision stays in the decisions file, tied to the old text's hash.
    """
    sections = prompts.load_sections(prompts.GENERATE_VERSION, prompt_dir)
    variants = load_variants(files["variants"])
    by_group = {q["group_id"]: q for q in questions}
    attempts = _attempts(files["log"])
    redone = failed = 0
    for (g, vid), row in list(variants.items()):
        if vid == "original" or row["validity"] != "invalid":
            continue
        n = attempts.get((g, vid), 1) + 1
        reason = row["validity_reason"]
        system, user = prompts.generation_messages(by_group[g], sections,
                                                   redo={"variant_id": vid, "rejected": row["text"], "reason": reason})
        out, result, error = _ask(client, system, user, [vid], tag=f"redo{n}")
        _append(files["log"], _log_row(by_group[g], [vid], n, result, client, error, reason))
        if out is None:
            failed += 1
            log(f"  {g}/{vid}: FAILED ({error})")
            continue
        variants[(g, vid)] = make_variant(g, vid, out[vid], result.get("model_version") or client.model,
                                          validity_reason=f"regenerated (attempt {n}) after invalid")
        redone += 1
        log(f"  {g}/{vid}: regenerated (attempt {n})")
    save_variants(variants, files["variants"], [q["group_id"] for q in questions])
    return {"regenerated": redone, "failed": failed}


def load_checks(path):
    """``{(group_id, variant_id, text_sha): row}``."""
    if not Path(path).exists():
        return {}
    return {(r["group_id"], r["variant_id"], r["text_sha"]): r for r in read_jsonl(path)}


def run_checks(questions, files, judge=None, prompt_dir=prompts.PROMPT_DIR, log=print):
    """Rule flags for every paraphrase, plus a judge verdict if ``judge`` (an LLMClient) is given.

    Results are keyed by text hash, so only new or regenerated text is checked again.
    """
    sections = prompts.load_sections(prompts.JUDGE_VERSION, prompt_dir) if judge else None
    variants = load_variants(files["variants"])
    existing = load_checks(files["checks"])
    by_group = {q["group_id"]: q for q in questions}
    new = 0
    for (g, vid), row in variants.items():
        if vid == "original" or g not in by_group:
            continue
        sha = text_sha(row["text"])
        prev = existing.get((g, vid, sha))
        if prev and (judge is None or prev.get("judge_verdict") is not None):
            continue
        q = by_group[g]
        kind = prompts.task_kind(q)
        others = [variants[(g, o)]["text"] for o in STYLES if o != vid and (g, o) in variants]
        out = {"group_id": g, "variant_id": vid, "text_sha": sha,
               "rule_flags": checks.check_pair(q["original_question"], row["text"], kind, others),
               "judge_version": None, "judge_model": None, "judge_verdict": None, "judge_issues": [],
               "judge_explanation": None, "judge_error": None, "timestamp": _now()}
        if judge is not None:
            system, user = prompts.judge_messages(q, row["text"], sections)
            result = judge.complete(system, user, tag="judge")
            out.update(judge_version=prompts.JUDGE_VERSION, judge_model=result.get("model_version") or judge.model)
            try:
                obj = parse_json_object(result["text"])
                verdict = obj.get("verdict")
                out.update(judge_verdict=verdict if verdict in {"same", "different", "unsure"} else "unsure",
                           judge_issues=[str(x) for x in obj.get("issues") or []],
                           judge_explanation=obj.get("explanation"))
            except ValueError as e:
                out.update(judge_verdict="unsure", judge_error=str(e))
        _append(files["checks"], out)
        existing[(g, vid, sha)] = out
        new += 1
    flagged = sum(1 for (g, v), row in variants.items() if v != "original"
                  and (c := existing.get((g, v, text_sha(row["text"]))))
                  and (c["rule_flags"] or c["judge_verdict"] in {"different", "unsure"}))
    log(f"  checked {new} new paraphrases; {flagged} currently flagged for a closer look")
    return {"checked": new, "flagged": flagged}


def write_csv(path, fieldnames, rows, committed=False):
    """Write a CSV. Files that go into git (``committed=True``) are plain UTF-8 with ``\\n`` line endings, so a
    re-import on any operating system leaves them unchanged. Other sheets keep a BOM and ``\\r\\n`` so
    Excel opens them correctly."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8" if committed else "utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n" if committed else "\r\n")
        w.writeheader()
        w.writerows(rows)
