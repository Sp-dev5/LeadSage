# LeadSage

**AI-powered lead qualification and sales automation.**

---

## What it does, in one paragraph

Salespeople waste most of their day on busywork: hunting for companies to sell to, reading up on each one, finding the right person, and writing "personal" emails one at a time. LeadSage does that work for them. You describe your ideal customer; it finds matching companies, researches them, scores each lead and explains why, writes a personalised first email, and runs the follow-ups. The payoff: **less busywork, plus a ranked list that says who to contact first, and when.**

## How it works (the loop everything serves)

1. **Describe the ideal customer**: industry, size, location, the problem you solve.
2. **Find companies and the right person** at each one.
3. **Research** each company and write a short brief, with sources.
4. **Score the lead and explain why** it's worth chasing.
5. **Write a personalised email** that references something real about them.
6. **A human approves**, then up to 3 follow-ups go out, stopping if they reply.
7. **The best leads rise to the top**, and everything syncs to a CRM.

## What's built so far

- **Lead scoring** (`agent/score_lead.py`): scores a company against the ICP with evidence for every number. Facts like size and country are checked in code; the model only judges buyer and pain.
- **Model layer** (`agent/llm.py`): a local open-source model through Ollama first, Gemini as fallback and second opinion.
- **Evaluation** (`agent/evaluate.py`): grades the scorer against hand-labelled leads.

Steps 2, 3 and 5 to 7 are planned.

## Run it

```bash
pip install -r requirements.txt
ollama pull qwen2.5:7b            # local model (default)
# optional: add GEMINI_API_KEY=your-key to .env for the fallback
python -m agent.score_lead        # score a sample company
python -m agent.evaluate          # grade against the labelled leads
```

The ICP lives in `data/icp.json`. Change it to score for a different customer.

## A few terms

| Term | Plain meaning |
|---|---|
| **Lead** | A company or person who might buy. |
| **ICP** | Ideal Customer Profile: a description of the perfect customer. |
| **Agent** | An AI that does a job in steps on its own (search then read then write). |
| **RAG** | The AI reads real documents before answering, so emails cite real proof. |
| **CRM** | Software sales teams use to track customers. |

## Docs

| Doc | What it's for |
|---|---|
| [docs/differentiators.md](docs/differentiators.md) | The one differentiator, signal-triggered timing, and the trust layer under it |
| [docs/02-architecture.md](docs/02-architecture.md) | How the parts connect, the tech stack, the data model |
