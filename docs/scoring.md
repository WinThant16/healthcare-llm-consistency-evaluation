# Response scoring

Owner: Bruce. Runner-format review: Abhi. Clinical-rule review: Win.
Implements `feat/response-scoring` against main revision `d577cf9948f83dc34ea52d9be0582feefa0074ef`.
No new third-party packages are needed for this stage (Python 3.10+ standard library). The existing
runner/data dependencies are unchanged. All commands run from the repository root.

This stage consumes Abhi's runner files, labels individual responses, supports independent human
review and exports a handoff for `src/analyze/`. It does not compute GIR, kappa, recall or figures.
It does not rerun the patient models or change the paraphrases. Originals-only pilot runs work now;
four-wording completeness belongs to later GIR analysis.

## Files and responsibilities

| File | Purpose |
|---|---|
| `src/score/__init__.py` | Scorer and annotation-guide version identifiers |
| `src/score/__main__.py` | Command-line interface |
| `src/score/inputs.py` | Read actual runner outputs, bind exact input text, snapshot and report coverage |
| `src/score/schema.py` | Blinded observation validation and private guide-format adapter |
| `src/score/mapper.py` | Versioned mapper jobs, offline import, optional cached live JSON chat client |
| `src/score/review.py` | Independent packets, expansion, imports, disagreements and third-person adjudication |
| `src/score/export.py` | Guide annotations and normalized decision records for analysis |
| `src/score/demo.py` | Entirely synthetic integration example using the actual runner with fake clients |
| `prompts/response_mapper_v1.md` | Versioned, structured-output scoring instructions |
| `tests/test_score.py` | Offline unit and real-runner integration checks |

## Existing runner contract

Pass a run directory, not a newly assembled response file:

```text
outputs/responses/{run_id}/
    run.json
    metadata.jsonl
    gpt.jsonl
    claude.jsonl
    jev.jsonl
    errors.jsonl          # optional historical transport/API failures
```

- Response and metadata rows are joined by `response_id`, never their order. IDs/group/variant/model
  placement and one-run boundaries are checked; filenames come from frozen `run.json` model keys.
- Metadata uses the runner's `params`, `latency_s`, `cost_usd`, prompt version and returned model ID.
  Model mode is read from `run.json`: `chat` or `decision`. No invented metadata `settings`/`mode` fields
  or combined `all_models.jsonl` file are required.
- The runner request key is recomputed from frozen model/prompt/settings hashes and the exact question
  text. Incorrect private variants or modified questions are rejected. Today's runner settings are not
  substituted for the settings used to collect a run.
- Jev's `decision`, sparse/full probabilities and confidence are preserved; Jev skips text mapping and
  human response review. Unknown probabilities are never filled in with zeros.
- `error` on a response means a refusal/truncation or another unusable model outcome. It is retained as
  an unusable decision, not sent to the mapper. Empty chat text likewise has no inferred label.
- An API error-log row does not by itself mean a response is still missing: the runner may have filled
  the gap on a rerun. Missing slots are computed from planned inputs minus received response IDs.
- Replies flagged `over_visible_cap` are preserved and flagged, not truncated/dropped. The team must
  settle the 512-visible-token rule. Scorer code does not silently change that protocol rule.
- Prepare a workspace after the runner has finished writing. Mismatched response/metadata IDs fail
  clearly; the scorer never repairs or overwrites the original run.

## 1. Prepare the real originals-only pilot

Obtain Abhi's private `run-20261115-pilot-v2` directory and the matching five `pilot.jsonl` question rows.
The public repo has IDs/manifests, but source-derived question and response text is shared privately.

```bash
python3 -m src.score prepare \
  --run-dir outputs/responses/run-20261115-pilot-v2 \
  --questions data/processed/pilot.jsonl \
  --out outputs/scoring/run-20261115-pilot-v2/score-v1
```

For three models and five originals expect 15 planned records: 10 usable chat mapper jobs and 5 direct
Jev choices if all requests succeeded. No paraphrase file is required for originals-only scoring.

For a four-wording run, also pass the exact variants file used by the runner:

```bash
python3 -m src.score prepare \
  --run-dir outputs/responses/YOUR_FOUR_VARIANT_RUN \
  --questions data/processed/pilot.jsonl \
  --variants data/processed/paraphrase/pilot/variants.jsonl \
  --out outputs/scoring/YOUR_FOUR_VARIANT_RUN/score-v1
```

