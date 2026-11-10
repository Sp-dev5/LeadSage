# LeadSage

**Prospect research and first emails for teams that sell to businesses. Every line about a company has a source.**

[![CI](https://github.com/Sp-dev5/LeadSage/actions/workflows/ci.yml/badge.svg)](https://github.com/Sp-dev5/LeadSage/actions/workflows/ci.yml)

![The LeadSage home page: drafts ready to review, leads scored, verdicts and the quote check](docs/images/home.webp)

Salespeople spend most of their day reading up on companies, finding the right person and writing "personal" emails one at a time. LeadSage does that work: describe your ideal customer, give it companies, and it researches each one, scores it with reasons, finds who to write to and drafts the emails. A person approves every email before it goes out.

## At a glance

- **Finds and researches companies** from the open Y Combinator list, their own websites, job boards and Indian startup news.
- **Scores each one against your ideal customer** and says why, with a quote behind every reason.
- **Drafts a first email and three follow-ups.** A line about the prospect that has no matching quote is cut before you see it.
- **A person approves every email**, then it sends from their own Gmail. Replies stop the follow-ups, and "not interested" puts the company on a do-not-contact list.
- **Shows its work:** every model prompt and answer for a lead is kept and shown on the lead's page.
- **Runs on your laptop** with local models. No paid APIs.

**Built with** Python, FastAPI, SQLite, Ollama (local LLMs), `nomic-embed-text` embeddings for retrieval, SMTP and IMAP. CI runs 778 tests, `bandit`, `pip-audit` and a secrets scan on every pull request.

## See it work

The screenshots below use made-up companies on `.example` domains, run through the real app. **[Worked example](docs/examples/northwind-payroll.md)**: one lead from website text to sent-ready email, with the scoring output, the email's sources and the full audit trail.

**Scoring, with the reason behind each number.** Northwind Payroll qualifies; a tutoring app and a design agency are disqualified, each with its reason.

![Leads page: three companies scored, one qualify and two disqualify, with fit, contact and the rule that held each back](docs/images/leads.webp)

**An email where every claim has a source.** Each sentence about the prospect is tied to the words it came from, and the rep still approves it.

![Outreach page: the draft email with highlighted claims, footnotes, and a table of the source words for each](docs/images/email.webp)

**The audit trail.** Every step on a lead, each model call with the prompt that went in and the answer that came back, and each check with what it kept and cut. See it in the [worked example](docs/examples/northwind-payroll.md#5-the-audit-trail).

## Results

**Scoring**, with the final rules on local models (November 2026):

| Set | Text scored | Agrees | Near | Wrong |
|---|---|---|---|---|
| 47 hand-labelled companies, mostly made up for testing | typed description | 35 | 11 | 1 |
| 50 real companies, labelled from the ICP's rules by a separate AI model | website text | 39 | 11 | 0 |

Near means one verdict away (review against qualify or disqualify).

**A run on 66 real companies** (from the Y Combinator list and Indian startups, read mostly from their own websites): 55 worth contacting (11 qualify, 24 strong fits held back by one rule, 20 to review), 11 disqualified, 29 emails drafted. Of the lines about prospects in those drafts, 34 matched a quote and were kept, 3 had none and were cut.

**Sending and replies**, tested end to end on real Gmail: an approved email sent, a "not interested" reply in the same thread found over IMAP, the company moved to do-not-contact and its follow-ups stopped. The reply came from the same account, not a separate person.

**What this does not show.** No real sales campaign was run, so there are no reply rates, revenue or measures of how often reps edit drafts. The numbers show the scorer follows the ICP's rules, not that the rules are right. A quote check proves the words exist, not that a sentence reads them right; the [worked example](docs/examples/northwind-payroll.md#4-the-email-every-claim-tied-to-a-source) shows one it missed.

## Quick start

Needs Python 3.12 and [Ollama](https://ollama.com).

```bash
pip install -r requirements.txt
ollama pull qwen3.5:9b && ollama pull qwen3.5:4b && ollama pull nomic-embed-text
cp .env.example .env                                  # optional: Groq key, Gmail, app password
python -m agent.find --industry B2B --limit 20        # real companies into data/found_leads.csv
python -m agent.pipeline --file found_leads.csv       # research and score them
python -m agent.outreach --draft                      # find contacts and draft first emails
uvicorn web.app:app --host 127.0.0.1 --port 8000      # open http://127.0.0.1:8000
```

Tests: `pip install -r requirements-dev.txt` then `pytest` (no model or network needed). The ideal customer lives in `data/icp.json`; edit it on the Ideal customer page. All commands: [docs/features.md](docs/features.md#commands).

## Layout

| Folder | What's in it |
|---|---|
| `agent/` | The pipeline: finding companies, research, scoring, contacts, emails, sending, the audit trail, your documents |
| `web/` | The FastAPI app: routes, templates, CSS |
| `data/` | The ideal customer and outreach settings, labelled test sets, sample documents (the lead store itself is git-ignored) |
| `tests/` | pytest suite, with fake models and mailboxes |
| `site/` | A one-page website for the product |
| `deploy/` | Guide and scripts for a free Ubuntu server behind HTTPS |
| `docs/` | Features, worked example, architecture |

## Docs

| Doc | What's in it |
|---|---|
| [docs/features.md](docs/features.md) | Every part in detail, and every command |
| [docs/examples/northwind-payroll.md](docs/examples/northwind-payroll.md) | One lead start to finish: scoring output, cited email, audit trail |
| [docs/differentiators.md](docs/differentiators.md) | Signal-triggered timing, and the trust layer under it |
| [docs/02-architecture.md](docs/02-architecture.md) | The original plan: how the parts connect, the data model |
