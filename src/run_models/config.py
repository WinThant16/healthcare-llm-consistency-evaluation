"""Frozen-by-version model settings for the experiment runner (codebase.md §1, protocol v1.0 + v1.1).

Changing anything here changes the request key, so a run that already has responses refuses to continue
with different settings. Start a new ``run_id`` instead.
"""

from pathlib import Path

PROMPT_DIR = Path("prompts")
OUTPUT_DIR = Path("outputs/responses")

SYSTEM_PROMPT_VERSION = "patient_system_v2"
JEV_QUESTIONS_VERSION = "jev_questions_v1"

# Protocol v1.0: "medium reasoning/effort, one sample, 512 visible output tokens, no tools".
# Neither chat API can cap visible text separately from reasoning: the API cap covers both. So the API cap
# leaves reasoning headroom, the system prompt asks for a short reply, and every response records its
# visible token count and whether it exceeded VISIBLE_TOKEN_CAP.
VISIBLE_TOKEN_CAP = 512
API_OUTPUT_TOKEN_CAP = 4096
EFFORT = "medium"

MODELS = {
    "gpt": {"model": "gpt-6-astra", "mode": "chat", "env_key": "OPENAI_API_KEY",
            "prompt_version": SYSTEM_PROMPT_VERSION,
            "params": {"reasoning_effort": EFFORT, "max_output_tokens": API_OUTPUT_TOKEN_CAP}},
    "claude": {"model": "claude-opus-5", "mode": "chat", "env_key": "ANTHROPIC_API_KEY",
               "prompt_version": SYSTEM_PROMPT_VERSION,
               "params": {"effort": EFFORT, "thinking": "adaptive", "max_tokens": API_OUTPUT_TOKEN_CAP}},
    "jev": {"model": "jev-latest", "mode": "decision", "env_key": "JEV_API_KEY",
            "prompt_version": JEV_QUESTIONS_VERSION, "params": {}},
}

# USD per million (input, output) tokens. None = price not confirmed, so cost is recorded as null.
PRICES = {"claude": (5.00, 25.00), "gpt": None, "jev": None}


def cost_usd(model_key, input_tokens, output_tokens):
    price = PRICES.get(model_key)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return round((input_tokens * price[0] + output_tokens * price[1]) / 1_000_000, 6)
