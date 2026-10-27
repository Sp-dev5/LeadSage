# What makes LeadSage different

Most lead tools return the same shape: a multi-agent loop, RAG, a 3-step cadence, a ranked queue, CRM sync. LeadSage goes deep on one thing: timing, with a source for every claim.

## Where this stands

Built today:
- Two timing signals, each with its source and date: open sales roles on a company's job board, and recent items (a launch, a partnership, a new hire) from its own news page.
- Every email opens with one of those as its hook, and every sentence about the company must quote its own text. Drafts with no hook are flagged.
- A person approves every email, then sends it with one click from their own mailbox or sends it themselves. Nothing is sent without that click.
- Each email ends with an opt-out line and the sender's address; a do-not-contact list and a delete-lead button remove a company's data.

Not built yet: re-checking companies on a schedule, funding and leadership signals from other sites, "act now" cards that move a lead up the queue, tracking replies, an audit log and auto-send. The rest of this page describes where it is going.

## The differentiator: signal-triggered timing

A ranked list that is scored once is frozen the moment it's built: open it today or next week, same list. It can't see that a company just posted a job, raised money, or changed leadership. Some tools do track signals, so tracking alone is not the edge. Proving each one with a source is.

**The goal is for LeadSage to keep watching.** The agent re-checks each company for fresh buying signals on its own, and the moment one fires, that lead jumps the queue with a plain-English reason and a source link. The rep opens the app and the right lead is already waiting at the top.

Why timing is the edge, not "explainable scoring":
- Explainable scoring is table stakes now; everyone has it.
- In sales, **timing beats everything**. The same email lands a reply today and gets ignored a week later. A perfect lead at the wrong moment is a dead lead.

### What counts as a buying signal
- **New job posting**: hiring for the exact role the product supports or replaces.
- **Funding announced**: fresh budget; the clearest buy-now moment in B2B.
- **Leadership change**: a new VP re-evaluates tools in their first 90 days.
- **Tech-stack change**: adopted or dropped a tool that sits next to ours.
- **Product / expansion**: a launch or new market means new operational pain.
- **Engagement**: opened, clicked, or replied; intent to act on now.

Each signal is scored for **recency + relevance**: a signal from today outranks a higher base score from a cold lead.

### How it flows: watch then verify then surface
1. **Watch**: lightweight checks against real sources (search, news, job boards) on a schedule, autonomously.
2. **Verify**: a detected event is confirmed against its source before it counts. No source, no signal. (The trust layer.)
3. **Score**: the verified signal is weighted for recency + relevance and merged into the lead's score.
4. **Surface**: if it clears the bar, the lead jumps the queue as an "Act now" card, reason and source attached.

It's **RAG** (grounded in real sources) and **agentic** (keeps watching on its own).

## The trust layer underneath

Timing only matters if the system is one a real client could switch on:

- **Verified before surfaced**: no signal reaches you without a real source. The agent won't invent a reason to act.
- **Human approves the send**: nothing goes out on its own. The rep approves each email and clicks send; the app sends one at a time, under a daily cap. Auto-send and bulk send are not built.
- **Audit log**: today each email keeps the source and date behind every line about the company. A full log of every signal, score and action is planned.
- **Privacy by default**: minimal personal data (a name and title taken from the company's own site), every email carries an opt-out line, a do-not-contact list keeps a company from being drafted for, and deleting a lead wipes its data and saved pages. When reply checking is set up, a reply asking not to be emailed puts the company on the do-not-contact list on its own.
- **Honest scoring**: the score explains itself today and is built to tune on reps' real decisions as they use it. No invented ground truth claimed.
