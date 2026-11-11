"""Minimal LLM client for paraphrase generation and the meaning-check judge.

Uses only the standard library, so the repo needs no extra dependencies. Two providers:

- ``anthropic``: the Messages API. Key: ``ANTHROPIC_API_KEY``.
- ``openai``: any OpenAI-compatible Chat Completions endpoint (OpenAI, OpenRouter, Together, Gemini's
  OpenAI-compatible endpoint, a local vLLM server...). Key: ``OPENAI_API_KEY``; endpoint:
  ``OPENAI_BASE_URL`` (default ``https://api.openai.com/v1``).

Keys are read from the environment or from ``.env`` in the repo root (git-ignored).

Every call is cached in a JSONL file keyed by a hash of the full request (provider, model, messages and
settings), so re-running a command never re-bills or silently changes a paraphrase. Delete the cache entry,
or change a setting, to force a new sample.
"""

import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CACHE = Path("data/processed/paraphrase/cache.jsonl")
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def load_dotenv(path=".env"):
    """Set variables from a simple ``KEY=value`` file without overriding the real environment."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip("'\""))


class LLMError(RuntimeError):
    pass


class Cache:
    def __init__(self, path=DEFAULT_CACHE):
        self.path = Path(path)
        self.entries = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        e = json.loads(line)
                        self.entries[e["key"]] = e

    def get(self, key):
        return self.entries.get(key)

    def put(self, entry):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.entries[entry["key"]] = entry


class LLMClient:
    """``complete(system, user) -> dict`` with ``text``, ``model_version``, token counts and latency."""

    def __init__(self, provider, model, temperature=None, seed=None, max_tokens=1024, cache=None,
                 max_tokens_field="max_tokens", timeout=120, max_retries=6):
        if provider not in {"anthropic", "openai"}:
            raise ValueError("provider must be 'anthropic' or 'openai'")
        self.provider, self.model = provider, model
        self.temperature, self.seed, self.max_tokens = temperature, seed, max_tokens
        self.max_tokens_field = max_tokens_field
        self.timeout, self.max_retries = timeout, max_retries
        self.cache = cache if cache is not None else Cache()

    @property
    def settings(self):
        return {"provider": self.provider, "model": self.model, "temperature": self.temperature,
                "seed": self.seed, "max_tokens": self.max_tokens}

    def _request(self, system, user):
        if self.provider == "anthropic":
            body = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            if self.temperature is not None:
                body["temperature"] = self.temperature
            headers = {"x-api-key": _key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"}
            return "https://api.anthropic.com/v1/messages", headers, body
        body = {"model": self.model, self.max_tokens_field: self.max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.seed is not None:
            body["seed"] = self.seed
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        return f"{base}/chat/completions", {"Authorization": f"Bearer {_key('OPENAI_API_KEY')}"}, body

    def cache_key(self, system, user, tag=""):
        payload = json.dumps({**self.settings, "system": system, "user": user, "tag": tag}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def complete(self, system, user, tag=""):
        """Return the cached result if there is one, otherwise call the API. ``tag`` varies the cache key
        (used for redo attempts so a second redo can get a new sample)."""
        key = self.cache_key(system, user, tag)
        hit = self.cache.get(key)
        if hit is not None:
            return {**hit["result"], "cached": True}
        result = self._call(system, user)
        self.cache.put({"key": key, "settings": self.settings, "tag": tag, "result": result,
                        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return {**result, "cached": False}

    def _call(self, system, user):
        url, headers, body = self._request(system, user)
        data = json.dumps(body).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=data, method="POST",
                                         headers={**headers, "content-type": "application/json"})
            start = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                return self._parse(raw, time.monotonic() - start)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                if e.code not in RETRY_STATUS or attempt == self.max_retries:
                    raise LLMError(f"HTTP {e.code} from {self.provider}: {detail}") from e
                retry_after = e.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 2 ** attempt
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == self.max_retries:
                    raise LLMError(f"network error calling {self.provider}: {e}") from e
                wait = 2 ** attempt
            time.sleep(min(wait, 60) + random.random())
        raise LLMError("unreachable")

    def _parse(self, raw, latency):
        if self.provider == "anthropic":
            text = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
            usage = raw.get("usage", {})
            tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
            stop = raw.get("stop_reason")
        else:
            choice = (raw.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            usage = raw.get("usage") or {}
            tokens_in, tokens_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
            stop = choice.get("finish_reason")
        return {"text": text, "model_version": raw.get("model", self.model), "tokens_in": tokens_in,
                "tokens_out": tokens_out, "latency_s": round(latency, 3), "stop_reason": stop}


def _key(name):
    value = os.environ.get(name)
    if not value:
        raise LLMError(f"{name} is not set. Put it in .env (git-ignored) or export it.")
    return value


def parse_json_object(text):
    """Parse the first JSON object in a model reply, tolerating code fences or stray text around it."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if "\n" in text else text
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("reply JSON is not an object")
    return obj
