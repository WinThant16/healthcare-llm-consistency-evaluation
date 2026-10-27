# Healthcare LLM Consistency Evaluation

Do healthcare LLMs make the **same safety decision** when the same patient question is reworded?

We test two behaviors under meaning-preserving paraphrases:

- **Presupposition correction:** does the model explicitly correct a false or harmful assumption built into
  the question?
- **Care escalation:** does the model recommend the right level of care (clarify, self-care, nonurgent, urgent)?

**Team:** Abhi Desai · Advait Chalke · Bruce Nguyen · Sahil Gupta · Win Thant Tin Han

## Study design

Protocol v1.0 (5 Oct 2026), as amended by the proposed v1.1.

| Domain | Behavior | Source | Sample |
|---|---|---|---|
| Oncology | Correction | Cancer-Myth | 60 positives + 20 physician-confirmed NFP controls |
| Public health | Correction | UPHILL (v1.1; replaces Pregnant Questions) | 60 false claims + 20 true-claim controls |
| Acute symptom triage | Escalation | CARE-Bench v1.1 | 80 cases, 20 per action class (`0A` `0B` `1A` `1B`) |

- **Scale:** 240 question groups. Each group is one original plus 3 paraphrases, so 960 prompts per model.
- **Models:**
  - GPT-6 Astra and Claude Opus 5, both free-text chat
  - Jev (TypeSafe AI), which replaces Gemini 3.8 Flash in v1.1 and runs in decision-only mode
- **Primary metric:** the **Group Inconsistency Rate (GIR)**, the share of question groups that get more
  than one decision across their four versions. Reported by model, domain and behavior, with bootstrap 95% CIs.
  Accuracy against gold labels is reported separately.
- **Scoring:** a blinded automated mapper, two human reviewers on the pilot and on a stratified 20% audit, and
  third-reviewer adjudication.

## Status

| Stage | Owner | State |
|---|---|---|
| Raw data acquisition | Advait | ✅ Cancer-Myth and CARE-Bench committed; UPHILL fetched by script |
| Candidate pools, schema, validator | Advait | ✅ |
| Screening tools, sampler, pilot selector | Advait | ✅ code ready, awaiting human screening |
| Gold labels and annotation guide | Win | ✅ guide v1; labeling in progress |
| Paraphrases | Sahil | ⬜ |
| Model runner (incl. Jev access) | Engineering lead | ⬜ |
| Scoring and analysis | Bruce | ⬜ |

The task board with milestones and open decisions is in [`docs/schedule.md`](docs/schedule.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
bash data/fetch_raw.sh              # downloads UPHILL (not committed) and verifies all checksums
python -m src.data.build_pools      # raw data → data/processed/candidates/{domain}.jsonl
python -m pytest tests              # schema, loader, screening and sampling tests
```

### From candidates to the 240 groups

```bash
python -m src.data.screening export        # review sheets → data/processed/screening/
# reviewers fill eligibility_status / eligibility_reason / target_false_premise / reviewed_by / review_status
python -m src.data.screening import <csv>  # validate + merge → data/decisions/{domain}.csv (committed)
python -m src.data.sample --pilot          # 5-question pilot → data/processed/pilot.jsonl
python -m src.data.sample --report         # included vs. target per label
python -m src.data.sample                  # → data/processed/questions.jsonl + data/questions_manifest.csv
```

[`data/README.md`](data/README.md) covers every source's fields, licences, label counts and screening caveats.

## Documentation

| Document | What it covers |
|---|---|
| [`codebase.md`](codebase.md) | **Start here.** Pipeline, ownership, data formats, conventions |
| [`docs/protocol_v1.0.pdf`](docs/protocol_v1.0.pdf) | Week 1 study design freeze |
| [`docs/protocol_v1.1_amendment.md`](docs/protocol_v1.1_amendment.md) | UPHILL and Jev changes (proposed) |
| [`docs/annotation_guide_v1.md`](docs/annotation_guide_v1.md) | Labeling rules, QC workflow, response annotation format |
| [`docs/schedule.md`](docs/schedule.md) | Task board, milestones, open decisions |
| [`docs/proposal.pdf`](docs/proposal.pdf) | Original proposal and execution plan (superseded where the freeze differs) |
| [`data/README.md`](data/README.md) | Datasets, candidate pools, screening and sampling |

## Project structure

```text
codebase.md    How the repo works (start here)
docs/          Protocol, amendment, annotation guide, schedule, proposal
data/
  raw/         Source datasets, never edited (UPHILL git-ignored; see fetch_raw.sh)
  decisions/   Screening decisions: IDs and decisions only (committed)
  processed/   Candidate pools, review sheets, questions.jsonl (git-ignored: source text)
src/data/      Loaders, schema + validator, pre-screen flags, screening, sampling
prompts/       Versioned model, Jev, mapper and paraphrase prompts
outputs/       Model responses, annotations, metrics, figures
tests/         Unit tests
report/        Final report
presentation/  Final presentation
```

## Data and licensing

This repository is public. To respect source terms:

- **CARE-Bench** (CC BY-NC 4.0) is committed with attribution.
- **Cancer-Myth** has no explicit dataset licence. Use it internally with citation, and don't redistribute
  derived questions or paraphrases without author permission.
- **UPHILL** has no stated licence. It isn't committed; `data/fetch_raw.sh` downloads it from the authors.
- Files containing source-derived text (candidate pools, review sheets, `questions.jsonl`) are git-ignored and
  rebuilt locally. Only IDs, decisions and manifests are committed.

Sources: Cancer-Myth ([arXiv:2504.11373](https://arxiv.org/abs/2504.11373)), UPHILL
([arXiv:2312.08800](https://arxiv.org/abs/2312.08800)), CARE-Bench
([arXiv:2608.03731](https://arxiv.org/abs/2608.03731)).

## Contributing

Branch from `main`, open a PR, and get one review. Changes to schemas, prompts, labels or scoring logic need
review from that stage's owner. Commit messages use `type: summary` (`data:`, `docs:`, `feat:`, `fix:`, `chore:`).
API keys go in `.env`, which is git-ignored.