For production, use the full questions/variants files, verified targets and `--mode production`
(see the next section):

```bash
python3 -m src.score prepare --mode production \
  --run-dir outputs/responses/YOUR_PRODUCTION_RUN \
  --questions data/processed/questions.jsonl \
  --variants data/processed/variants.jsonl \
  --targets data/processed/scoring/targets_verified.jsonl \
  --out outputs/scoring/YOUR_PRODUCTION_RUN/score-v1
```

Pilot 9xx group IDs and production IDs are not interchangeable. Non-valid variants are skipped exactly as in the runner and counted explicitly in the
preparation summary. If a received response has no matching planned input, preparation fails.

The workspace snapshots all inputs privately and freezes the mapper prompt text. Subsequent runner
reruns do not mutate it: prepare a new scoring workspace to pick up later responses. Existing outputs
are never overwritten. `status --work ...` prints coverage without exposing response text.

## Target context and blind observation (guide v1.2)

The guide requires target-specific correction and blinding. Supplying a `target_false_premise` only
for positive items would reveal control status, so annotation guide v1.2 §4.4 (PR #23) gives every
correction group, controls included, a neutral assessment target and scores the blind observation
fields below. `GUIDE_VERSION` in `src/score/__init__.py` is `annotation-v1.2`. The mapper prompt and
reviewer instructions follow §4.4 and the §5.2 untimed-referral rule.

Each workspace freezes the guide version and mapper prompt it was prepared under, and its reviews,
mapper imports and exports keep that frozen version. Workspaces prepared before v1.2 stay
`annotation-v1.1+blind-observation-v1-proposed`: do not relabel or reuse them for v1.2 scoring.
After a guide or prompt change, prepare a fresh, uniquely named workspace.

**Verified targets are mandatory for production correction scoring** (guide v1.2 §4.4). There are two
scoring modes, chosen with `prepare --mode` and recorded in the workspace, `status`, every
`decisions.jsonl` row (`scoring_mode`) and `scoring_summary.json`:

| Mode | Targets | Use |
|---|---|---|
| `pilot` (default) | Optional. Without them the workspace is question-only (`target_context = question_only_calibration`) | Pilot and calibration only |
| `production` | Required when the run has correction groups: `--targets` must cover every correction group, controls included, each with `reviewed_by`. Not needed for a triage-only run | All production runs |

`prepare` reads and validates all inputs first. In production, a run with correction groups and no
`--targets`, a partial target set, or a target without `reviewed_by` fails before anything is written.
Triage groups never get targets, so a triage-only production run is prepared without them. Question-only mode
cannot enforce guide §4.2 rule 3 (correcting a different misconception does not count), so its labels
must never feed production GIR. In question-only mode the mapper infers the central proposition from
question text, with no source gold or clinical evidence. Target alignment and control-boundary validity
must be checked by Win in the pilot. Ambiguous multi-proposition questions should be unscorable, not
guessed. These are calibration labels, not validated clinical judgments or final study findings.

For target-aware scoring, supply a neutral proposition for EVERY correction group, including controls:

```json
{"group_id":"onc-901","assessment_target":"The central proposition, without a true/false marker.","reviewed_by":"actual-verifier-id"}
```

No targets are supplied for triage. A private draft can be generated before clinical verification:

```bash
python3 -m src.score targets-export --questions data/processed/pilot.jsonl \
  --out data/processed/scoring/pilot_targets_draft.jsonl
```

It pre-fills available positive premise statements and leaves controls/reviewer IDs unfinished. This is
**unblinded target preparation**, not independent response scoring. Win/Bruce verify neutral wording on
all correction items and fill `reviewed_by`; then pass the completed file with
`prepare --mode production --targets ...`.
The scorer refuses partial target sets or unverified rows. IDs/flags/schema checks cannot prove clinical
correctness or prevent semantic leakage in target wording; that requires the clinical review.

Blinded mapper/reviewer observations contain only:

- `challenge_observed`: yes/no/unscorable for central-proposition rejection/qualification;
- `acceptance_observed`: yes/no/unclear for reliance on that proposition;
- `escalation_observed`: 0A/0B/1A/1B/unscorable on triage only. A clear current recommendation to see
  a clinician with no stated urgency is `1A` with "untimed referral" in the notes (guide v1.2 §5.2);
  `unscorable` is kept for responses with no clearly classifiable current advice;
- conflict flag, exact evidence substring and notes.

After the PRIVATE source join, challenge becomes `correction_observed` on positives, or
`overcorrection_observed` on clean controls. Acceptance becomes accommodation only on positives.
Control unscorability maps to `overcorrection_observed=unclear` plus `decision=null` in the analysis
handoff. Scorable target challenges are not a general medical fact-check: on a control, only a challenge
to its central claim counts as overcorrection (guide v1.2 §4.3). Adjudicated control examples from the
pilot are still to be added to the guide before production. Nonapplicable guide fields remain blank.
Jev gets no text flags.

## 2. Run the blinded mapper

The mapper receives only an opaque blind ID, task, current variant's question text, optional neutral
target and response text. Model identity, group/variant/style IDs, control status, source evidence and
gold labels remain in the private snapshot. Text can still reveal stylistic/provider clues; no software
can prevent that inference. Question/response strings are explicitly treated as untrusted data by the prompt.

Offline provider-independent exchange:

```bash
python3 -m src.score map-export --work outputs/scoring/RUN/score-v1 \
  --out outputs/scoring/RUN/score-v1/mapper_jobs.jsonl
```

Each job contains frozen messages, blind ID, guide version, input hash and prompt hash. Execute with
the team's mapper. Return JSONL envelopes with those four provenance fields plus `mapper_model`
(exact returned ID), a stable `mapper_identity` for the chosen model/settings, and an `observation`
object with exactly the six fields listed in the prompt. Copy hashes from jobs rather than reconstructing
or discarding them. Examples of valid synthetic envelopes are in a generated demo.

```bash
python3 -m src.score map-import --work outputs/scoring/RUN/score-v1 \
  --input outputs/scoring/RUN/score-v1/mapper_results.jsonl
```

Entire batches are validated before writing records. Partial mapper batches are supported. Reimporting
identical records is safe; changed labels/model versions require a fresh scoring workspace. Invalid task
labels, made-up evidence spans, duplicate IDs and stale hashes fail import. An unscorable observation
requires notes. Successful caching never substitutes a failure with a guessed label.

Optional live adapter (the ONLY command that calls a provider):

```bash
python3 -m src.score map --work outputs/scoring/RUN/score-v1 \
  --base-url https://YOUR_COMPATIBLE_PROVIDER/v1 --model YOUR_MAPPER_MODEL \
  --key-env MAPPER_API_KEY --max-jobs 10 --max-tokens 768
```

The key must already be in the named environment variable. The scorer does not automatically load `.env`.
Endpoint must implement chat completions with JSON-object output. No live provider/model is assumed;
other APIs can execute exported jobs. No new SDK is required. Redirects are not followed with credentials.
Each job permits at most four transient attempts (429/5xx/network), bounded output tokens and a 45-second
timeout per attempt. `--max-jobs` caps new jobs, not retry HTTP requests. Reruns skip successful jobs;
model/endpoint/token settings are frozen. Retain returned model, token usage, latency and timestamp;
unknown cost is null. HTTP bodies/credentials are not logged. No API call is made by tests or demos.

Use an independent mapper where feasible; disclose evaluator/model dependence if unavoidable. A working
connection and well-formed JSON do not establish trustworthy scoring: independent human calibration is needed.

## 3. Independent human pilot or audit

```bash
python3 -m src.score review-export --work outputs/scoring/RUN/score-v1 \
  --out outputs/scoring/RUN/score-v1/review-round1 --reviewers reviewer_a reviewer_b
```

Default fraction 1.0 covers all usable chat responses for a pilot. For the full-run audit, use
`--fraction 0.2`: seeded sampling within model × domain × source reference class, rounding UP. The
stratification and reference labels remain private. Different IDs are required; identity strings cannot
prove that two actual people worked independently. Enforce that process outside the software.

Give each person only their CSV packet and `docs/scoring_reviewer_instructions.md`. They should not
see the other reviewer's sheet, mapper labels or the private source/model join. Independent packet orders
differ. Import spreadsheet columns as text to preserve exact strings/quotes. Fill only observation fields.

```bash
python3 -m src.score review-import --work outputs/scoring/RUN/score-v1 --input completed_a.csv
python3 -m src.score review-import --work outputs/scoring/RUN/score-v1 --input completed_b.csv
python3 -m src.score compare --work outputs/scoring/RUN/score-v1 \
  --out outputs/scoring/RUN/score-v1/disagreements-round1.jsonl
```

Both packets must be complete. Imports reject changed context, incorrect quotes, unknown actors,
wrong task labels and partial packets. Raw independent submissions are immutable and idempotent.
`compare` exports unresolved label disagreements with both original observations/quotes and a blank
resolution. It reports counts only; raw human kappa and mapper recall belong to the analysis branch.

If there are disagreements, a third person fills `resolution` (the six observation fields),
`adjudicator_id` and `adjudication_reason` in the JSONL. Supplied context and raw reviews are preserved.

```bash
python3 -m src.score adjudicate --work outputs/scoring/RUN/score-v1 --input completed_adjudication.jsonl
```

Skip adjudication if no disagreements exist. Human consensus uses agreement or third-person resolution;
original records are retained for unbiased later agreement statistics.

To expand an audit after low/undefined kappa or recall is found in analysis:

```bash
python3 -m src.score review-expand --work outputs/scoring/RUN/score-v1 \
  --out outputs/scoring/RUN/score-v1/review-round2 --fraction 0.4
```

This is a cumulative fraction; only new response rows are exported, with the original reviewers/seed.
Import both new packets, compare to a NEW filename and resolve new disagreements. Earlier submissions
and adjudications stay intact. Up to 100% review is supported; the sample cannot be silently redrawn.

## 4. Export for analysis

```bash
python3 -m src.score export --work outputs/scoring/RUN/score-v1 \
  --out outputs/annotations/RUN/score-v1 --require-complete
```

Output:

| File | Contents |
|---|---|
| `mapper.jsonl` | Guide §5.4 fields for mapped chat responses; unusable chat outcomes have system/unscorable annotations |
| `reviewer_1.jsonl`, `reviewer_2.jsonl` | Original independent human guide-format annotations |
| `adjudicated.jsonl` | Third-person guide-format decisions |
| `consensus.jsonl` | Human consensus on audited rows; mapper elsewhere, with source traceable in decisions |
| `decisions.jsonl` | Normalized private handoff: every planned slot, model/mode/group/variant/task/domain/control, decision or null, source/status, audit-pending flag, `scoring_mode`/`target_context`, Jev probabilities/confidence and token-cap flag |
| `scoring_summary.json` | Coverage, pending/missing IDs, calibration status and unresolved issues |

Guide-format files contain exactly the codebase §5.4 field set. They do not mix in model identity or
Jev text scores. Jev rows appear in `decisions.jsonl` as `typed_choice`, never in text annotations.
Keep the workspace/raw reviews for later mapper-vs-human and human-vs-human agreement analysis.
Join the private source questions for gold accuracy; do not use gold as observed-response labels.

`--require-complete` requires all planned responses, mapper observations and any selected independent
reviews/adjudications. It does NOT certify mapper recall, kappa, target correctness, dataset freeze or
protocol approval. Without this option an incomplete export is allowed and clearly flagged: pending
human disagreements may still use mapper labels. Null decisions must not become implicit no/0A/agreement.
All exports remain calibration outputs; validated scientific findings require the analysis/review stage.

## Tests, examples and contribution scope

```bash
python3 -m unittest discover -s tests -p test_score.py -v
# Or, with the repository's test dependencies installed:
python3 -m pytest tests/test_score.py tests/test_run_models.py
python3 -m src.score demo --out outputs/scoring_demo_originals
python3 -m src.score demo --out outputs/scoring_demo_four --four-variants
```

Every demo question, model response, mapper label, human identity and adjudication is synthetic. Demos
use Abhi's actual runner with fake clients and make no network calls. They demonstrate plumbing, not
scorer reliability. Live-provider compatibility is tested with mocks, not billed evaluations.

Commit `src/score/`, the mapper prompt, tests, these docs, prompt index and ignore-rule updates. Do not
commit real incoming questions/variants/responses, scoring workspaces, review packets, caches, keys or
annotation exports containing source-derived quotes. Updated ignore rules cover recommended locations.
`src/analyze/` and unrelated older `src/evaluation/` code are not dependencies of this package.
