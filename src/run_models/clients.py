"""One function per provider. Each takes the prompt text and returns the same normalized dict:

``response_text``, ``decision``, ``probabilities``, ``confidence``, ``error``, plus metadata
(``model_version``, ``stop_reason``, ``input_tokens``, ``output_tokens``, ``reasoning_tokens``).

``error`` is for a *model outcome* that leaves the response unusable (refusal, truncated before any
text). Transport and API failures raise instead, after the SDK's own retries, and the runner logs them.
"""

import os

MAX_RETRIES = 6  # SDK-level exponential backoff on 429 / 5xx / connection errors


def _result(**values):
    base = {"response_text": None, "decision": None, "probabilities": None, "confidence": None, "error": None,
            "model_version": None, "stop_reason": None, "input_tokens": None, "output_tokens": None,
            "reasoning_tokens": None}
    base.update(values)
    return base


class GptClient:
    def __init__(self, spec):
        import openai
        self.spec = spec
        self.client = openai.OpenAI(api_key=os.environ[spec["env_key"]], max_retries=MAX_RETRIES)

    def check(self):
        return self.client.models.retrieve(self.spec["model"]).id

    def call(self, text, task, prompt):
        p = self.spec["params"]
        r = self.client.responses.create(
            model=self.spec["model"], instructions=prompt, input=text,
            reasoning={"effort": p["reasoning_effort"]}, max_output_tokens=p["max_output_tokens"], store=False,
        )
        reasoning = getattr(r.usage.output_tokens_details, "reasoning_tokens", None) if r.usage else None
        stop = r.status if r.status == "completed" else f"{r.status}:{getattr(r.incomplete_details, 'reason', None)}"
        refused = any(getattr(c, "type", None) == "refusal" for item in r.output if item.type == "message"
                      for c in item.content)
        text_out = r.output_text or ""
        error = "refusal" if refused else (stop if r.status != "completed" else None)
        return _result(response_text=text_out, error=error, model_version=r.model, stop_reason=stop,
                       input_tokens=r.usage.input_tokens if r.usage else None,
                       output_tokens=r.usage.output_tokens if r.usage else None, reasoning_tokens=reasoning)


class ClaudeClient:
    def __init__(self, spec):
        import anthropic
        self.spec = spec
        self.client = anthropic.Anthropic(api_key=os.environ[spec["env_key"]], max_retries=MAX_RETRIES)

    def check(self):
        return self.client.models.retrieve(self.spec["model"]).id

    def call(self, text, task, prompt):
        p = self.spec["params"]
        # No refusal fallback: a fallback would answer with a different model and contaminate the comparison.
        m = self.client.messages.create(
            model=self.spec["model"], max_tokens=p["max_tokens"], system=prompt,
            thinking={"type": p["thinking"]}, output_config={"effort": p["effort"]},
            messages=[{"role": "user", "content": text}],
        )
        text_out = "".join(b.text for b in m.content if b.type == "text")
        details = getattr(m.usage, "output_tokens_details", None)
        error = m.stop_reason if m.stop_reason in ("refusal", "max_tokens") else None
        return _result(response_text=text_out, error=error, model_version=m.model, stop_reason=m.stop_reason,
                       input_tokens=m.usage.input_tokens, output_tokens=m.usage.output_tokens,
                       reasoning_tokens=getattr(details, "thinking_tokens", None))


class JevClient:
    """Decision-only (amendment §2): the question text is the state, one typed Choice question per task."""

    def __init__(self, spec):
        from typesafe_sdk import TypeSafeClient
        self.spec = spec
        self.client = TypeSafeClient(api_key=os.environ[spec["env_key"]], model=spec["model"])

    def check(self):
        from src.run_models.prompts import load_prompt
        prompt, _ = load_prompt("jev")
        return self.call("I have a mild headache. What should I do?", "escalation", prompt)["model_version"]

    def call(self, text, task, prompt):
        from typesafe_sdk import Choice
        q = prompt[task]
        r = self.client.system_one(state=text, questions={
            "decision": Choice(instructions=q["instructions"], criteria=q["criteria"])})
        a = r.answers["decision"]
        return _result(decision=a.choice, probabilities=dict(a.probabilities), confidence=a.confidence,
                       model_version=r.model, stop_reason="completed",
                       input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)


CLIENTS = {"gpt": GptClient, "claude": ClaudeClient, "jev": JevClient}
