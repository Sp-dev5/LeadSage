# LeadSage

**Prospect research and first emails for teams that sell to businesses. Every line about a company has a source.**

---

## What it does, in one paragraph

Salespeople waste most of their day on busywork: reading up on each company, finding the right person, and writing "personal" emails one at a time. LeadSage does that work for them. You describe your ideal customer and give it a list of companies; it researches each one, scores it and explains why, finds a person to write to, and drafts a personalised first email and up to three follow-ups for a person to approve and send. The payoff: **less busywork, plus a ranked list that shows who is closest to a sent email.**

## How it works (the loop everything serves)

Steps 1 and 3 to 6 are built. Step 2 is partly built, and step 7 is planned.

1. **Describe the ideal customer**: industry, size, location, the problem you solve.
2. **Find the right person** at each company. (Finding the companies themselves is not built: you supply the list.)
3. **Research** each company, with sources and dates.
4. **Score the lead and explain why** it's worth chasing.
5. **Write a personalised email** that references something real about them.
6. **A human approves** each email and sends it. Follow-ups are drafted for 3, 7 and 14 days later and stop if the lead replies, books a meeting or is lost.
7. **The best leads rise to the top**, and everything syncs to a CRM. (Planned.)

## What's built so far

- **The app** (`streamlit_app.py`): a top menu with four pages. **Home** says how many drafts are ready to review, with the key numbers, a tile per verdict, the pipeline from scored to sent and the strong fits held back by one rule. **Leads** lists every scored company with its logo and website, split into Qualify, Strong fit (fit above the qualify cut-off but held at review by one rule, which is named), Review, Disqualify and All, with filters (country, your ICP region, minimum fit, hiring for sales) and the reasons behind every number. **Outreach** is the work queue for emails. **Ideal customer** edits the profile; each description has a Suggest button that asks the local model for a fuller version, and nothing changes until you click Use this. Leads and emails export as CSV from the sidebars. Location is only a filter; it never changes a score. Dark theme only; animations follow your system's motion setting.
- **Company logos** (`agent/logos.py`): the icon each company's own homepage declares, saved in `data/logos/` (not committed). No logo service is used. Companies whose site gives none show an initial. `python -m agent.logos` fetches them for stored leads, and the pipeline fetches one for each new lead.
- **Website** (`site/index.html`): a one-page site that explains the product and links to the app. Plain HTML, CSS and a little JavaScript, no build step.
- **Pipeline and lead store** (`agent/pipeline.py`, `agent/store.py`): reads each company's website, finds open sales roles, scores it and saves the result in a local SQLite file the app reads.
- **Outreach** (`agent/outreach.py`, `agent/contacts.py`, the Outreach page): finds the person to write to on the company's own about and team pages, drafts a short first email and up to 3 follow-ups, and tracks each lead's stage (new, drafted, approved, contacted, replied, meeting, lost). Every sentence about the prospect must quote their own website or job posts; sentences without a real quote are cut, and each kept one shows its source and date. A first email should open with a hook, a timely reason to write: the company is hiring salespeople, or its own news page shows something recent (a launch, a partnership, a new hire). Drafts with no hook are flagged so a person adds one before sending. A person approves every email and sends it from their own mailbox; a reply stops the follow-ups. Sender, follow-up days and which leads get emails live in `data/outreach.json`.

- **Lead scoring** (`agent/score_lead.py`): scores a company against the ICP with evidence for every number. Facts like size and country are checked in code; the model only judges buyer and pain. A company under the minimum headcount, or above the largest size times `max_size_factor` (both in `data/icp.json`), is dropped without asking the model for buyer and pain. The fit weights and cut-offs in that file are plain numbers for the fit average: the model never sees them. Reading facts out of a company's text (what it sells, who it sells to, whether it is a non-profit or a government body) runs at temperature 0, so the same text gives the same facts; only the buyer and pain judgement keeps a little variety. Freelancers and sole proprietors who buy as a business count as businesses, and a non-profit or government body goes to review, not to disqualify.
- **Model layer** (`agent/llm.py`): local open-source models through Ollama do the scoring, so nothing leaves your machine. Optional: with a free Groq key in `.env` (`GROQ_API_KEY`), the email drafts are written by a larger model on Groq's free tier; only the drafting prompt (public website text about the company, the contact's name and title, and your pitch) is sent. Without a key, or if Groq fails, the local model writes them.
- **Evaluation** (`agent/evaluate.py`): grades the scorer against hand-labelled leads. With `--text fetched` it scores the company's real website instead of the typed description.
- **Fetcher** (`agent/fetch.py`): reads a company's web page and returns the text with its source and date. It checks robots.txt first, including at every redirect. A failed fetch never stops a batch.
- **Sales hiring** (`agent/signals.py`): reads a company's open jobs from its job board (Ashby, Greenhouse, Lever, Keka, Trakstar; found on the careers page or typed in the CSV) and finds the sales roles. A title counts if it contains one of the ICP's sales titles, or if the model says it's a sales role and the title really is on the list. Each role keeps its source and date. Found roles tell the scorer the company has salespeople.
- **Careers probe** (`agent/fetch_careers.py`): a feasibility check for the hiring signal. It fetches each careers page from `data/signals_leads.csv` (columns `name,careers_url`) and reports whether the page is readable and mentions sales roles. It passes if at least half do.

## Roadmap

1. Lead scoring (done)
2. Walking skeleton: research, score, contact, cited email, follow-ups (done; sending stays manual)
3. Scoring engine: add intent and engagement on top of fit
4. **Signal watcher**: watch each company for fresh buying signals and bump it up the queue the moment one fires
5. Approval pause: the agent stops for a human to approve before "sending"

## Run it

```bash
pip install -r requirements.txt
ollama pull qwen3.5:9b            # local model (default)
python -m agent.pipeline --file real_leads.csv   # research + score into the lead store
python -m agent.outreach --draft  # find contacts and draft first emails
python -m agent.outreach --follow-ups            # draft follow-ups that are due
streamlit run streamlit_app.py --server.address localhost   # the app (it has no login: keep it on localhost)
python -m agent.logos             # fetch company logos for stored leads
python -m http.server 8600 --directory site                 # the website, at http://127.0.0.1:8600/
python -m agent.score_lead        # score a sample company
python -m agent.evaluate          # grade against the labelled leads
python -m agent.evaluate --file real_leads.csv --text fetched   # score real websites
python -m agent.fetch https://example.com                       # read one page
python -m agent.fetch_careers                                   # probe careers pages
python -m agent.signals                                         # sales roles from job boards
```

Run the tests with `pip install -r requirements-dev.txt` then `pytest`.

The ICP lives in `data/icp.json`. Change it to score for a different customer.
Swapping models only touches `agent/llm.py`; nothing that calls it changes.

## A few terms

| Term | Plain meaning |
|---|---|
| **Lead** | A company or person who might buy. |
| **ICP** | Ideal Customer Profile: a description of the perfect customer. |
| **Agent** | An AI that does a job in steps on its own (search then read then write). |
| **CRM** | Software sales teams use to track customers. |

## Docs

| Doc | What it's for |
|---|---|
| [docs/differentiators.md](docs/differentiators.md) | The one differentiator, signal-triggered timing, and the trust layer under it |
| [docs/02-architecture.md](docs/02-architecture.md) | How the parts connect, the tech stack, the data model |
