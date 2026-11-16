# Integrating the independent analysis feature

The response-scoring PR is still unmerged. Start `feat/consistency-analysis` from updated main, not from
the scoring branch. This feature adds only new paths, so its PR does not contain scoring commits or edit
files owned by PR #21. Both packages communicate through a documented file contract.

## Branch and patch workflow (commands for the user; not executed by delivery)

Preserve unfinished work first. From a clean actual Git clone:

```bash
git switch main
git pull --ff-only origin main
git switch -c feat/consistency-analysis
git apply --check /ABSOLUTE/PATH/Consistency-Analysis-Feature.patch
git apply /ABSOLUTE/PATH/Consistency-Analysis-Feature.patch
```

If already on an appropriate analysis branch, keep it and verify its base first. The local delivery
snapshot is not an actual Git checkout; do not upload the entire source snapshot or copy scoring code
into the analysis PR to make imports work. The analysis runtime has no such imports.

## What to stage

- `src/analyze/` (eight implementation modules)
- `tests/test_analyze.py`
- `tests/test_analyze_scoring_integration.py` (optional contract tests, skip without scorer)
- `docs/analysis.md`
- `docs/analysis_integration.md`
- `configs/consistency_analysis_v1.json`
- `requirements-analysis.txt` (optional Matplotlib only)

No edits to `.gitignore`, `requirements.txt`, README, schedule, prompt index, or `src/score/` are needed.
Generated result directories create their own local ignore file, so even the ignore rules do not overlap.
The feature ZIP and patch list the same new files. Local start pages, verification manifests, dependency
environments, source snapshots and output examples are delivery aids, not PR content.

## Test it before the scoring merge

```bash
python3 -m unittest discover -s tests -p 'test_analyze*.py' -v
python3 -m src.analyze demo --out outputs/analysis_review_demo
python3 -m src.analyze demo --out outputs/analysis_review_originals --originals-only
```

You should see standalone checks pass and optional scoring integration tests skip. The full fake run
includes one inconsistent group among two oncology positives (GIR 50%), a consistently wrong group,
missingness, independent mapper mismatch and retained human disagreement. The originals-only run
produces NA for GIR. No real data, API keys or scoring branch are required.

Optional figures:

```bash
python3 -m pip install -r requirements-analysis.txt
python3 -m src.analyze demo --out outputs/analysis_review_figures --plots
```

## Test both features together without changing either branch

After the scoring PR merges into main, update the analysis branch normally and run:

```bash
python3 -m pytest tests/test_analyze.py tests/test_analyze_scoring_integration.py tests/test_score.py tests/test_run_models.py -q
```

The integration tests will automatically stop skipping. They import scoring only inside the optional
tests; analysis itself remains a data-file consumer. If the scoring export contract changes during
review, update this feature's adapter/tests rather than modifying the other branch behind its reviewers.

## Actual-data handoff once scoring is available

Abhi's original model response directory goes to the scoring stage. Analysis consumes the scorer's
export, not unlabelled model prose. Example:

```bash
python3 -m src.analyze run \
  --annotations outputs/annotations/YOUR_RUN/score-v1 \
  --questions data/processed/pilot.jsonl \
  --scoring-work outputs/scoring/YOUR_RUN/score-v1 \
  --policy-file configs/consistency_analysis_v1.json \
  --out outputs/analysis/YOUR_RUN/analysis-v1
```

Without private scoring workspace access, omit `--scoring-work` and explicitly supply
`--models gpt claude jev`; use the exported annotation files for the guide-format agreement adapter.
Confirm that references and group IDs match, preserve raw independent reviews, resolve policy choices
with Win and disclose any missing/undefined class statistics. Five originals can be analyzed for label
agreement/accuracy now, but four-wording GIR needs all four observed variants.

Keep the two PRs independently reviewable. Review of the analysis defaults is not approval of the
scoring prompt or its clinical target/control supplement, and passing synthetic tests is not evidence
that the real mapper is trustworthy.
