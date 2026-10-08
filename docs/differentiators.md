# What makes ours different

Fed this brief, every team's AI returns the same plan: a multi-agent loop, RAG, a 3-step cadence, a ranked queue, CRM sync. Ours and the other concepts we've seen are near-identical. **The idea won't win this. One thing will** — so we go deep on it, not wide across ten half-features. Build the demo and the pitch around it.

## The differentiator: signal-triggered timing

Every other tool scores each lead once and hands you a ranked list. The order is frozen the moment it's built — open it today or next week, same list. It can't see that a company just posted a job, raised money, or changed leadership.

**Ours keeps watching.** The agent re-checks each company for fresh buying signals on its own, and the moment one fires, that lead jumps the queue with a plain-English reason and a source link. The rep opens the app and the right lead is already waiting at the top.

Why this is the edge, not "explainable scoring":
- Explainable scoring is now table stakes — everyone will have it, so it can't be the reason to pick us.
- In sales, **timing beats everything**. The same email lands a reply today and gets ignored a week later. A perfect lead at the wrong moment is a dead lead.
- Nobody else will build this. They'll all stop at "here's your scored list."

### What counts as a buying signal
- **New job posting** — hiring for the exact role our product supports or replaces.
- **Funding announced** — fresh budget; the clearest buy-now moment in B2B.
- **Leadership change** — a new VP re-evaluates tools in their first 90 days.
- **Tech-stack change** — adopted or dropped a tool that sits next to ours.
- **Product / expansion** — a launch or new market means new operational pain.
- **Engagement** — opened, clicked, or replied; intent to act on now.

Each signal is scored for **recency + relevance** — a signal from today outranks a higher base score from a cold lead.

### How it flows: watch → verify → surface
1. **Watch** — lightweight checks against real sources (search, news, job boards) on a schedule, autonomously.
2. **Verify** — a detected event is confirmed against its source before it counts. No source, no signal. (The trust layer.)
3. **Score** — the verified signal is weighted for recency + relevance and merged into the lead's score.
4. **Surface** — if it clears the bar, the lead jumps the queue as an "Act now" card, reason and source attached.

It's **RAG** (grounded in real sources) and **agentic** (keeps watching on its own) — the two things Spazor Labs builds every day.

### The demo moment
A live "Act now" feed. Each card: *who* + *the thing that just happened* + *why it matters today*, with a clickable source. In front of the judges, a signal fires and the lead visibly jumps to the top of the queue.

**The line:** *"Every other tool tells you who to call. Ours tells you to call them this afternoon — before your competitor does."*

## The trust layer underneath (keeps it defensible)

Timing is the headline, but it only matters if the system is one a real client could switch on. Spazor Labs holds ISO 42001 (AI governance), so this lands with them specifically:

- **Verified before surfaced** — no signal reaches you without a real source. The agent won't invent a reason to act.
- **Human approves the send** — nothing goes out on its own; optional auto-send only above a score you set.
- **Full audit log** — every signal, score and action logged; you can see exactly why the system did what it did.
- **Privacy by default** — minimal PII, unsubscribe enforced in code, deleting a lead wipes its data.
- **Honest scoring** — the score explains itself today, validated on a synthetic set, and is built to tune on reps' real decisions as they use it. No invented ground truth claimed.

## How this changes the build

- Spend the hours on the **signal watcher + the "Act now" queue + the live demo**, not on a trained-from-scratch model.
- Get **real CRM sync** working live — competing concepts have marked it "not done."
- Use a real web UI so it reads as shippable.
- Name everything we cut as a deliberate **roadmap**, so depth reads as focus, not as unfinished work.
