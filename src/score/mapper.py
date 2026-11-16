"""Bound, blinded mapper jobs; offline import and an optional compatible JSON chat endpoint."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .inputs import load_state
from .schema import (OBSERVATION_FIELDS, read_json, read_rows, require, sha256, text_value, unique,
                     validate_observation, write_json, write_rows)


def jobs(work):
    state = load_state(work)
    result = []
    for context in state["contexts"]:
        identity = {"context": context, "prompt_sha256": state["prompt_sha256"], "guide_version": state["guide_version"]}
        result.append({"blind_id": context["blind_id"], "input_sha256": sha256(identity),
                       "prompt_sha256": state["prompt_sha256"], "guide_version": state["guide_version"],
                       "messages": [{"role": "system", "content": state["mapper_prompt"]},
                                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]})
    return result


def export_jobs(work, out):
    requests = jobs(work)
    write_rows(out, requests)
    return {"jobs": len(requests), "out": str(out)}


def load_mapper(work):
    rows = []
    for path in sorted((Path(work) / "private/mapper").glob("*.json")):
        row = read_json(path)
        require(path.stem == row.get("blind_id"), "Mapper cache filename/ID mismatch")
        rows.append(row)
    return unique(rows, lambda row: row["blind_id"], "cached mapper ID")


def import_results(work, source):
    state = load_state(work)
    expected = {row["blind_id"]: row for row in jobs(work)}
    ctx = {row["blind_id"]: row for row in state["contexts"]}
    rows = read_rows(source)
    unique(rows, lambda row: row["blind_id"], "mapper blind_id")
    existing = load_mapper(work)
    clean = []
    for row in rows:
        bid = row.get("blind_id")
        require(bid in expected, "Unknown mapper blind_id")
        for field in ("input_sha256", "prompt_sha256", "guide_version"):
            require(row.get(field) == expected[bid][field], f"Wrong/stale mapper {field}")
        text_value(row.get("mapper_model"), "returned mapper_model")
        text_value(row.get("mapper_identity"), "mapper_identity")
        observation = validate_observation(row.get("observation"), ctx[bid])
        record = {k: row[k] for k in ("blind_id", "input_sha256", "prompt_sha256", "guide_version", "mapper_model", "mapper_identity")}
        record["observation"] = observation
        require(bid not in existing or existing[bid] == record,
                "Mapper result already stored; use a new scoring workspace to rescore")
        clean.append(record)
    identities = {(r["mapper_identity"], r["mapper_model"]) for r in [*existing.values(), *clean]}
    require(len(identities) <= 1, "Do not mix mapper models/settings in one scoring workspace")
    # Validate the whole batch before writing anything. Exact repeated imports are idempotent.
    for record in clean:
        if record["blind_id"] not in existing:
            write_json(Path(work) / "private/mapper" / (record["blind_id"] + ".json"), record)
    return {"imported": len(clean), "total": len(set(existing) | {r["blind_id"] for r in clean}), "expected": len(expected)}


def validate_cached(work):
    """Revalidate saved results against the frozen inputs on every export."""
    state = load_state(work)
    expected = {row["blind_id"]: row for row in jobs(work)}
    ctx = {row["blind_id"]: row for row in state["contexts"]}
    cached = load_mapper(work)
    for bid, row in cached.items():
        require(bid in expected, "Extraneous cached mapper result")
        for field in ("input_sha256", "prompt_sha256", "guide_version"):
            require(row.get(field) == expected[bid][field], "Cached mapper provenance changed")
        validate_observation(row["observation"], ctx[bid])
    require(len({(r["mapper_identity"], r["mapper_model"]) for r in cached.values()}) <= 1,
            "Cached mapper models/settings are mixed")
    return cached


def validate_endpoint(base_url, max_tokens):
    parsed = urllib.parse.urlparse(base_url)
    require(parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}),
            "Use HTTPS or loopback HTTP")
    require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
            "Endpoint must not contain credentials/query/fragment; use the key environment variable")
    require(isinstance(max_tokens, int) and 64 <= max_tokens <= 8192, "max_tokens must be between 64 and 8192")


def chat_completion(base_url, api_key, model, messages, max_tokens=768, attempts=4, timeout=45):
    """User-selected chat-completions-compatible JSON API. No default model or provider."""
    validate_endpoint(base_url, max_tokens)
    require(isinstance(attempts, int) and attempts >= 1, "attempts must be positive")
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens,
                       "response_format": {"type": "json_object"}}).encode("utf-8")
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", body,
                                    {"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
    # Do not forward an Authorization header through a provider redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    for attempt in range(attempts):
        try:
            with opener.open(request, timeout=timeout) as stream:
                result = json.load(stream)
            choice = result["choices"][0]
            require(choice.get("finish_reason") in {None, "stop"}, "Mapper output refused/truncated; no label saved")
            observation = json.loads(choice["message"]["content"])
            return observation, {"model": result.get("model") or model, "usage": result.get("usage"),
                                 "provider_response_id": result.get("id"), "attempts": attempt + 1}
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise ValueError(f"Mapper HTTP status {status}; provider body/credentials not logged") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise ValueError("Mapper connection failed; retry later to resume cached work") from None
        time.sleep(min(2 ** attempt, 8))


def run_mapper(work, base_url, model, key_env="MAPPER_API_KEY", max_jobs=1, max_tokens=768):
    require(isinstance(max_jobs, int) and not isinstance(max_jobs, bool) and max_jobs > 0, "Explicit --max-jobs must be positive integer")
    validate_endpoint(base_url, max_tokens)
    text_value(model, "mapper model")
    key = os.environ.get(key_env)
    require(key, f"Set {key_env} in the environment (not saved in workspace)")
    settings = {"requested_model": model, "endpoint_sha256": sha256(base_url), "max_tokens": max_tokens,
                "transport": "chat-completions-json"}
    identity = sha256(settings)
    config_path = Path(work) / "private/mapper_config.json"
    existing = validate_cached(work)
    require(not existing or all(row["mapper_identity"] == identity for row in existing.values()),
            "Mapper configuration changed; create a new scoring workspace")
    if config_path.exists():
        require(read_json(config_path) == settings, "Mapper configuration changed; create a new scoring workspace")
    else:
        write_json(config_path, settings)
    state = load_state(work)
    ctx = {c["blind_id"]: c for c in state["contexts"]}
    calls = 0
    for job in jobs(work):
        bid = job["blind_id"]
        if bid in existing:
            continue
        if calls == max_jobs:
            break
        started = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        observation, provider = chat_completion(base_url, key, model, job["messages"], max_tokens=max_tokens)
        calls += 1
        observation = validate_observation(observation, ctx[bid])
        require(not existing or all(r["mapper_model"] == provider["model"] for r in existing.values()),
                "Provider mapper version changed during run; use separate scoring workspaces")
        record = {k: job[k] for k in ("blind_id", "input_sha256", "prompt_sha256", "guide_version")}
        record.update(mapper_model=provider["model"], mapper_identity=identity, observation=observation)
        write_json(Path(work) / "private/mapper" / (bid + ".json"), record)
        write_json(Path(work) / "private/mapper_usage" / (bid + ".json"),
                   {**provider, "timestamp": timestamp, "latency_s": time.monotonic() - started, "cost_usd": None})
        existing[bid] = record
    return {"new_jobs": calls, "cached_jobs": len(existing), "remaining_jobs": len(ctx) - len(existing)}
