# Paraphrase QC report: `pilot`

**Pipeline checks:** pass (every group has all four variants and every paraphrase is valid)

## Setup

| Provider | Generator model | Temperature | Prompt |
|---|---|---|---|
| claude-chat-session | claude-sonnet-5-5 | None | paraphrase_generate_v1 (rules applied manually in a Claude chat session, no API call) |

Styles: `p1` = neutral, `p2` = casual, `p3` = worried_indirect.

Judge model: none (rule flags only).

**Note:** this set was written by hand in a chat session, not through the pipeline's `generate` step, so no API generator or judge has been used on it. The models for the full run are still to be chosen by the team.

Generation calls: 6 (0 failed). Regenerations: 1 (0 after a reviewer rejected a paraphrase, 1 before review). New tokens billed: 0 in, 0 out.

## Current validity

| Domain | Style | Paraphrases | Valid | Invalid | Needs review |
|---|---|---|---|---|---|
| acute_triage | p1 (neutral) | 2 | 2 (100%) | 0 (0%) | 0 (0%) |
| acute_triage | p2 (casual) | 2 | 2 (100%) | 0 (0%) | 0 (0%) |
| acute_triage | p3 (worried_indirect) | 2 | 2 (100%) | 0 (0%) | 0 (0%) |
| oncology | p1 (neutral) | 2 | 2 (100%) | 0 (0%) | 0 (0%) |
| oncology | p2 (casual) | 2 | 2 (100%) | 0 (0%) | 0 (0%) |
| oncology | p3 (worried_indirect) | 2 | 2 (100%) | 0 (0%) | 0 (0%) |
| public_health | p1 (neutral) | 1 | 1 (100%) | 0 (0%) | 0 (0%) |
| public_health | p2 (casual) | 1 | 1 (100%) | 0 (0%) | 0 (0%) |
| public_health | p3 (worried_indirect) | 1 | 1 (100%) | 0 (0%) | 0 (0%) |

## First-pass quality

Of 15 paraphrases with a final decision, 15 (100%) were valid when first reviewed and 0 (0%) were rejected by a reviewer and regenerated. (Regenerations before review are not counted here.)

## Reviewer agreement

Independently double-reviewed decisions: 15.

| Reviewer 1 \ Reviewer 2 | valid |
|---|---|
| valid | 15 |

## Automatic checks against human decisions

How well the rule flags and the judge predicted an `invalid` human decision, on reviewed text.

| Rule flag | Reviewed paraphrases with flag | Of which invalid |
|---|---|---|
| `(no flag)` | 14 | 0 (0%) |
| `person_terms_changed` | 1 | 0 (0%) |

