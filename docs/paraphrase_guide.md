# Paraphrase Stage Guide

**Owner:** Sahil (paraphrase & quality lead). **Meaning check:** Sahil + Win (annotation guide §6, §7.1 step 3).
**Status:** v1 pipeline and prompts, *proposed*. Styles and generator/judge models need team sign-off before the
pilot run.

Stage 3 of the pipeline (codebase.md §2): `data/processed/questions.jsonl` → `data/processed/variants.jsonl`.
Each question group gets the original plus three paraphrases (`p1`, `p2`, `p3`).

## Design

| Variant | Style | What changes | What must not change |
|---|---|---|---|
| `original` | source | nothing | — |
| `p1` | neutral | words and sentence structure, same register | everything in guide §6 |
| `p2` | casual | informal chat-app phrasing, contractions, shorter | everything in guide §6 |
| `p3` | worried / indirect | expressed feelings, hedged or indirect asking | everything in guide §6; **no added urgency** |

- The proposal's "more confident or leading" style is dropped. Strengthening a false premise changes the
  correction stimulus, which guide §6 forbids ("same harmful/false assumption").
- `p3` is the riskiest style for triage, because worry can read as urgency. The prompt forbids new urgency, and
  the rule checks flag added urgency or minimizing words. Reviewers should look hardest at triage `p3`.
- The generator sees the **target false premise** for correction positives, so it can keep it intact. It never
  sees the triage **gold label**, so it can't tilt the wording toward an answer.
- One call per group writes all three styles, so they differ from each other.
- **Generator ≠ evaluated models, ideally.** If the generator is GPT-6 Astra or Claude Opus 5, that model is
  answering its own rewrites. Record it as a limitation if unavoidable. The judge should also differ from the
  generator.

## Workflow

```bash
# 0. Inputs (built locally; git-ignored)
bash data/fetch_raw.sh && python -m src.data.build_pools && python -m src.data.sample --pilot

# 1. Generate (put ANTHROPIC_API_KEY and/or OPENAI_API_KEY [+ OPENAI_BASE_URL] in .env)
python -m src.paraphrase generate --set pilot --provider anthropic --model <generator-model>

# 2. Automatic checks: rule flags always; judge verdict if a judge model is given
python -m src.paraphrase check --set pilot --judge-provider openai --judge-model <judge-model>

# 3. Review sheet → data/processed/paraphrase/pilot/review.csv
python -m src.paraphrase export --set pilot

# 4. Two reviewers fill the sheet (below), then:
python -m src.paraphrase import --set pilot data/processed/paraphrase/pilot/review.csv

# 5. Regenerate anything invalid, re-check, re-export only what's new, review again
python -m src.paraphrase redo --set pilot --provider anthropic --model <generator-model>
python -m src.paraphrase check --set pilot --judge-provider openai --judge-model <judge-model>
python -m src.paraphrase export --set pilot --force

# 6. QC report (no source text, safe to commit) and the production gate
python -m src.paraphrase qc --set pilot --out report/paraphrase_qc_pilot.md
python -m src.paraphrase validate --set pilot     # exit 0 only if every group has 4 valid variants
```

Use `--set full` for the 240 groups after `data-v1.0` is tagged. Use `--limit 3` on the first `generate` to look
at a few groups before running the rest. Re-running any step is safe: model calls are cached, and only new or
regenerated text is re-checked.

## Filling the review sheet

Open `review.csv` in a spreadsheet. Each row is one paraphrase, next to its original, with the target premise
(correction positives) or gold label (triage) for reference. `rule_flags` and `judge_*` are **hints only**.
They miss problems and raise false alarms.

1. **Review independently.** Reviewer 1 fills `reviewer_1`, `validity_1`, `reason_1`. Reviewer 2 fills the `_2`
   columns, without looking at reviewer 1's. The simplest way is for each of you to fill your own copy of the
   sheet, then paste your columns into one file before importing. Reviewer IDs must differ.
2. **Validity values:** `valid`, `invalid` or `needs_review`. Apply guide §6, comparing side by side:
   - same symptoms, severity, duration, timing, patient details and risk factors
   - same false assumption, exactly as firm (correction positives); no new assumption (controls)
   - same expected care action (triage): no added or removed urgency, red flags, or reassurance
   - same request, and same present-versus-hypothetical framing and patient
   - nothing added: no diagnosis, negation, warning, history or new claim
3. **Reasons** are required for `invalid` and `needs_review`. Keep them short and generic ("adds urgency", "drops
   duration", "premise weakened"). **Don't quote the question or paraphrase**: the decisions file is committed
   and the repo is public.
4. **Disagreements.** If you disagree, or either says `needs_review`, fill `final_validity` and `final_reason`
   after discussing. `valid` always needs both independent reviews.

Rows with nothing filled are skipped, so you can import in batches. Any invalid row blocks the whole import, so
nothing is half-written.

## Files

| Path | Contents | Committed? |
|---|---|---|
| `prompts/paraphrase_generate_v1.md`, `prompts/paraphrase_judge_v1.md` | Versioned prompts (never edit once used) | Yes |
| `data/processed/paraphrase/{set}/variants.jsonl` | §5.2 rows: `group_id`, `variant_id`, `text`, `generator`, `validity`, `validity_reason`, `verified_by` | No (source-derived) |
| `data/processed/paraphrase/{set}/generation_log.jsonl` | One row per model call: settings, model version, tokens, latency, attempt, errors | No |
| `data/processed/paraphrase/{set}/checks.jsonl` | Rule flags and judge verdicts, keyed by text hash | No |
| `data/processed/paraphrase/{set}/review.csv` | Review sheet | No |
| `data/processed/paraphrase/cache.jsonl` | API response cache: share it privately to reproduce the exact text | No |
| `data/decisions/paraphrase/{set}.csv` | Reviewer decisions keyed by `group_id`, `variant_id`, `text_sha` | **Yes** |
| `report/paraphrase_qc_{set}.md` | QC report: counts, agreement, check performance | Yes |

Decisions are tied to a hash of the exact text reviewed. A regenerated paraphrase starts again at
`needs_review`, and the old decision stays on record. The runner should read `variants.jsonl` only after
`validate` passes.

## Open decisions

| Decision | Who |
|---|---|
| Confirm the three styles (above) | Sahil + Win + Advait |
| Generator and judge models (not GPT-6 Astra / Claude Opus 5 if possible) | Sahil + engineering lead |
| Is expressed worry in triage `p3` acceptable, or does it count as an urgency change? | Sahil + Win |
| Who reviews the full 240: Win on all 720 paraphrases (guide §7.1) or split with a third reviewer? | Sahil + Win |
