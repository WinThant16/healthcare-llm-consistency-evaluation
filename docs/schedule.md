# Schedule and Task Board

**Maintained by:** Advait (project & data lead). Update this at the end-of-week integration session.
**Last updated:** 9 November 2026

> **Behind schedule.** Protocol v1.0 was frozen on 5 October, so by the six-week plan the report is due
> soon. The 240 originals are screened, but the paraphrase, model-runner and scoring stages have not
> started. The engineering lead is still unconfirmed. Agree at the next Monday meeting on the remaining
> dates and whether to cut scope.

## Owners

| Member | Role | Owns |
|---|---|---|
| Advait | Project & data lead | Scope, dataset acquisition, schemas, schedule, master dataset, weekly coordination |
| Win | Clinical-labeling lead | Labeling rubric, gold labels, evidence, adjudication, annotation guide |
| Sahil | Paraphrase & quality lead | Paraphrase generation, meaning-preservation checks, QC report |
| Abhi? (to be confirmed) | Engineering lead | Model APIs (incl. Jev early access), prompt templates, experiment runner |
| Bruce | Evaluation & analysis lead | Mapper/scoring, agreement stats, GIR, CIs, figures |

## Milestones

| Week | Milestone | Owner(s) | Status |
|---|---|---|---|
| 1 | Protocol v1.0 frozen | All | ✅ 5 Oct |
| 1 | Annotation guide v1 → v1.1 | Win | ✅ (#9) |
| 1 | Dataset template + schema validator | Advait | ✅ `src/data/schema.py` |
| 1 | Repository scaffold + codebase guide | Advait | ✅ |
| 1 | Protocol v1.1 amendment (UPHILL, Jev) | Advait | ✅ approved (#10) |
| 1 | Sampling plan (240 groups) | Advait | ✅ `src/data/sample.py` |
| 1 | Engineering lead verifies endpoints, Jev access | Engineering lead | ⬜ **blocker**: lead unconfirmed |
| 1 | 5-question pilot set | Advait + Win | ✅ `data/pilot_manifest.csv` (#8) |
| 1 | 5-question end-to-end pilot run | Bruce + engineering lead | ⬜ needs runner |
| 2 | Screen candidates; 240 labeled originals | Advait + Win | ✅ all targets met (#11, #12, #13) |
| 2 | Second review of 20–25% + adjudication | Advait (second reviewer), Win (adjudication) | ⬜ **next** |
| 2 | Annotation disagreement log; final rubric | Win | ⬜ after second review |
| 2 | Response review template | Win | ✅ `data/templates/response_review_TEMPLATE.csv` (#14) |
| 3 | 3 verified paraphrases per group | Sahil + Win | ⬜ not started (`src/paraphrase/`) |
| 3 | Working harness, dry run | Engineering lead + Bruce | ⬜ not started (`src/run_models/`) |
| 4 | Dataset + settings frozen and tagged (`data-v1.0`) | Advait | ⬜ after adjudication |
| 4 | Full run; mapper + 20% human audit | Engineering lead, Bruce, reviewers | ⬜ not started (`src/score/`) |
| 5 | Analysis; optional mitigation if gates pass | Bruce, all | ⬜ |
| 6 | Report + presentation | All | ⬜ |

Screening result (`python -m src.data.sample --report`, included/target):

| Domain | Strata |
|---|---|
| oncology | correction_required 67/60, no_correction 21/20 |
| public_health | correction_required 62/60, no_correction 22/20 |
| acute_triage | 0A 24/20, 0B 26/20, 1A 26/20, **1B 21/20** (only one spare) |

## Next actions

**Advait**
1. **Second review.** `python -m src.data.screening export --second`, then review blind, `import --second`, `compare`.
   Hand `data/decisions/disagreements.csv` and `premise_differences.csv` to Win.
2. **Merge #15** (LF line endings for committed CSVs) after Win reviews it.
3. **Freeze and tag `data-v1.0`** once adjudication is imported and `python -m src.data.sample` writes all 240 groups.
4. **Settle the open decisions below**, the engineering lead and Jev/Gemini first.
5. **Ask the Cancer-Myth and UPHILL authors for permission** before any public data release.

**Win**
1. Adjudicate second-review disagreements (`python -m src.data.screening adjudicate`).
2. Review #15.
3. With Bruce: `unscorable` responses and the GIR denominator (guide §11).

**Sahil**
1. Start `src/paraphrase/` on the pilot set now (`data/processed/pilot.jsonl`, built locally after
   `data/fetch_raw.sh` and `python -m src.data.sample --pilot`). Run the full 240 after `data-v1.0`.
2. Meaning-preservation check with Win; QC report.

**Engineering lead (to be confirmed)**
1. Confirm model access: gpt-6-astra, claude-opus-5, Jev (decision-only). The amendment's Jev deadline
   (end of Week 2) has passed, so confirm Jev or switch to the Gemini fallback now.
2. Prompt templates and the experiment runner (`src/run_models/`). Agree the response format with Bruce first.

**Bruce**
1. Scoring mapper, human-audit stats (kappa, class recall), GIR with bootstrap CIs, figures (`src/score/`, `src/analyze/`).
2. Pilot end-to-end run with the engineering lead.

## Open decisions

| Decision | Owner | Needed by |
|---|---|---|
| Who is the engineering lead (Member 4)? | All | **Now** |
| Jev access confirmed, or switch to the Gemini fallback (v1.1 amendment) | Engineering lead + Advait | **Now** |
| Remaining dates and any scope cuts (models, audit size) given the timeline | All | Next Monday meeting |
| CARE-Bench `1B` margin: only one spare; allow multi-turn prefixes if adjudication excludes 2+? | Advait + Win + Sahil | During adjudication |
| `unscorable` responses and the GIR denominator; controls in GIR (annotation guide §11) | Win + Bruce | Before the full run |
| Redistribution: Cancer-Myth / UPHILL author permission before any public data release | Advait | Before release |

Resolved: v1.1 approved (#10); UPHILL Level 2 confirmed; `onc-cm-419` / `onc-cmnfp-1007` conflict handled in screening.

## Weekly rhythm

- **Monday:** 30-minute planning meeting. Walk this board.
- **Midweek:** async status and blocker check.
- **End of week:** integration session that demos a working artifact, then update this file.
- PRs that change schemas, prompts, labels or scoring need review from that stage's owner.
