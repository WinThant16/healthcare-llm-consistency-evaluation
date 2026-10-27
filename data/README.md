# Data

This directory contains datasets used for the healthcare LLM consistency evaluation.

## Structure

- `raw/` - Original datasets exactly as downloaded. Never edit these files.
- `processed/` - Cleaned and structured data used by the evaluation pipeline (see `codebase.md` §5)
- `fetch_raw.sh` - Downloads raw datasets that are not committed and verifies checksums

```bash
bash data/fetch_raw.sh                      # run from repo root after creating .venv
python -m src.data.build_pools              # raw → data/processed/candidates/{domain}.jsonl
python -m src.data.validate data/processed/candidates/*.jsonl
python -m pytest tests                      # schema + loader tests
```

## Candidate pools: `processed/candidates/`

These are git-ignored because they contain source-derived text and the repo is public. Rebuild them with
`build_pools`. Each row follows the question schema in `codebase.md` §5.1 (see `src/data/schema.py`), with
three extensions for the candidate stage:

- `candidate_id`: a stable pool ID (`onc-cm-412`, `ph-uphill-10009_p`, `tri-meb_v1_case_0001_r01`).
  `group_id` stays `null` until sampling assigns `onc-001`, and so on.
- `eligibility_status` may be `pending`. The final stage only allows `include` / `exclude` / `hold`.
- `source_meta`: source-specific fields kept for screening and stratified sampling (cancer type, UPHILL
  claim/source_db/subjects, CARE-Bench split/specialty/age group/anchor facts).

| Pool | Rows | Gold labels | Notes |
|---|---:|---|---|
| `oncology` | 735 | 585 `correction_required` · 150 `no_correction` | All NFP controls start on `hold` (see below). `target_false_premise` is empty: Cancer-Myth gives the correction, not the premise, so the clinical lead writes it from `source_evidence` |
| `public_health` | 1,613 | 848 `correction_required` · 765 `no_correction` | Level 2 only; mixture/fabricated dropped (325); exact-duplicate claims dropped (7); `target_false_premise` = cleaned claim |
| `acute_triage` | 439 | 245 `0A` · 55 `0B` · 117 `1A` · 22 `1B` | Round 1 only. `source_evidence` = the case's `source_grounding` |

**Cancer-Myth NFP warning.** All 150 rows in `cancer-myth-nfp` have a `non_presupposition` field that
*describes a presupposition* ("The presupposition is that…"). They may not be clean no-false-presupposition
controls. The loader puts them all on `hold` until the data lead and clinical lead check the Cancer-Myth
paper's definition of NFP.

## Sources at a glance

| Folder | Dataset | Domain / task | Committed? | Licence / terms |
|---|---|---|---|---|
| `raw/cancer-myth/`, `raw/cancer-myth-nfp/` | Cancer-Myth (arXiv:2504.11373) | `oncology` / correction | Yes | No explicit licence. Internal use with citation. Don't redistribute derived questions or paraphrases without author permission (protocol v1.0). |
| `raw/uphill/` | UPHILL (arXiv:2312.08800) | `public_health` / correction | **No** (git-ignored; run `fetch_raw.sh`) | No stated licence. `UPHILL.csv` is 629 MB. |
| `raw/care-bench/` | CARE-Bench v1.1 (arXiv:2608.03731), HF `ningkko/CARE-Bench` | `acute_triage` / escalation | Yes | CC BY-NC 4.0 (protocol v1.0). Underlying source terms still apply. Raw source dialogues are withheld by the authors. |

---

## UPHILL: `raw/uphill/`

Source: https://flair-iisc.github.io/uphill/ (Google Drive folder). Code: https://github.com/navreeetkaur/UPHILL

| File | Rows | Use |
|---|---:|---|
| `queries.csv` | 9,725 (1,945 claims × 5 levels) | **Main input.** Claims, veracity and generated queries |
| `UPHILL.csv` | not counted (629 MB) | `queries.csv` plus responses from 2023 models and entailment predictions. Not needed for our pipeline |
| `entailment_validation.csv` | 463 | Human (MTurk / expert) stance labels used to validate the authors' entailment model. Not needed |

### `queries.csv` columns

| Column | Notes |
|---|---|
| `Unnamed: 0` | Pandas index, ignore |
| `claim_id` | `{n}_p` = PubHealth, `{n}_m` = Monant, `{n}_{k}` = fabricated. Use as `source_id` |
| `claim` | The health claim (often a news headline) |
| `claim_veracity` | `true` 3,830 · `false` 4,270 · `mixture` 795 · `fabricated` 830 (row counts; ÷5 for claims) |
| `veracity_explanation`, `main_text`, `sources`, `fact_checkers` | Fact-check evidence → `source_evidence` |
| `subjects`, `date_published` | Topic tags (mostly `medical`, `health news`) and date |
| `source_db` | `pubhealth`, `monant`, `fabricated` |
| `presupposition_level` | `Neutral`, `Mild Presupposition`, `Unequivocal Presupposition`, `Writing Request`, `Writing Demand` |
| `query_with_presupposition` | The query text → `original_question` |

