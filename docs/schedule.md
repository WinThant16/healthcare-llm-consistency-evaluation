# Schedule and Task Board

**Maintained by:** Advait (project & data lead). Update this at the end-of-week integration session.
**Last updated:** 25 November 2026

> **Fixed dates:** **poster session 30 Nov** (oncology results), **final report 7 Dec** (all three domains).
> Midterm report submitted 15 Nov.

The dataset is frozen at **`data-v1.0`** (#29): 240 groups, with the oncology second review (kappa 0.84,
#24) adjudicated (#28). Scoring (#21), analysis (#22) and annotation guide v1.2 (#23) are merged, and Jev is
pinned to `jev-1.14.0` (#25). **The poster covers oncology only** (80 groups × 4 wordings × 3 models =
960 requests). The final report covers all 240 groups. The critical path to 30 Nov is: choose the
generator, judge and mapper models, run the 240 oncology paraphrases and review them, then do the full
oncology run, scoring with a 20% audit, and analysis.

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
| 1 | Annotation guide v1 → v1.1 → v1.2 | Win | ✅ (#9, #23) | |
| 1 | Dataset template, schema validator, repo scaffold, sampling plan | Advait | ✅ | |
| 1 | Protocol v1.1 amendment (UPHILL, Jev) | Advait | 🟡 in effect (#10). Sign-off: Win, Abhi, Advait ✅; Sahil, Bruce ⬜ | |
| 1 | Endpoints and Jev access | Abhi | ✅ `gpt-6-astra`, `claude-opus-5`, Jev pinned `jev-1.14.0` (#25) | |
| 1 | 5-question pilot set | Advait + Win | ✅ (#8) | |
| 1 | 5-question end-to-end pilot run | Bruce + Abhi | 🟡 originals run on all three models; pilot paraphrases reviewed (#18). Four-wording run and pilot scoring not reported yet | **26 Nov** |
| 2 | Screen candidates; 240 labeled originals | Advait + Win | ✅ (#11, #12, #13) | |
| 2 | Oncology second review + adjudication | Advait, Win | ✅ kappa 0.84; 3 disagreements adjudicated (#24, #28) | |
| 2 | Public-health and triage second review | Advait (second reviewer), Win | ⬜ drafts exist, none confirmed. Feeds reported agreement only; any decision change needs `data-v1.1` | 5 Dec |
| 2 | Response review template; disagreement log | Win | ✅ (#14), guide §7.3 | |
| 3 | 3 verified paraphrases per group (oncology first) | Sahil + Win | 🟡 checks + prompt v2 in review (#27). Generator and judge not chosen | oncology **27 Nov** |
| 3 | Working harness, dry run | Abhi + Bruce | ✅ (#19) | |
| 4 | Dataset frozen and tagged | Advait | ✅ `data-v1.0` (#29) | |
| 4 | Scoring workflow + analysis package | Bruce | ✅ (#21, #22). Before real scoring: guide version → `annotation-v1.2`, mapper prompt vague-referral rule | |
| 4 | Full oncology run; mapper + 20% human audit | Abhi, Bruce, reviewers | ⬜ waits on oncology paraphrases and mapper choice | **28 Nov** |
| 5 | Oncology analysis (GIR, CIs, figures) | Bruce | ⬜ | **29 Nov** |
| 6 | Poster session | All | ⬜ | **30 Nov** |
| 6 | Public-health + triage paraphrases, runs, scoring, analysis | All | ⬜ | 5 Dec |
| 6 | Final report | All | ⬜ | **7 Dec** |

Screening result (`python -m src.data.sample --report`, included/target, effective decisions at `data-v1.0`):

| Domain | Strata |
|---|---|
| oncology | correction_required 67/60, no_correction 20/20 |
| public_health | correction_required 62/60, no_correction 22/20 |
| acute_triage | 0A 24/20, 0B 26/20, 1A 26/20, **1B 21/20** (only one spare) |

## Next actions

**Advait**
1. Send the frozen `questions.jsonl` privately to Sahil and Abhi (SHA-256 in `data/README.md`).
2. Chase the generator, judge and mapper choices (open decisions below).
3. Second review of public health and triage; Win adjudicates.
4. Final report: abstract and introduction with results, AI-drafted screening disclosure (guide §7.1).
5. Ask the Cancer-Myth and UPHILL authors for permission before any public data release.

**Win**
1. Adjudicate the public-health and triage second reviews when they come in.
2. Review the oncology paraphrases with Sahil (meaning preservation, guide §6).
3. Score the pilot responses with Bruce (two independent reviewers, guide §7.2).

**Sahil**
1. Get #27 reviewed and merged; tick the v1.1 amendment sign-off.
2. Generate and check the 240 oncology paraphrases from `data-v1.0` (prompt v2), then review with Win and write the QC report.
3. Remaining 480 paraphrases after the poster.

**Abhi**
1. Four-wording pilot run; get #26 reviewed.
2. Full oncology run (960 requests) once paraphrases are verified; share responses privately with Bruce.

**Bruce**
1. Set the scorer to `annotation-v1.2` and update the mapper prompt (guide §5.2 rule 8); tick the v1.1 amendment sign-off.
2. Score the pilot, then the oncology run with a 20% human audit; GIR, CIs and figures for the poster.
3. Follow-ups from #22: class-stratified escalation bootstrap; check the questions file against `data/questions_manifest.csv`.

## Open decisions

| Decision | Owner | Needed by |
|---|---|---|
| Paraphrase generator and judge models (not the evaluated models; judge ≠ generator). #27 trialled Claude Sonnet 5.5 + `gpt-6.1-terra` | Sahil + Abhi | **26 Nov** |
| Mapper model (not GPT-6 Astra, Claude Opus 5 or Jev; guide §7.2) | Bruce + Abhi | **26 Nov** |
| 512 visible output tokens can't be capped by either chat API; accept the under-200-words instruction plus the `over_visible_cap` flag? | Abhi + Bruce | Before the full run |
| Paraphrase reviewers for oncology (two people incl. Win) and a third reviewer for the remaining 480 | Win + Sahil | 26 Nov |
| CARE-Bench `1B` margin: only one spare if triage second review excludes 2+ | Advait + Win | Triage adjudication |
| Redistribution: Cancer-Myth / UPHILL author permission | Advait | Before release |

Resolved: Abhi is engineering lead; Jev pinned (#25); UPHILL Level 2; `unscorable`, GIR denominator and
control GIR (guide v1.2 §9.1); oncology-only poster scope; dataset frozen at `data-v1.0`.

## Weekly rhythm

- **Monday:** 30-minute planning meeting. Walk this board.
- **Midweek:** async status and blocker check.
- **End of week:** integration session that demos a working artifact, then update this file.
- PRs that change schemas, prompts, labels or scoring need review from that stage's owner.
