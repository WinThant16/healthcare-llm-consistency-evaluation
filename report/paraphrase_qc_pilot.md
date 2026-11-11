# Paraphrase QC report: `pilot`

**Ready for the model run:** yes

## Setup

| Provider | Generator model | Temperature | Prompt |
|---|---|---|---|
| claude-chat-session | claude-sonnet-5-5 | None | paraphrase_generate_v1 (rules applied manually in a Claude chat session, no API call) |

Styles: `p1` = neutral, `p2` = casual, `p3` = worried_indirect.

Generation calls: 6 (0 failed, 1 regenerations). New tokens billed: 0 in, 0 out.

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

Of 15 paraphrases with a final decision, 15 (100%) were valid on the first attempt and 0 (0%) needed regeneration.

## Reviewer agreement

Independently double-reviewed decisions: 15.

| Reviewer 1 \ Reviewer 2 | valid |
|---|---|
| valid | 15 |

## Automatic checks against human decisions

How well the rule flags and the judge predicted an `invalid` human decision, on reviewed text.

| Rule flag | Reviewed paraphrases with flag | Of which invalid |
|---|---|---|
| `(no flag)` | 13 | 0 (0%) |
| `person_terms_changed` | 1 | 0 (0%) |

