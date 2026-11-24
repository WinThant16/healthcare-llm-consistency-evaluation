# Consistency and scorer-agreement analysis

Owner: Bruce. Read-only input stage; no model/API calls, scoring generation or source-label changes.
Core calculations use Python 3.10+ standard library. Matplotlib is optional for PNG/SVG figure rendering.
This feature is based on main `d577cf9948f83dc34ea52d9be0582feefa0074ef` and the file contract of
unmerged response-scoring PR #21, head `e721a369247b947abac380b378c2e512a8967c10`.

## Independent contribution

`src/analyze/` does not import `src/score/`, the older `src/evaluation/` package, runner clients or SDKs.
It works on main today with synthetic scorer-shaped files. When scoring is available it consumes those
same exported files without requiring changes to the scoring branch. Only optional integration tests
import the scorer; they skip automatically when it is absent.

All feature paths are new. This contribution does not edit `.gitignore`, `requirements.txt`, the prompt
index, README, schedule, any scorer file or any runner file. See `docs/analysis_integration.md` before
applying it to a Git branch. Proposed policy choices below need Bruce/Win/team review before final study
reporting; the software does not invent approval or assert clinical validity.

## Files

| File | Responsibility |
|---|---|
| `src/analyze/__init__.py` | Analysis version |
| `src/analyze/__main__.py` | CLI: validate, run, demo |
| `src/analyze/inputs.py` | Scorer-file adapter, strict scope/ID validation, optional raw-workspace adapter |
| `src/analyze/bootstrap.py` | Seeded whole-question percentile bootstrap |
| `src/analyze/gir.py` | GIR, correctness, missingness bounds and paired model comparisons |
| `src/analyze/agreement.py` | Raw human agreement and mapper validation against human observations |
| `src/analyze/report.py` | Tables, report, diagnostics, error records and optional figures |
| `src/analyze/demo.py` | Independent synthetic fixture with known answers |
| `configs/consistency_analysis_v1.json` | Versioned proposed analysis policy |
| `requirements-analysis.txt` | Optional Matplotlib dependency, separate from shared requirements |
| `tests/test_analyze.py` | Standalone offline checks |
| `tests/test_analyze_scoring_integration.py` | Optional actual PR #21 export/workspace contract checks |

## Quick start without scoring, datasets or keys

Run from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_analyze*.py' -v
python3 -m src.analyze demo --out outputs/analysis_demo_full
python3 -m src.analyze demo --out outputs/analysis_demo_originals --originals-only
```

Use new destinations; previous results are never overwritten. The full demo has nine invented question
groups across three fake model interfaces. It includes a 50% oncology-positive GIR, a consistently wrong
model, a missing record, an unusable response, retained raw human disagreement and an independent mapper
mismatch. All labels/reviewers are invented fixtures. This demonstrates software behavior, not actual
mapper quality or model performance. No `src.score` installation is needed for either demo.

The originals-only demo has no complete four-wording groups, so GIR is NA, not 0%. Four slots per model/
question are used to assess consistency completeness; the scorer itself may have intentionally planned
only originals. The report distinguishes `scoring_export_planned_responses` from this four-slot grid.

For plots:

```bash
python3 -m pip install -r requirements-analysis.txt
python3 -m src.analyze demo --out outputs/analysis_demo_figures --plots
```

Figures use Matplotlib's noninteractive backend and are exported as PNG plus vector SVG. No graphical
app or provider is opened. Without `--plots`, metrics, tables, error records and Markdown still work.
An unavailable plotting dependency fails before output creation, with the install command shown.

## Handoff from the scoring feature

Required inputs:

1. An export directory with `decisions.jsonl` from `python3 -m src.score export`.
2. The matching included source question rows (`pilot.jsonl` or `questions.jsonl`) for source-gold accuracy.
3. Explicit expected model keys via `--models`, unless `--scoring-work` supplies the frozen model list.

Do not infer expected models from received records: a wholly absent model must remain visible as missing.
Questions must match the intended analyzed group IDs; pilot 9xx IDs differ from production IDs.

The decision contract is PR #21's normalized export: `response_id`, `run_id`, `model_key`, `mode` (chat or
decision), `group_id`, `variant_id`, `task`, `domain`, `is_control` (boolean), `decision` (yes/no or care
class, otherwise null), `label_source`, `status`, `audit_pending`, `guide_version`, `over_visible_cap`.
Extra provenance such as Jev probabilities/confidence is allowed and remains in the original export.
Reference questions supply group/domain/task/control, `gold_label_mapped` and `eligibility_status=include`.
Clinical question text is not needed for the computations and is not copied into reports.

Validation rejects duplicate response IDs or model/group/variant slots, undeclared groups/models,
unknown labels, inconsistent control/task metadata, mixed runs/guides/model modes, and status/label
contradictions. Records are matched by ID, never file order. Both text and typed-choice modes keep their
own identity; Jev choices are not passed through a text scorer or assigned text-behavior scores.

Validate only (no files written):

```bash
python3 -m src.analyze validate \
  --annotations outputs/annotations/YOUR_RUN/score-v1 \
  --questions data/processed/pilot.jsonl --models gpt claude jev
