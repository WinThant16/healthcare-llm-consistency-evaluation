# Prompts

Versioned prompt templates: the patient-facing system prompt, Jev choice questions, judge/mapper prompts, and paraphrase instructions.

- Owner: engineering lead (model prompts), Sahil (paraphrase prompts), Bruce (mapper prompts).
- Never edit a version that has been used. Add `*_v2` instead (codebase.md §6).

| File | Used by | Notes |
|---|---|---|
| `paraphrase_generate_v1.md` | `python -m src.paraphrase generate` / `redo` | Three styles: `p1` neutral, `p2` casual, `p3` worried/indirect. Sees the target premise for correction positives, never the triage gold label |
| `paraphrase_judge_v1.md` | `python -m src.paraphrase check --judge-model ...` | Meaning-check hint for reviewers (guide §6). Never sets `validity` |
