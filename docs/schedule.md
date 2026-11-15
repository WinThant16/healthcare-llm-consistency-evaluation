# Schedule and Task Board

**Maintained by:** Advait (project & data lead). Update this at the end-of-week integration session.
**Last updated:** 15 November 2026

> **Fixed dates:** midterm report 15 Nov, **poster session 30 Nov** (needs complete results), **final report 7 Dec**. 

All 240 original questions have been screened, and the paraphrase pipeline (#18) and model runner (#19) are now merged. The five-question paraphrase pilot is complete, with all 15 paraphrases independently reviewed and marked valid. The model runner has also been tested on the five original pilot questions across all three models. The remaining work includes second-round screening, full-scale paraphrase generation, and scoring. The proposed timeline below will be reviewed at the November 16 meeting, along with any necessary adjustments to the project scope.

## Owners

| Member | Role | Owns |
|---|---|---|
| Advait | Project & data lead | Scope, dataset acquisition, schemas, schedule, master dataset, weekly coordination |
| Win | Clinical-labeling lead | Labeling rubric, gold labels, evidence, adjudication, annotation guide |
| Sahil | Paraphrase & quality lead | Paraphrase generation, meaning-preservation checks, QC report |
| Abhi | Engineering lead | Model APIs (incl. Jev early access), prompt templates, experiment runner |
| Bruce | Evaluation & analysis lead | Mapper/scoring, agreement stats, GIR, CIs, figures |

## Milestones

| Week | Milestone | Owner(s) | Status | Target |
|---|---|---|---|---|
| 1 | Protocol v1.0 frozen | All | ✅ 5 Oct | |
| 1 | Annotation guide v1 → v1.1 | Win | ✅ (#9) | |
| 1 | Dataset template + schema validator | Advait | ✅ `src/data/schema.py` | |
| 1 | Repository scaffold + codebase guide | Advait | ✅ | |
| 1 | Protocol v1.1 amendment (UPHILL, Jev) | Advait | ✅ approved (#10) | |
| 1 | Sampling plan (240 groups) | Advait | ✅ `src/data/sample.py` | |
| 1 | Engineering lead verifies endpoints, Jev access | Abhi | ✅ 15 Nov: `gpt-6-astra`, `claude-opus-5`, Jev (`jev-latest` → `jev-1.14.0`) | |
| 1 | 5-question pilot set | Advait + Win | ✅ `data/pilot_manifest.csv` (#8) | |
| 1 | 5-question end-to-end pilot run | Bruce + Abhi | 🟡 originals run on all three models (`run-20261115-pilot-v2`); pilot paraphrases reviewed (#18), four-variant run and scoring pending | wk of 16 Nov |
| 2 | Screen candidates; 240 labeled originals | Advait + Win | ✅ all targets met (#11, #12, #13) | |
| 2 | Second review of 20–25% + adjudication | Advait (second reviewer), Win (adjudication) | ⬜ **next**; CSV workflow ready, review app (#17) pending save-lock fix | wk of 16 Nov |
| 2 | Annotation disagreement log; final rubric | Win | ⬜ after second review | wk of 16 Nov |
| 2 | Response review template | Win | ✅ `data/templates/response_review_TEMPLATE.csv` (#14) | |
| 3 | 3 verified paraphrases per group | Sahil + Win (+ third reviewer) | 🟡 pipeline merged (#18), pilot 15/15 valid; full 720 waits on `data-v1.0` and the generator choice | 16–23 Nov |
| 3 | Working harness, dry run | Abhi + Bruce | ✅ runner merged (#19), run on the pilot originals; Bruce to review the response format | |
| 4 | Dataset + settings frozen and tagged (`data-v1.0`) | Advait | ⬜ after adjudication | wk of 16 Nov |
| 4 | Full run; mapper + 20% human audit | Abhi, Bruce, reviewers | ⬜ not started (`src/score/`) | wk of 23 Nov |
| 5 | Analysis (GIR, CIs, figures); optional mitigation if gates pass | Bruce, all | ⬜ | by 30 Nov |
| 5 | Midterm report | All | 🟡 due 15 Nov, 11:59 PM | **15 Nov** |
| 6 | Poster session | All | ⬜ | **30 Nov** |
| 6 | Final report | All | ⬜ | **7 Dec** |

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
2. **Fix #17** (lock around `save()`, see Win's review) to enable the browser-based review workflow. The CSV workflow remains available in the meantime.
3. **Freeze and tag `data-v1.0`** once adjudication is imported and `python -m src.data.sample` writes all 240 groups.
4. **Settle the open decisions below.**
5. **Ask the Cancer-Myth and UPHILL authors for permission** before any public data release.

**Win**
1. Adjudicate second-review disagreements (`python -m src.data.screening adjudicate`).
2. Re-review #17 once the lock fix is in.
3. With Bruce: `unscorable` responses and the GIR denominator (guide §11); score the pilot responses.
4. Coordinate meaning-preservation review of the full 720 paraphrases with Sahil and any additional reviewer, following the agreed review split.

**Sahil**
1. Share the pilot `variants.jsonl` with Abhi (privately) for the four-variant pilot run.
2. Generate the full 720 after `data-v1.0` and the generator choice; review with Win; QC report.
3. Add a rule check for content words that disappear (no current rule catches a dropped symptom).

**Abhi**
1. Get Bruce's review of the response and metadata format (`outputs/responses/{run_id}/`, codebase.md §5.3)
   and the team's review of the prompts (`prompts/patient_system_v2.txt`, `prompts/jev_questions_v1.json`).
2. Rerun the pilot with all four variants (Sahil shares `data/processed/paraphrase/pilot/variants.jsonl`).
   Pin `jev-1.14.0` in `config.py` before the full run.
3. Freeze prompts and settings with `data-v1.0`, then the full run (2,880 requests).

**Bruce**
1. Scoring mapper, human-audit stats (kappa, class recall), GIR with bootstrap CIs, figures (`src/score/`, `src/analyze/`).
2. Pilot end-to-end run with Abhi: score `run-20261115-pilot-v2` (Abhi shares it; responses are not committed).

## Open decisions

| Decision | Owner | Needed by |
|---|---|---|
| 512 visible output tokens can't be capped by either chat API (the cap covers reasoning too). The runner asks for under 200 words and flags longer replies (`over_visible_cap`); accept this? | Abhi + Bruce | Before the full run |
| Confirm the proposed target weeks and any scope cuts (audit size, mitigation) given the 30 Nov poster | All | Mon 16 Nov |
| Paraphrase generator and judge models (ideally differ from the three evaluated models) | All | Mon 16 Nov |
| Third reviewer for the 720 paraphrases | All | Mon 16 Nov |
| CARE-Bench `1B` margin: only one spare; allow multi-turn prefixes if adjudication excludes 2+? | Advait + Win + Sahil | During adjudication |
| `unscorable` responses and the GIR denominator; controls in GIR (annotation guide §11) | Win + Bruce | Before the full run |
| Redistribution: Cancer-Myth / UPHILL author permission before any public data release | Advait | Before release |

Resolved: Abhi is the engineering lead; Jev access confirmed, so no Gemini fallback; v1.1 approved (#10); UPHILL Level 2 confirmed; `onc-cm-419` / `onc-cmnfp-1007` conflict handled in screening.

## Weekly rhythm

- **Monday:** 30-minute planning meeting. Walk this board.
- **Midweek:** async status and blocker check.
- **End of week:** integration session that demos a working artifact, then update this file.
- PRs that change schemas, prompts, labels or scoring need review from that stage's owner.