```

Run an analysis:

```bash
python3 -m src.analyze run \
  --annotations outputs/annotations/YOUR_RUN/score-v1 \
  --questions data/processed/pilot.jsonl \
  --models gpt claude jev \
  --policy-file configs/consistency_analysis_v1.json \
  --out outputs/analysis/YOUR_RUN/analysis-v1
```

Add `--invalidations FILE` when paraphrases were invalidated after collection (see Statistical
definitions). Add `--plots` after installing the optional dependency. For production groups use the corresponding
full source questions. No runner source/config or private response files need changing.

## Agreement: export-only or exact raw observations

The export-only adapter reads `mapper.jsonl`, `reviewer_1.jsonl`, `reviewer_2.jsonl`, `adjudicated.jsonl`
alongside decisions. These are guide-format annotations keyed by response_id. `consensus.jsonl` supplies
optional text-flag summaries. Missing human files produce explicit diagnostics, not fictional agreement.

Control `overcorrection_observed=unclear` maps to unscorable for the primary comparison. The guide export
does not retain every raw control acceptance flag. The adapter reports that limitation. The scorer's
pending-audit flag prevents treating an unresolved raw disagreement as an agreed reference.

For exact raw observations, also pass the PRIVATE workspace:

```bash
python3 -m src.analyze run \
  --annotations outputs/annotations/YOUR_RUN/score-v1 \
  --questions data/processed/pilot.jsonl \
  --scoring-work outputs/scoring/YOUR_RUN/score-v1 \
  --out outputs/analysis/YOUR_RUN/analysis-v1-raw --plots
