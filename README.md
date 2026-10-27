# Healthcare LLM Consistency Evaluation

Evaluating healthcare LLM consistency under paraphrasing for false-presupposition detection and clinician escalation.

## Project Overview

This project studies whether large language models maintain consistent safety behavior when healthcare questions are rephrased while preserving the same underlying meaning.

We focus on:

- False-presupposition detection
- Appropriate clinician escalation
- Consistency across paraphrased questions
- Flip-rate across model responses

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
bash data/fetch_raw.sh               # downloads UPHILL (not committed) and verifies checksums
python -m src.data.build_pools       # raw data → candidate pools
python -m pytest tests
```

Next steps, from candidates to the 240 question groups: see `data/README.md`.

## Project Structure

```text
codebase.md   How the repo works: pipeline, schemas, conventions (start here)
docs/         Protocol, amendment, annotation guide, schedule
data/         Raw sources, screening decisions, manifests (see data/README.md)
src/data/     Loaders, schema validator, screening, sampling
prompts/      Versioned model and judge prompts
outputs/      Model responses, annotations, metrics, figures
tests/        Unit tests
report/       Final report
presentation/ Final presentation
```
