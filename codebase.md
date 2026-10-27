# Codebase Guide

How this repository is organized, what data moves between stages, and the conventions to follow when you
implement your part. Read this before writing code or adding data.

**Project:** Consistency of Escalation & False-Presupposition Detection in Healthcare LLMs Under Paraphrasing
**Team:** Abhi Desai, Advait Chalke, Bruce Nguyen, Sahil Gupta, Win Thant Tin Han

## Source of truth

Where documents conflict, follow this order:

1. [`docs/protocol_v1.1_amendment.md`](docs/protocol_v1.1_amendment.md). Replaces Pregnant Questions with
   UPHILL and Gemini with Jev (decision-only). *Proposed; takes effect once the team approves it.*
2. [`docs/protocol_v1.0.pdf`](docs/protocol_v1.0.pdf), the Week 1 Study Design Freeze (Oct 5, 2026). Scope,
   models, labels, review rules and the GIR metric.
3. [`docs/annotation_guide_v1.md`](docs/annotation_guide_v1.md). Labeling rules, field definitions and the
   QC workflow. This file is the most detailed reference in the repo. Where it mentions Pregnant Questions or
   `maternal_infant`, read UPHILL / `public_health`.
4. [`docs/proposal.pdf`](docs/proposal.pdf). The original proposal and six-week execution and ownership plan.
   **Its domain list, paraphrase count (4) and question counts are superseded by the freeze.**

This guide summarizes those documents and adds engineering conventions. If it disagrees with them, they
win. Fix this file in a PR.

---

## 1. Frozen study scope

| Domain (`domain`) | Source dataset | Task (`task`) | Sample |
|---|---|---|---:|
| `oncology` | Cancer-Myth (+ NFP controls) | `correction` | 60 positive + 20 controls |
| `public_health` | UPHILL (v1.1; was Pregnant Questions) | `correction` | 60 false-claim + 20 true-claim controls |
| `acute_triage` | CARE-Bench | `escalation` | 80 (20 per action class) |
| **Total** | | | **240 question groups** |

- **Question group:** 1 original + **3** meaning-preserving paraphrases = 4 variants (`original`, `p1`, `p2`, `p3`).
- **Volume:** 960 prompts per model × 3 models = **2,880 responses**.
- **Models:**

  | `model_key` | Model | Mode |
  |---|---|---|
  | `gpt` | GPT-6 Astra (`gpt-6-astra`) | Free-text chat: medium effort, 512 output tokens, 1 sample, no tools |
  | `claude` | Claude Opus 5 (`claude-opus-5`) | Free-text chat, same settings |
  | `jev` | Jev, TypeSafe AI (v1.1; was Gemini 3.8 Flash) | **Decision-only:** typed Choice question, returns choice + probabilities |

  Jev's decisions go straight into GIR and accuracy. It isn't scored for correction wording, accommodation
  or conflict, and it skips the mapper. See amendment §2.
- **Escalation classes** (CARE-Bench, never redefined): `0A` clarification, `0B` self-care/monitor,
  `1A` nonurgent professional care, `1B` urgent/emergency.
- **Primary metric: Group Inconsistency Rate (GIR).** A group is inconsistent if its scorable variants
  contain more than one decision. GIR = 100 × inconsistent groups ÷ eligible, fully scored groups.
  Report it by model, domain and behavior, with bootstrap 95% CIs that resample question groups.
  Accuracy against gold labels is reported **separately**.

---

## 2. Pipeline and ownership

```
 data/raw ─► data/processed/questions ─► data/processed/variants ─► outputs/responses ─► outputs/annotations ─► outputs/metrics + figures
   (1)              (2)                         (3)                        (4)                    (5)                        (6)
 collect      eligibility + gold          paraphrase + verify           run models         blinded scoring            GIR, accuracy, CIs
```

| Stage | Owner | Main deliverable | Code (planned) |
|---|---|---|---|
| 1. Collect candidates, schemas, master dataset | **Advait**, project & data lead | `data/processed/questions.jsonl`, dataset docs | `src/data/` |
| 2. Eligibility, gold labels, evidence, adjudication | **Win**, clinical-labeling lead | Gold labels, annotation guide | n/a |
| 3. Paraphrase generation and meaning check | **Sahil**, paraphrase & quality lead | `data/processed/variants.jsonl`, QC report | `src/paraphrase/` |
| 4. Model APIs, prompts, experiment runner | **Engineering lead** (Member 4; Abhi?, to be confirmed) | Reproducible runner, raw responses, logs | `src/run_models/` |
| 5–6. Scoring mapper, human audit stats, metrics, plots | **Bruce**, evaluation & analysis lead | Scores, GIR, CIs, figures | `src/score/`, `src/analyze/` |

