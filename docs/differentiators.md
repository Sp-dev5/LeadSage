# What makes ours different

Fed this brief, every team's AI returns the same plan: a multi-agent loop, RAG, a 3-step cadence, a ranked queue, CRM sync. Ours and the other concepts we've seen are near-identical. **The idea won't win this. These three things will** — build the demo and the pitch around them.

## 1. Scoring that learns from your reps, not from invented data

The tempting move is to "train a model from scratch." But there's no real sales-outcome data at a hackathon, so any such model is trained on numbers you made up — it learns the rules you used to label the data and proves nothing. An ML-literate sponsor (Spazor builds this for a living) will ask "what's your ground truth?" and it collapses.

**Our position:** a transparent score (fit + intent signals + engagement) where every number shows its reasons. The human-in-the-loop isn't just an approval gate — **each approval, edit, and won/lost outcome is a real training signal.** The system is built to be tuned on real results once they exist (a simple logistic regression over logged outcomes). Say this out loud: *it gets smarter as reps use it, on real decisions, not synthetic data.*

## 2. A glass box reps actually trust

Reps ignore black-box scores. The win is trust, and trust comes from showing the work:
- **Watch the agent run** — each step (searched, read, scored, drafted) visible live, not behind a spinner.
- **Every score justified** — "92" ships with the three facts that earned it.
- **Every email line sourced** — each claim traces to a real document or a real fact about the prospect.

This is cheap to build (you're already logging each agent step) and it's the thing judges remember after ten identical demos.

## 3. Built responsibly — the way Spazor already works

Spazor Labs holds ISO 42001 (AI governance). Most teams ship an AI that fires emails on its own; we ship one a real client could switch on:
- Approval before any send (optional "auto-send above score X").
- A full audit log of every agent action.
- Unsubscribe + suppression enforced in code, before every send.
- Minimal PII; deleting a lead wipes its data.

Each of these is a few lines of code and a visible UI element — high signal for this specific sponsor, low cost.

## How this changes the build

- Don't spend hours faking a trained model. Spend them on the **explainable score + the visible agent trace**.
- Get **real HubSpot sync** working live — competing concepts have marked it "not done."
- Use a real web UI, not a data-science prototype tool, so it reads as shippable.
- In the pitch, name everything we cut as a deliberate **roadmap**, so depth reads as focus, not as unfinished work.
