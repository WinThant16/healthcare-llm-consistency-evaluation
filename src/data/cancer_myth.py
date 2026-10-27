"""Cancer-Myth loader → oncology correction candidates.

- ``cancer-myth/validation`` (585): positives, gold ``correction_required``.
- ``cancer-myth-nfp/nfp`` (150): controls, gold ``no_correction``. These are generated questions that an LLM
  verifier flagged as containing a false presupposition but physicians confirmed contain none (Cancer-Myth
  paper, review criterion 1). ``non_presupposition`` is the presupposition the LLM *claimed* and physicians
  rejected, which makes these adversarial overcorrection controls.

Cancer-Myth gives the expert *correction*, not the premise itself, so ``target_false_premise`` stays empty
for the clinical lead to write from ``source_evidence``.
"""

import pandas as pd

from src.data.schema import candidate_id, make_row

POSITIVES = "data/raw/cancer-myth/validation-00000-of-00001.parquet"
NFP = "data/raw/cancer-myth-nfp/nfp-00000-of-00001.parquet"
NFP_EVIDENCE = "Physicians confirmed no false presupposition. LLM-claimed presupposition they rejected: "


def load(positives_path=POSITIVES, nfp_path=NFP):
    rows = []
    for r in pd.read_parquet(positives_path).itertuples(index=False):
        source_id = f"cm-{r.QID}"
        rows.append(make_row(
            candidate_id=candidate_id("oncology", source_id),
            domain="oncology",
            task="correction",
            is_control=False,
            source_dataset="cancer_myth",
            source_id=source_id,
            original_question=r.question.strip(),
            source_label_raw={"category": r.category, "presupposition_correction": r.presupposition_correction},
            gold_label_mapped="correction_required",
            source_evidence=r.presupposition_correction.strip(),
            eligibility_status="pending",
            review_status="pending",
            source_meta={"cancer": r.cancer, "from_model": r.from_model, "source": r.source,
                         "source_myth": r.source_myth},
        ))
    for r in pd.read_parquet(nfp_path).itertuples(index=False):
        source_id = f"cmnfp-{r.QID}"
        rows.append(make_row(
            candidate_id=candidate_id("oncology", source_id),
            domain="oncology",
            task="correction",
            is_control=True,
            source_dataset="cancer_myth_nfp",
            source_id=source_id,
            original_question=r.question.strip(),
            source_label_raw={"non_presupposition": r.non_presupposition, "source_type": r.source_type},
            gold_label_mapped="no_correction",
            source_evidence=NFP_EVIDENCE + r.non_presupposition.strip(),
            eligibility_status="pending",
            review_status="pending",
            source_meta={"cancer": r.cancer, "from_model": r.from_model, "source_row": int(r.source_row)},
        ))
    return rows