### Level 2 (`Unequivocal Presupposition`) pool, one query per claim

| `source_db` | false | true | mixture | fabricated |
|---|---:|---:|---:|---:|
| pubhealth | 203 | 587 | 95 | 0 |
| monant | 651 | 179 | 64 | 0 |
| fabricated | 0 | 0 | 0 | 166 |
| **Total** | **854** | **766** | 159 | 166 |

That is plenty for 60 positives + 20 controls. The binding constraint is eligibility screening, not supply.

### Things to watch when screening (for the data lead and the clinical lead)

1. **Many claims aren't personal-health questions.** Examples include news and policy items ("Federal shutdown
   affected probe into unique bovine TB case", Planned Parenthood video claims, "viral post in January 2020
   was an authentic bulletin"). Exclude them under the "patient-facing" rule.
2. **PubHealth labels can rate the *news story*, not the claim.** Some PubHealth `veracity_explanation`
   text is a HealthNewsReview critique of reporting quality (e.g. claim `10009_p`: *"This was a very clean
   and efficient story…"*). A `false` there may mean "poorly reported", not "medically false". Check the
   explanation before accepting a PubHealth label. Monant labels are claim-level fact-checks and are safer.
3. **Queries are templated.** Level 2 queries follow a handful of GPT-3.5 templates ("There are studies which
   indicate that X. Could you help me with more data on it?"). Paraphrases will still vary the wording, but
   note this as a limitation.
4. Seven Level 2 claims are exact-text duplicates. Deduplicate.
5. Some claims have stray quote or bracket characters from scraping (e.g. `It] is nearly impossible…`).

---

## CARE-Bench v1.1: `raw/care-bench/`

Source: https://huggingface.co/datasets/ningkko/CARE-Bench (public release: 439 cases, 925 labeled
prefixes). The data-file checksums match `VALIDATION_REPORT.txt`. The HF `README.md` card has been
edited since the report.

| File | Rows | Use |
|---|---:|---|
| `evaluation_prefixes_public.jsonl` | 925 | **Main input.** One row per conversation prefix, with `gold_label` |
| `cases_public.jsonl` | 439 | Case metadata, source anchor facts, full extended conversation |
| `model_inputs_public.jsonl` | 925 | `input_messages` only (subset of the prefixes file) |
| `label_schema.json` | | The four labels and their meanings |
| `submission_template_public.csv` | 925 | Their leaderboard format, not needed |

### `evaluation_prefixes_public.jsonl` fields

| Field | Notes |
|---|---|
| `prefix_id` | e.g. `meb_v1_case_0001_r01` → `source_id` |
| `case_id`, `round_id` | Case and turn number (1–3) |
| `split` | `development` 609 · `validation` 181 · `public_test_1` 135 |
| `source_dataset` | `MedDialog_OpenMed`, `ChatDoctor_HealthCareMagic`, `ChatDoctor_iCliniq`, `PriMock57`, `Followup_Q_derived` |
| `specialty_or_domain` | 16 specialties (largest: reproductive/urinary 95, dermatology 64 in round 1) |
| `case_age_group`, `case_speaker_role` | e.g. `adult`, `caregiver_parent` |
| `information_state` | `sufficient_information` / `insufficient_information` |
| `round_role` | How the round was constructed (`source_baseline`, `source_current_action`, `clarification`, …) |
| `gold_label` | `0A_NO_ESCALATION_INFO_NEEDED`, `0B_NO_ESCALATION_SELF_CARE_MONITOR`, `1A_ESCALATION_NONURGENT_CARE`, `1B_ESCALATION_URGENT_CARE` → map to `0A`/`0B`/`1A`/`1B` |
| `input_patient_turns` | List of patient messages up to this round |
| `input_messages` | Ready-made chat messages with CARE-Bench's own system prompt. **We use our frozen system prompt instead.** |

### Gold labels by round

| `round_id` | 0A | 0B | 1A | 1B | Total |
|---|---:|---:|---:|---:|---:|
| 1 (single patient turn) | 245 | 55 | 117 | **22** | 439 |
| 2 | 4 | 102 | 191 | 45 | 342 |
| 3 | 0 | 5 | 34 | 105 | 144 |

**Key constraint.** Our study paraphrases a *single* question, so round 1 prefixes are the natural fit
(no prior turns, 31 words on average). But round 1 has only **22 `1B` cases**, so 20 per class leaves
almost no room for eligibility exclusions. Rounds 2–3 have more `1B` cases but are multi-turn. Merging
turns into one message, or paraphrasing only the last turn, is a design change the team needs to decide.
`PriMock57` and `Followup_Q_derived` have almost no round-1 `0B` or `1B` cases, so 1B sampling will lean on
the ChatDoctor sources.
