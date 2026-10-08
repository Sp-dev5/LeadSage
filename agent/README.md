# agent — the LeadSage agent (Role A)

This is the brain: the steps that find, research, score and write to leads.
We build it one piece at a time. Step 1 is here.

## Step 1 — lead scoring (`score_lead.py`)

Takes an ideal-customer profile (ICP) and a company, asks Claude to rate the
company against each ICP criterion **with evidence**, then computes the fit
score in plain Python. The score is never a black box — every number shows the
reasons behind it. This is the atom everything else builds on.

## The model

The app's judgment calls go through one file, `llm.py`. Today it uses **Google
Gemini's free API**. When we later move to a local open-source model, `llm.py`
is the only file that changes — nothing that calls it has to.

## Run it

```bash
# 1. install
pip install -r requirements.txt

# 2. add your free Gemini key (never commit the real .env)
#    get one at https://aistudio.google.com/ → "Get API key"
cp .env.example .env        # then paste your key into .env
export $(grep -v '^#' .env | xargs)

# 3. run the demo on a seeded company
python -m agent.score_lead
```

You should see a fit score, a verdict (qualify / review / disqualify), and the
per-criterion reasons.

## What's next (Role A build order)

1. ✅ **Lead scoring** — this file.
2. Walking skeleton — one seeded company goes research → score → drafted email.
3. Scoring engine — add intent + engagement on top of fit.
4. **Signal watcher** (the standout) — watch each company for fresh buying
   signals and bump it up the queue the moment one fires.
5. Approval pause — the agent stops for a human to approve before "sending".
