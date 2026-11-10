# Evaluation

How well the scorer agrees with a person, measured on companies labelled by hand before they were scored. Rerun with `python -m agent.benchmark` (no model needed: it reads the saved results in `data/`); rescore with `python -m agent.evaluate`.

## The benchmark

57 companies, each labelled qualify, review or disqualify by the project owner from the ideal customer's rules, without seeing the scorer's answer.

| Set | Companies | Text scored | Role |
|---|---|---|---|
| `labelled_leads.csv` | 20 made-up | typed description | Used while writing the rules (tuning set) |
| `unseen_leads.csv` | 15 made-up | typed description | Held out: written and labelled after the rules, never tuned on |
| `unseen_leads_2.csv` | 12 made-up | typed description | Held out, labelled blind |
| `real_leads.csv` | 10 real | their own website, fetched | Real companies; rescored 10 November 2026 |

All on `qwen3.5:9b` with the second opinion from `qwen3.5:4b`, final rules (November 2026).

## Results, all 57

Rows are the person's label, columns the scorer's verdict.

| Label \ scorer | qualify | review | disqualify |
|---|---|---|---|
| **qualify** | 13 | 4 | 0 |
| **review** | 8 | 9 | 0 |
| **disqualify** | 1 | 4 | 18 |

Exact match: 40 of 57 (70%). Every miss but one is a single step (review against qualify or disqualify); the exception is a company labelled disqualify that the scorer qualified.

| Question | Precision | Recall | False positives | False negatives |
|---|---|---|---|---|
| **Worth a rep's time?** (qualify or review against disqualify) | 87% | 100% | 5 | 0 |
| **Qualify?** | 59% | 76% | 9 | 4 |

- **Worth a rep's time** is the decision that costs money: a false negative is a good lead the team never sees, a false positive is a lead a rep reads and drops. The scorer dropped **no** lead the person wanted (recall 100%) and sent 5 of the 23 the person would have dropped to a rep (all 5 visible with their reason).
- **Qualify** is weaker. Of 22 leads the scorer called qualify, 9 were labelled lower, mostly review. In practice the scorer is generous at the top: it promotes leads the person would want checked first.

## By set

| Set | Exact | Worth it: precision / recall | Qualify: precision / recall |
|---|---|---|---|
| Made-up, tuning (20) | 16 / 20 (80%) | 83% / 100% | 62% / 100% |
| Made-up, held out (15) | 11 / 15 (73%) | 90% / 100% | 50% / 67% |
| Made-up, held out, blind (12) | 8 / 12 (67%) | 86% / 100% | 50% / 67% |
| Real, from websites (10) | 5 / 10 (50%) | 90% / 100% | 67% / 67% |
| **Held out and real together (37)** | **24 / 37 (65%)** | **89% / 100%** | **57% / 67%** |

The tuning set scores highest, as expected for the set the rules were written against; the one lead labelled disqualify and scored qualify is in it. On the 37 never tuned on, nothing labelled disqualify was qualified and no wanted lead was dropped. The real set scores lowest on exact match: real websites are wordier and vaguer than typed descriptions, and the real labels were written before some rules changed.

## What this does not show

- **It is small.** 57 companies; one more mistake moves a percentage by several points.
- **One person labelled everything**, the same person who wrote the rules. A second labeller would show how much people disagree with each other.
- **20 of the 57 were used for tuning.** The 37 held-out and real ones are the fairer test: 65% exact against 70% overall.
- **Labels follow the ideal customer's rules, not outcomes.** Agreeing with a person is not the same as finding companies that buy. That needs a real campaign, which was not run.
- **Runs vary.** The same company scored twice can move one step, so a rerun can shift these numbers by a lead or two.

## A second set, labelled by a model

50 more real companies were labelled from the same rules by a separate AI model (not the scorer), then scored from their websites: 39 agree, 11 one step off, 0 wrong. It is a larger check of rule-following, but a model's labels are not a person's, so it is kept out of the numbers above.

## A pattern seen in the emails

The email writer read "Finance teams at more than 900 businesses use Northwind" as Northwind's own finance teams in three of the four drafts written for it ([worked example](examples/northwind-payroll.md)). The quote check passes it because the words are on the page. It is the clearest case of what the checks cannot catch, and the reason a person approves every email.
