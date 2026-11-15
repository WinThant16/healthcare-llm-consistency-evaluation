"""Load versioned prompt files from ``prompts/``. A version that has been used is never edited (codebase.md §6)."""

import hashlib
import json

from src.run_models.config import MODELS, PROMPT_DIR


def load_prompt(model_key, prompt_dir=PROMPT_DIR):
    """Return ``(prompt, sha256)``: the system prompt text for chat models, the question map for Jev."""
    spec = MODELS[model_key]
    suffix = ".json" if spec["mode"] == "decision" else ".txt"
    raw = (prompt_dir / (spec["prompt_version"] + suffix)).read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return (json.loads(raw) if suffix == ".json" else raw.strip()), digest
