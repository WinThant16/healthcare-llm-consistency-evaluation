# Prompts

Versioned prompt templates: the patient-facing system prompt, Jev choice questions, judge/mapper prompts, and paraphrase instructions.

- Owner: engineering lead (model prompts), Sahil (paraphrase prompts), Bruce (mapper prompts).
- Never edit a version that has been used. Add `*_v2` instead (codebase.md §6).

| File | Used by | Status |
|---|---|---|
| `paraphrase_generate_v1.md` | `python -m src.paraphrase generate` / `redo` | Three styles: `p1` neutral, `p2` casual, `p3` worried/indirect. Sees the target premise for correction positives, never the triage gold label |
| `paraphrase_judge_v1.md` | `python -m src.paraphrase check --judge-model ...` | Meaning-check hint for reviewers (guide §6). Never sets `validity` |
| `patient_system_v1.txt` | `gpt`, `claude` | Used in `run-20261115-pilot`. Superseded: its 300-word limit put every Claude reply over 512 visible tokens |
| `patient_system_v2.txt` | `gpt`, `claude` | Current (200-word limit). Not yet frozen |
| `jev_questions_v1.json` | `jev` | Current. Wording from amendment §2. Not yet frozen |

The versions the runner uses are set in `src/run_models/config.py`.