**Required pairings.** Advait and Win review inclusion and labels. Win and Sahil verify paraphrase meaning.
Member 4 and Bruce agree on the response and annotation formats **before** any model run. Two reviewers
score the 5-question pilot and the stratified 20% audit sample.

---

## 3. Repository layout

What exists today (✓) and what is planned. Create a planned directory when you first need it, not before.

```
.
├── README.md                     ✓ project overview
├── codebase.md                   ✓ this file
├── requirements.txt              ✓ pandas, pyarrow
├── .gitignore                    ✓ ignores .venv/, .env, __pycache__/
├── docs/
│   ├── annotation_guide_v1.md    ✓ labeling and QC rules (Win)
│   ├── proposal.pdf              ✓ proposal + execution plan
│   ├── protocol_v1.0.pdf         ✓ Week 1 study design freeze
│   └── protocol_v1.1_amendment.md ✓ UPHILL + Jev changes (proposed)
├── data/
│   ├── README.md                 ✓
│   ├── raw/                      ✓ source datasets, never edited
│   │   ├── cancer-myth/          ✓ validation-00000-of-00001.parquet  (585 rows)
│   │   ├── cancer-myth-nfp/      ✓ nfp-00000-of-00001.parquet         (150 rows)
│   │   ├── uphill/                 to add
│   │   └── care-bench/             to add
│   └── processed/                  pipeline outputs (questions, variants, splits)
├── prompts/                        versioned patient-facing and judge prompts
├── src/
│   ├── main.py                   ✓ Cancer-Myth loader smoke test
│   ├── data/                       loaders and source-to-gold mapping, one module per dataset
│   ├── paraphrase/
│   ├── run_models/
│   ├── score/
│   └── analyze/
├── outputs/                        responses/, annotations/, metrics/, figures/
├── tests/
├── report/
└── presentation/
```

### Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/main.py          # prints a preview of Cancer-Myth
```

Run scripts from the repo root. Paths in the code are relative to it.

---

## 4. Raw source data

### Cancer-Myth: `data/raw/cancer-myth/validation-00000-of-00001.parquet` (585 × 8)

| Column | Notes |
|---|---|
| `QID` | int, source identifier, used as `source_id` |
| `question` | patient question (verbatim) |
| `presupposition_correction` | reference correction of the false premise |
| `category` | myth category (e.g. `no treatment`) |
| `from_model` | `manual` or generating model |
| `cancer` | cancer type |
| `source`, `source_myth` | provenance of the question and the myth |

### Cancer-Myth NFP controls: `data/raw/cancer-myth-nfp/nfp-00000-of-00001.parquet` (150 × 7)

| Column | Notes |
|---|---|
| `QID` | int, starts at 1000 |
| `question` | patient question |
| `non_presupposition` | free-text explanation (**check its meaning before using it as a control label**; some rows read like a presupposition description) |
| `from_model`, `cancer`, `source_type`, `source_row` | provenance |

UPHILL and CARE-Bench are not in the repo yet. The UPHILL mapping (false claim → positive, true claim →
control, Level 2 queries, no mixture or fabricated claims) is in amendment §1. The CARE-Bench mapping is
still an open team decision (annotation guide §11.1). **Licensing:** confirm the redistribution terms before committing source
data or derived paraphrases to this repo (guide §10).

---

## 5. Processed data formats

Use **JSONL** (one object per line, UTF-8) for everything in `data/processed/` and `outputs/`. It diffs
cleanly and streams easily. Field names follow the annotation guide. These schemas are v1 drafts owned by
Advait; changes go through a PR.

### 5.1 Original question: `data/processed/questions.jsonl`

One row per original question. Fields are from annotation guide §3.

| Field | Values |
|---|---|
| `candidate_id` | Stable pool ID: `{onc\|ph\|tri}-{source_id}`, e.g. `onc-cm-412` |
| `group_id` | `{onc\|ph\|tri}-{nnn}`, e.g. `onc-007`. Assigned at sampling (`null` in candidate pools). Stable, never reused |
| `domain` | `oncology`, `public_health`, `acute_triage` |
| `task` | `correction`, `escalation` |
| `is_control` | bool (NFP / no-target-premise control) |
| `source_dataset`, `source_id` | `cancer_myth`, `cancer_myth_nfp`, `uphill`, `care_bench`; e.g. `cm-412` |
| `original_question` | verbatim |
| `source_label_raw` | the label as supplied by the dataset |
| `gold_label_mapped` | study label: `correction_required` / `no_correction` / `0A`–`1B` |
| `target_false_premise` | string for positive correction items, otherwise null |
| `source_evidence` | link / passage ID / source field |
| `eligibility_status`, `eligibility_reason` | `include` / `exclude` / `hold` + reason (`pending` allowed in candidate pools only) |
| `review_status` | `pending`, `independently_reviewed`, `adjudicated` |
| `source_meta` | Object of source-specific fields for screening and stratified sampling, or `null` |

The schema and validator live in `src/data/schema.py`. Candidate pools in `data/processed/candidates/`
use the `candidates` stage, and `data/processed/questions.jsonl` uses the `final` stage:
`python -m src.data.validate [--stage final] <files>`.

### 5.2 Variant: `data/processed/variants.jsonl`

One row per variant, including the original. Labels are inherited from the group and never re-labeled.

| Field | Values |
|---|---|
| `group_id` | |
| `variant_id` | `original`, `p1`, `p2`, `p3` |
| `text` | prompt text sent to the model |
| `generator` | `source`, `manual`, or exact model ID |
| `validity` | `valid`, `invalid`, `needs_review` (guide §6) |
| `validity_reason`, `verified_by` | |

### 5.3 Response: `outputs/responses/{run_id}/{model_key}.jsonl`

Responses are **write-once**. To fix a problem, start a new `run_id` (`run-YYYYMMDD-tag`).

`response_id`, `run_id`, `group_id`, `variant_id`, `response_text`, `error`. Jev rows have
`response_text: null` and add `decision` (the returned `choice`), `probabilities` and `confidence`. Model identity and settings
(`model_key`, exact `model_version`, `prompt_version`, temperature/seed/max_tokens, tokens, latency, cost,
timestamp) go in a **separate metadata table** keyed by `response_id`. That way reviewers and the mapper
stay blind (guide §8).

### 5.4 Annotation: `outputs/annotations/{run_id}/{scorer}.jsonl`

These are exactly the guide §8 fields: `response_id`, `group_id`, `variant_id`, `task`, `reviewer_id`,
`correction_observed`, `accommodation_observed`, `overcorrection_observed`, `escalation_observed`,
`response_conflict_flag`, `evidence_quote`, `annotation_notes`, `guide_version`. Disagreements go in a
separate log (guide §7.3). Original independent decisions are never overwritten.

---

## 6. Conventions

- **Branches and PRs:** branch from `main` and open a PR with one review. Changes to schemas, prompts,
  labels or scoring logic need review from the owner of that stage.
- **Commit messages:** use `type: summary`, matching the existing history (`data:`, `docs:`, `chore:`,
  `feat:`, `fix:`).
- **Raw data is immutable.** Transformations live in code and write to `data/processed/`.
- **Freeze before production:** question text, label mapping, IDs, guide version, prompts and model
  settings are frozen before the full run. Tag the repo (for example `data-v1.0`).
- **Prompts** are versioned files in `prompts/`. Never edit a version that has already been used.
- **Secrets:** API keys go in `.env`, which is git-ignored. Never commit keys or raw provider credentials.
- **Runner:** cache by (model version, prompt version, params, variant), retry on rate limits, and record
  tokens, latency and cost.
- **Blinding:** the scoring mapper and human reviewers never see model identity, paraphrase style or gold
  labels while scoring.
- **Thresholds:** expand human review if kappa < 0.80 or mapper class recall < 0.85.
- **Tests:** schema validation and metric unit tests go in `tests/`.

## 7. Open decisions (from annotation guide §11)

1. Raw-field-to-gold mapping for Cancer-Myth, NFP and CARE-Bench, plus confirming UPHILL Level 2 (Advait + Win)
2. Edge cases for overcorrection/accommodation and for vague or conditional referrals
3. How `unscorable` responses, incomplete groups and controls enter the GIR denominator
4. Adjudicators, audit stratification, and how kappa and recall are computed
5. Licensing and redistribution of source datasets in this repo (UPHILL has no stated licence)
6. Jev early-access timing and whether to fall back to Gemini 3.8 Flash if it isn't ready by the end of Week 2

## 8. Weekly rhythm

Monday 30-minute planning meeting · midweek async status and blocker check · end-of-week integration
session that demos a working artifact.
