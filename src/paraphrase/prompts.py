"""Load and render the versioned paraphrase prompts in ``prompts/``.

Prompt files are split into sections on lines of the form ``=== name ===``. Placeholders use
``string.Template`` syntax (``$question``) so the JSON braces in the prompts need no escaping.
"""

import re
from pathlib import Path
from string import Template

PROMPT_DIR = Path("prompts")
GENERATE_VERSION = "paraphrase_generate_v1"
JUDGE_VERSION = "paraphrase_judge_v1"

# Fixed variant -> style mapping (prompts/paraphrase_generate_v1.md). Never reorder after a run.
STYLES = {"p1": "neutral", "p2": "casual", "p3": "worried_indirect"}
VARIANT_IDS = ("original", *STYLES)

_SECTION_RE = re.compile(r"^=== (\w+) ===[ \t]*$", re.MULTILINE)


def load_sections(version, prompt_dir=PROMPT_DIR):
    """Return ``{section_name: text}`` for ``prompts/{version}.md``."""
    text = (Path(prompt_dir) / f"{version}.md").read_text(encoding="utf-8")
    parts = _SECTION_RE.split(text)
    # parts = [preamble, name1, body1, name2, body2, ...]
    sections = {parts[i]: parts[i + 1].strip("\n") for i in range(1, len(parts), 2)}
    if not sections:
        raise ValueError(f"{version}: no '=== name ===' sections found")
    return sections


def task_kind(row):
    """``correction_positive``, ``correction_control`` or ``escalation`` for a question row."""
    if row["task"] == "escalation":
        return "escalation"
    if row["task"] == "correction":
        return "correction_control" if row["is_control"] else "correction_positive"
    raise ValueError(f"unknown task {row['task']!r}")


def _task_block(sections, row):
    kind = task_kind(row)
    if kind == "correction_positive":
        premise = (row.get("target_false_premise") or "").strip()
        if not premise:
            raise ValueError(f"{row.get('group_id')}: correction positive has no target_false_premise")
        return Template(sections["task_correction_positive"]).substitute(premise=premise)
    return sections[f"task_{kind}"]


def _render(template_text, **values):
    # substitute() raises on a missing placeholder, so a broken prompt fails loudly.
    return Template(template_text).substitute(**values)


def generation_messages(row, sections, redo=None):
    """System and user text for one generation call.

    ``redo`` is ``None`` for a first attempt, or a dict with ``variant_id``, ``rejected`` and ``reason``
    to replace a single rewrite that a reviewer marked invalid.
    """
    user = _render(sections["user"], task_block=_task_block(sections, row), question=row["original_question"])
    if redo is None:
        user += "\n\n" + sections["generate"]
    else:
        vid = redo["variant_id"]
        user += "\n\n" + _render(sections["redo"], variant_id=vid, style=STYLES[vid],
                                 rejected=redo["rejected"], reason=redo["reason"] or "(no reason given)")
    return sections["system"], user


def judge_messages(row, paraphrase, sections):
    user = _render(sections["user"], task_block=_task_block(sections, row), question=row["original_question"],
                   paraphrase=paraphrase)
    return sections["system"], user