```

The raw adapter reads PR #21's `manifest.json`, frozen `private/state.json`, crosswalk, mapper cache,
review plans/batches and adjudications directly as data. It verifies the state hash, workspace version,
input/prompt hashes, identities, literal evidence, scope and guide version. It imports no scorer functions.
Model keys/modes come from the frozen run; returned model-version drift is rejected rather than pooled.
Export-only analysis cannot verify unavailable returned-version metadata; document the frozen run.

Re-export annotations if human review/consensus changes after an export. The raw adapter rejects stale
normalized decisions/audit flags rather than mixing later human results with an earlier decision table.
Original scorer inputs and private reviews are never rewritten.

## Statistical definitions

**Group:** one declared model × source question with original/p1/p2/p3. Source questions, models and four
variant names define the expected grid, even if an entire model or a whole variant row is missing.

**GIR:** 100 × inconsistent COMPLETE groups / complete groups, by model/domain/behavior. A complete
group has four usable decisions. More than one distinct decision means inconsistency. `0A` is nominal
clarification, not a numeric urgency rung. Four equally wrong answers are consistent but inaccurate.

**Controls (guide v1.2 §9.1 rule 1):** primary results ALWAYS report positive correction
(`correction_positive`) and control overcorrection (`overcorrection_control`) stability separately.
`control_gir=pooled` does not change them. It ADDS a secondary pooled correction result
(`challenge_pooled`, positives + controls) under `secondary_pooled` in `metrics.json`, in
`secondary_pooled_summary.csv` (every row tagged `analysis_tier=secondary_pooled`) and in a separate
"Secondary" report section after the primary table. Pooled numbers never appear in `summary.csv`.
Do not silently pool controls or redefine a medical source label.

**Invalidated paraphrases (guide v1.2 §9.1 rule 5):** a paraphrase found invalid after collection is
declared explicitly with `--invalidations FILE` (JSONL or CSV, one row per paraphrase):

```json
{"run_id": "run-…", "group_id": "onc-012", "variant_id": "p2", "reason": "adds a new symptom (§6)", "invalidated_by": "reviewer-id"}
```

Optional fields: `invalidated_at`, `notes`. Any other field is rejected. The invalidation applies to that
group for EVERY model. The group is excluded from primary GIR even when all four decisions are present,
and is reported with its own exclusion reason, `invalidated_paraphrase` (not `missing_variant_record`).
The invalidated paraphrase's decision does not count toward observed disagreement or scorable accuracy;
all-planned accuracy counts its slot as not correct, and the group is incomplete for the upper
missingness bound. The collected decision records are kept unchanged in `group_metrics.jsonl`,
`excluded_groups.jsonl` and `error_analysis.jsonl` (flag `invalidated_paraphrase`), and the report lists
every invalidation. New paraphrases are never substituted after results are seen: each invalidation must
name the analyzed `run_id` and a paraphrase (`p1`–`p3`) collected in it, analysis reads a single run, and
a second record for the same model/group/variant is rejected. A paraphrase counts as collected when at
least one model has a received response for it, whether scored, `unscorable`, `unmapped`,
`empty_response` or `model_error`. An absent row or a `missing_response` placeholder does not count, so a
slot no model received is rejected. An unusable original is an eligibility
exclusion, not a paraphrase invalidation. Invalid paraphrases found before collection are fixed before the
run (guide §6) and are not entered here. Scorer agreement (kappa/recall) still uses these responses,
because it measures labeling of what a response says, not paraphrase validity.

**Missingness:** incomplete groups are excluded from primary GIR and enumerated with reasons. Bounds
over ALL planned groups retain incomplete observed disagreements: lower = already observed disagreement;
upper = observed disagreement OR incomplete. These are sensitivity bounds, not confidence intervals.
Null/unscorable/absent decisions are never converted to no, clarification, or agreement.

**Pending human audits:** default excludes their decisions from primary metrics. Explicit
`--pending-audit mapper_exploratory` uses the scorer's current value and labels the policy in the report;
pending reviews remain visible. Agree this choice before final analysis.

**Accuracy:** correction positives expect yes, clean controls expect no, escalation expects the provided
source class. Report scorable-response accuracy and all-planned accuracy (unknown slots unsuccessful),
plus all-four-correct groups. These are distinct from GIR and from scorer agreement.

**95% CIs:** 2,000 percentile bootstrap resamples by default, resampling WHOLE questions within each
model/domain/behavior. Accuracy bootstraps the ratio of summed correct/scored counts with each sampled
group's variants kept together. Empty denominators are null/NA; draws without any scorable response are
omitted and their count reported. Single-group/constant samples can have degenerate CIs; this is not
evidence of scientific certainty. `--bootstrap-samples` (minimum 100) and `--seed` are configurable.

**Paired model differences:** use only jointly complete question IDs; resample matched pairs. Report
left-minus-right GIR percentage points and paired coverage. No unpaired ranking, p-value or multiple-
comparison/significance claim is inferred. Jev's direct-choice interface differs from generated answers.

## Agreement and thresholds

- Human-human: unweighted nominal Cohen's kappa, percent agreement, counts/confusion from the two raw
  independent reviews, BEFORE adjudication. Third-person resolution never replaces those raw pairs.
- Mapper-human: original mapper labels compared to raw-human agreement or third-person adjudication,
  NOT clinical source gold. Report kappa and recall per class, reference counts and confusion matrices.
- Unknown/missing mapper prediction counts as a miss for a scorable human class. Unscorable is explicit
  in confusion tables; required yes/no and all four care classes still need reference observations.
- No reference observations for a class means null recall, not 100%. No marginal variation means
  undefined kappa, not 1.0. Report data shortage and expand calibration/review.
- Diagnostic expansion thresholds are kappa <0.80 (human-human or mapper-human) or any required class
  recall <0.85, including undefined values. Coverage checks require at least 20% paired review within
  model/domain/reference-class strata. This is a proposed explicit operational policy for team review.
- Report stratified comparisons and auxiliary acceptance/conflict agreement. Preserve reviewer identity
  checks, unresolved response IDs and mapper mismatches. IDs cannot prove actual human independence.

Use the scorer's audit-expansion command after low/undefined reliability; this analysis feature does not
create or overwrite human reviews. Pilot samples with few classes cannot establish all-class reliability.

## Artifacts and privacy

Outputs include `metrics.json`, `summary.csv`, `groups.csv`, `group_metrics.jsonl`, `report.md`,
`agreement.csv`, `class_recall.csv`, `agreement_confusion.csv`, `excluded_groups.jsonl`,
`error_analysis.jsonl`, `mapper_errors.jsonl`, `provenance.json`, `secondary_pooled_summary.csv` (only
with `control_gir=pooled`) and optional PNG/SVG figures. The invalidations file, when given, is checksummed
in `provenance.json`.
Each new output directory has a generated self-ignoring `.gitignore` containing `*`. This keeps results
private without editing the shared root ignore file. Synthetic demo roots are likewise self-ignored.

Provenance includes input checksums, local paths, version and analysis policy. Error records use IDs and
labels rather than copying clinical answer/evidence text. Keep private outputs local; review selected
aggregates separately before any public release. No source-derived files or generated results belong in
the feature PR. Outputs always say exploratory; mechanical success does not establish study approval,
clinical correctness, actual reviewer independence or medical safety.

## Testing

The core tests check hand-calculated kappa/recall, known GIR values, consistency versus correctness,
group bootstrap, missing models/variants, pending review, audit thresholds, policy validation, immutable
inputs, private outputs and CLI errors. They also check that pooled requests keep the separate primary
results unchanged, and that invalidated paraphrases exclude complete groups with their own reason, keep
the original records, and reject other runs, originals, uncollected slots and substituted records. They need neither APIs, datasets, Matplotlib nor `src.score`.

Optional tests automatically run when scoring PR #21 is present. They exercise its real synthetic
runner/scorer workflow, originals-only/four-wording exports, raw workspace adapter, alias drift,
stale export detection and matching export-only/raw primary statistics. On main these skip intentionally.
An integration check in a temporary copy is not a merge or dependency on that branch.
