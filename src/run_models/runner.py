"""Experiment runner: send every variant to every model and save raw responses (codebase.md §5.3).

Usage (from repo root, keys in ``.env``):
    python -m src.run_models.runner --check                                  # verify access to all three models
    python -m src.run_models.runner --input data/processed/pilot.jsonl --run-id run-20261115-pilot
    python -m src.run_models.runner --input data/processed/variants.jsonl --run-id run-YYYYMMDD-full
    ... --models gpt claude    # subset        ... --dry-run    # plan only, no API calls

``--input`` is a variants file (§5.2: ``group_id``, ``variant_id``, ``text``) or a questions file (§5.1), in
which case each ``original_question`` is run as variant ``original``. Variants whose ``validity`` is not
``valid`` are skipped.

Writes to ``outputs/responses/{run_id}/``:
- ``{model_key}.jsonl``  blind response rows: response_id, run_id, group_id, variant_id, response_text, error
                         (Jev rows: response_text null, plus decision, probabilities, confidence)
- ``metadata.jsonl``     model identity, settings, tokens, latency and cost, keyed by response_id
- ``run.json``           settings, prompt hashes and git commit for the run
- ``errors.jsonl``       API failures that outlasted the retries; rerun the same command to fill those gaps

Responses are write-once. A rerun with the same ``run_id`` skips everything already saved, and refuses to
continue if the model settings or prompts changed (start a new ``run_id``).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from src.data.schema import DOMAINS, TASKS, read_jsonl
from src.run_models.config import MODELS, OUTPUT_DIR, VISIBLE_TOKEN_CAP, cost_usd
from src.run_models.prompts import load_prompt

VARIANT_IDS = ("original", "p1", "p2", "p3")
PREFIX_TASK = {prefix: TASKS[domain] for domain, prefix in DOMAINS.items()}


def load_items(path):
    """Return ``[{group_id, variant_id, task, text}]`` from a variants or questions file."""
    items, seen = [], set()
    for i, row in enumerate(read_jsonl(path), start=1):
        group_id = row.get("group_id")
        if not group_id or group_id.split("-")[0] not in PREFIX_TASK:
            raise ValueError(f"{path}:{i}: group_id {group_id!r} has no known domain prefix")
        if "text" in row:
            variant_id, text = row.get("variant_id"), row["text"]
            if row.get("validity", "valid") != "valid":
                continue
        else:
            variant_id, text = "original", row.get("original_question")
        if variant_id not in VARIANT_IDS:
            raise ValueError(f"{path}:{i}: variant_id {variant_id!r} not in {VARIANT_IDS}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{path}:{i}: empty prompt text")
        if (group_id, variant_id) in seen:
            raise ValueError(f"{path}:{i}: duplicate {group_id}/{variant_id}")
        seen.add((group_id, variant_id))
        items.append({"group_id": group_id, "variant_id": variant_id,
                      "task": PREFIX_TASK[group_id.split("-")[0]], "text": text})
    return items


def response_id(run_id, model_key, group_id, variant_id):
    """Opaque and stable: reviewers can't read the model or variant off it."""
    return "r-" + hashlib.sha256(f"{run_id}|{model_key}|{group_id}|{variant_id}".encode()).hexdigest()[:12]


