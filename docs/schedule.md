# Schedule and Task Board

**Maintained by:** Advait (project & data lead). Update this at the end-of-week integration session.
**Last updated:** 26 October 2026

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
| 1 | Annotation guide v1 | Win | ✅ draft (PR-reviewed) |
| 1 | Dataset template + schema validator | Advait | ✅ `src/data/schema.py` |
| 1 | Repository scaffold + codebase guide | Advait | ✅ |
| 1 | Protocol v1.1 amendment (UPHILL, Jev) | Advait | ⏳ awaiting team approval |
| 1 | Sampling plan (240 groups) | Advait | ✅ code ready (`src/data/sample.py`); needs screening |
| 1 | Engineering lead verifies endpoints, Jev access | Engineering lead | ⬜ |
| 1 | 5-question end-to-end pilot | Bruce (Advait supplies questions) | ⬜ needs 5 screened questions |
| 2 | Screen candidates; 240 labeled originals | Advait + Win | ⬜ |
| 2 | Annotation disagreement log; final rubric | Win | ⬜ |
| 3 | 3 verified paraphrases per group | Sahil + Win | ⬜ |
| 3 | Working harness, dry run | Engineering lead + Bruce | ⬜ |
| 4 | Dataset + settings frozen and tagged (`data-v1.0`) | Advait | ⬜ |
| 4 | Full run; mapper + 20% human audit | Engineering lead, Bruce, reviewers | ⬜ |
| 5 | Analysis; optional mitigation if gates pass | Bruce, all | ⬜ |
| 6 | Report + presentation | All | ⬜ |

## Advait's next actions

1. **Screen for the pilot first** (unblocks Week 1). Run `python -m src.data.screening export` and review the
   top of each sheet with Win until these are `include`: 1 oncology positive (write its
   `target_false_premise`), 1 oncology NFP control, 1 public-health positive, 1 triage `1B`, 1 triage `0A`.
   Then run `python -m src.data.sample --pilot` and hand `data/processed/pilot.jsonl` to Sahil and Bruce.
2. **Full screening with Win.** Work down each sheet in order. `auto_flags` are hints only. Two independent
   reviewers on at least 20–25% (protocol). Import in batches; check progress with `python -m src.data.sample --report`.
3. **Exclude** `onc-cm-419` / `onc-cmnfp-1007`: Cancer-Myth contains the same question as both a positive
   and an NFP control (flagged `duplicate_conflicting_labels`).
4. **Get team sign-off** on the v1.1 amendment and the open decisions below.
5. **Freeze and tag** the dataset (`data-v1.0`) once `python -m src.data.sample` writes all 240 groups.

## Open decisions

| Decision | Owner | Needed by |
|---|---|---|
| Approve protocol v1.1 (UPHILL, Jev decision-only, Gemini fallback if no Jev access by end of Week 2) | All | Before screening `public_health` |
| UPHILL presupposition level: confirm Level 2 | Advait + Win | Before screening `public_health` |
| CARE-Bench `1B` shortage (22 in round 1 for 20 needed): allow multi-turn prefixes if more than 2 are excluded? | Advait + Win + Sahil | During screening |
| Who is the engineering lead (Member 4)? | All | Now |
| `unscorable` responses and the GIR denominator; controls in GIR (annotation guide §11) | Win + Bruce | Before the full run |
| Redistribution: Cancer-Myth / UPHILL author permission before any public data release | Advait | Before release |

## Weekly rhythm

- **Monday:** 30-minute planning meeting. Walk this board.
- **Midweek:** async status and blocker check.
- **End of week:** integration session that demos a working artifact, then update this file.
- PRs that change schemas, prompts, labels or scoring need review from that stage's owner.
