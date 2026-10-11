# agent — the LeadSage agent

This is the brain: the steps that find, research, score and write to leads.
It's built one piece at a time. Lead scoring is here.

## Lead scoring (`score_lead.py`)

Takes an ideal-customer profile (ICP) and a company, works out the company's
facts, then computes a fit score in plain Python. The score is never a black
box: every number shows the reasons behind it. This is the atom everything
else builds on.

## The model

The app's judgment calls go through one file, `llm.py`. It uses a local
open-source model via **Ollama** by default, with **Google Gemini** as a
fallback and a second opinion on borderline leads. Switching models means
changing only `llm.py`; nothing that calls it has to change.

## Run it

```bash
# 1. install
pip install -r requirements.txt

# 2. pull the local model (default provider)
ollama pull qwen2.5:7b

# 3. (optional) add a Gemini key for the fallback
#    get one at https://aistudio.google.com/ then add to .env:
#    GEMINI_API_KEY=your-key

# 4. score a sample company
python -m agent.score_lead
```

You should see a fit score, a verdict (qualify / review / disqualify), and the
per-criterion reasons.

## Build order

1. Lead scoring — this file.
2. Walking skeleton — one seeded company goes research then score then drafted email.
3. Scoring engine — add intent + engagement on top of fit.
4. **Signal watcher** (the standout) — watch each company for fresh buying
   signals and bump it up the queue the moment one fires.
5. Approval pause — the agent stops for a human to approve before "sending".
