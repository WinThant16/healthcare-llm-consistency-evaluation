#!/usr/bin/env bash
# Download raw source datasets that are not committed to the repo, then verify checksums.
# Usage (from repo root, with .venv active or created):  bash data/fetch_raw.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}

# --- CARE-Bench v1.1 (committed; re-fetch only if missing) -------------------------------
CB=data/raw/care-bench
CB_URL=https://huggingface.co/datasets/ningkko/CARE-Bench/resolve/main
mkdir -p "$CB"
for f in README.md VALIDATION_REPORT.txt label_schema.json cases_public.jsonl \
         evaluation_prefixes_public.jsonl model_inputs_public.jsonl submission_template_public.csv; do
  [ -f "$CB/$f" ] || curl -sfL -o "$CB/$f" "$CB_URL/$f"
done

# --- UPHILL (not committed: no stated licence, UPHILL.csv is 629 MB) ----------------------
UP=data/raw/uphill
UP_FOLDER=https://drive.google.com/drive/folders/13H2-dA0y8eOChwI8O1AqTJTqAr1raPXf
if [ ! -f "$UP/queries.csv" ]; then
  "$PY" -m gdown --version >/dev/null 2>&1 || "$PY" -m pip install -q gdown
  "$PY" -m gdown --folder "$UP_FOLDER" -O "$UP"
fi

# --- Verify --------------------------------------------------------------------------------
shasum -a 256 -c <<EOF
510dba567dbc8cb338d91f6c9412ff326be8a6a6bdd1ece241926e5315fe9463  $CB/cases_public.jsonl
6d2850f303d05b8f92035c31a11233f41a965e77b3e878e1425f956a00cff534  $CB/evaluation_prefixes_public.jsonl
3771e69715278580f9c7d013ac622d333fb0f861c387f2a143517ddb6b60f95b  $CB/model_inputs_public.jsonl
805c2f21c576c836a081e3636bcee6efceb33a5150c2c2592b9923602a4b75b3  $CB/label_schema.json
ddd9f65b26a1046a5eb641c0bf16b16deb7c73295a8afe9f1c49bf99c0b3c088  $CB/submission_template_public.csv
f7fd1cf21448919b31e655ff8e4a9b2ff01afc9de9a4b83eea671f92a81b76b6  $UP/queries.csv
f30aa6bdb0c4dc83c596a571d0df3f7423e7a0e1baed1529e2f01e7ac39637a4  $UP/entailment_validation.csv
42de5b19171fa14069892868cc78066aa6c0fe7b65ea33b2841843d332fc0003  $UP/UPHILL.csv
EOF