def request_key(model_key, prompt_sha, text):
    """Cache key (codebase.md §6): model, prompt version, params and the exact variant text."""
    spec = MODELS[model_key]
    payload = json.dumps([spec["model"], spec["prompt_version"], prompt_sha, spec["params"], text], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def response_row(rid, run_id, item, result, mode):
    row = {"response_id": rid, "run_id": run_id, "group_id": item["group_id"], "variant_id": item["variant_id"],
           "response_text": result["response_text"], "error": result["error"]}
    if mode == "decision":
        row.update(decision=result["decision"], probabilities=result["probabilities"], confidence=result["confidence"])
    return row


def metadata_row(rid, model_key, key, result, latency_s):
    spec = MODELS[model_key]
    out, reasoning = result["output_tokens"], result["reasoning_tokens"]
    visible = None if spec["mode"] == "decision" or out is None else out - (reasoning or 0)
    return {"response_id": rid, "model_key": model_key, "model_requested": spec["model"],
            "model_version": result["model_version"], "prompt_version": spec["prompt_version"],
            "params": spec["params"], "request_key": key, "stop_reason": result["stop_reason"],
            "input_tokens": result["input_tokens"], "output_tokens": out, "reasoning_tokens": reasoning,
            "visible_output_tokens": visible,
            "over_visible_cap": None if visible is None else visible > VISIBLE_TOKEN_CAP,
            "latency_s": round(latency_s, 3), "cost_usd": cost_usd(model_key, result["input_tokens"], out),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def append_jsonl(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def run(items, run_id, model_keys, clients, out_dir=OUTPUT_DIR, workers=4, log=print):
    """Run every (model, item) not already saved. Returns ``{model_key: (done, skipped, failed)}``."""
    run_dir = Path(out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path, err_path = run_dir / "metadata.jsonl", run_dir / "errors.jsonl"
    saved_keys = {m["response_id"]: m["request_key"] for m in (read_jsonl(meta_path) if meta_path.exists() else [])}

    prompts = {k: load_prompt(k) for k in model_keys}
    plan = []
    for model_key in model_keys:
        for item in items:
            rid = response_id(run_id, model_key, item["group_id"], item["variant_id"])
            key = request_key(model_key, prompts[model_key][1], item["text"])
            if rid in saved_keys and saved_keys[rid] != key:
                raise SystemExit(f"{run_id}: settings, prompt or text changed for {model_key} "
                                 f"{item['group_id']}/{item['variant_id']} since it was saved. Start a new run_id.")
            plan.append((model_key, item, rid, key))

    manifest = run_dir / "run.json"
    if not manifest.exists():
        manifest.write_text(json.dumps({
            "run_id": run_id, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_commit": git_commit(), "visible_token_cap": VISIBLE_TOKEN_CAP,
            "models": {k: {**MODELS[k], "prompt_sha256": prompts[k][1]} for k in model_keys},
        }, indent=2) + "\n", encoding="utf-8")

    lock = threading.Lock()
    counts = {k: [0, 0, 0] for k in model_keys}

    def one(job):
        model_key, item, rid, key = job
        if rid in saved_keys:
            counts[model_key][1] += 1
            return
        start = time.monotonic()
        try:
            result = clients[model_key].call(item["text"], item["task"], prompts[model_key][0])
        except Exception as e:  # after SDK retries; left unsaved so a rerun fills the gap
            with lock:
                counts[model_key][2] += 1
                append_jsonl(err_path, {"response_id": rid, "model_key": model_key, "group_id": item["group_id"],
                                        "variant_id": item["variant_id"], "error": f"{type(e).__name__}: {e}"[:500],
                                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            log(f"  FAIL {model_key} {item['group_id']}/{item['variant_id']}: {type(e).__name__}")
            return
        latency = time.monotonic() - start
        with lock:  # response and metadata rows land together
            append_jsonl(run_dir / f"{model_key}.jsonl", response_row(rid, run_id, item, result, MODELS[model_key]["mode"]))
            append_jsonl(meta_path, metadata_row(rid, model_key, key, result, latency))
            counts[model_key][0] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, plan))
    return {k: tuple(v) for k, v in counts.items()}


def load_env(path=".env"):
    try:
        from dotenv import load_dotenv
        load_dotenv(path)
    except ImportError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", help="variants.jsonl (§5.2) or a questions file (§5.1)")
    parser.add_argument("--run-id", help="run-YYYYMMDD-tag")
    parser.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--check", action="store_true", help="verify model access and print returned model IDs")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; make no API calls")
    args = parser.parse_args(argv)

    load_env()
    missing = [MODELS[k]["env_key"] for k in args.models if not os.environ.get(MODELS[k]["env_key"])]
    if missing and not args.dry_run:
        print(f"Missing in .env: {', '.join(missing)}")
        return 1

    if args.check:
        from src.run_models.clients import CLIENTS
        ok = True
        for k in args.models:
            try:
                print(f"OK   {k:6s} requested {MODELS[k]['model']}, returned {CLIENTS[k](MODELS[k]).check()}")
            except Exception as e:
                ok = False
                print(f"FAIL {k:6s} {MODELS[k]['model']}: {type(e).__name__}: {str(e)[:200]}")
        return 0 if ok else 1

    if not args.input or not args.run_id:
        parser.error("--input and --run-id are required unless --check is given")
    items = load_items(args.input)
    groups = len({i["group_id"] for i in items})
    print(f"{len(items)} prompts from {groups} groups × {len(args.models)} models = {len(items) * len(args.models)} requests")
    if args.dry_run:
        return 0

    from src.run_models.clients import CLIENTS
    clients = {k: CLIENTS[k](MODELS[k]) for k in args.models}
    counts = run(items, args.run_id, args.models, clients, workers=args.workers)
    failed = 0
    for k, (done, skipped, bad) in counts.items():
        failed += bad
        print(f"{k:6s} saved {done}, already saved {skipped}, failed {bad}")
    print(f"wrote {OUTPUT_DIR / args.run_id}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
