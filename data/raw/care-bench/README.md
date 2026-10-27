---
pretty_name: CARE-Bench

configs:
  - config_name: model_inputs
    default: true
    data_files:
      - split: public
        path: model_inputs_public.jsonl

  - config_name: cases
    data_files:
      - split: public
        path: cases_public.jsonl

  - config_name: evaluation_prefixes
    data_files:
      - split: public
        path: evaluation_prefixes_public.jsonl

  - config_name: submission_template
    data_files:
      - split: public
        path: submission_template_public.csv
---

# CARE-Bench v1.1 public data

[GitHub link to the project](https://github.com/ningkko/CARE-bench)

The public release contains 439 cases and 925 labeled prefixes.

| Split | Cases | Prefixes |
|---|---:|---:|
| Development | 284 | 609 |
| Validation | 93 | 181 |
| Public Test 1 | 62 | 135 |

The prefix labels are distributed as follows:

| Label | Prefixes |
|---|---:|
| Information needed | 249 |
| Self-care or monitor | 162 |
| Nonurgent care | 342 |
| Urgent care | 172 |

The Dataset Viewer includes four configurations:

- `model_inputs`: identifiers, split information, and model-facing messages
- `cases`: released source-grounded cases
- `evaluation_prefixes`: reference labels and evaluation metadata
- `submission_template`: submission rows for every released prefix

The `cases` configuration excludes internal review annotations, construction-plan fields, and target-round planning notes.

The `public_test_1` split contains 135 scored prefixes.

Controlled-access Test 2 inputs and labels are excluded from the public repository. Input-only data can be requested through the [CARE-Bench Test 2 access request form](https://forms.gle/hrmC3ciGcj46f4gW7).