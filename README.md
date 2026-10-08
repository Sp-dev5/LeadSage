# LeadSage

**AI-powered lead generation, qualification and sales automation.**
Built for *The Industry Games* (ACM SIGGRAPH SRMIST) — District 02, sponsored by Spazor Labs.

---

## What we're building, in one paragraph

Salespeople waste most of their day on busywork: hunting for companies to sell to, reading up on each one, finding the right person's email, and writing "personal" emails one at a time. Our app does that work for them. You describe your ideal customer; the app finds matching companies, researches them, scores each lead and explains why, writes a personalised first email, and runs the follow-ups. The payoff is simple: **less busywork, plus a ranked list that tells the team who to contact first.**

## How it works (the one loop everything serves)

1. **Describe the ideal customer** — industry, size, location, the problem you solve.
2. **Find companies and the right person** at each one.
3. **Research** each company and write a short brief, with sources.
4. **Score the lead and explain why** it's worth chasing (this is the output judges care about most).
5. **Write a personalised email** that references something real about them.
6. **A human approves**, then the app sends up to 3 follow-ups and stops if they reply.
7. **The best leads rise to the top**, and everything syncs to HubSpot (free sales software).

## A few terms (so everyone's on the same page)

| Term | Plain meaning |
|---|---|
| **Lead** | A person who might buy from us. |
| **ICP** | Ideal Customer Profile — a description of the perfect customer. |
| **Agent** | An AI that does a job in steps on its own (search → read → write). |
| **RAG** | The AI reads *our* documents before answering, so emails cite real proof. |
| **CRM** | Software sales teams use to track customers. We use HubSpot's free tier. |

## The team & who owns what

| Role | Owner | Builds |
|---|---|---|
| **A — Agents & AI** | **Shaivi** | The agent that runs the 7 steps, prompts, qualification, email drafting, scoring |
| **B — Backend & integrations** | Udbhav / Siddhanth / Keerthana *(to agree)* | API, database, data providers, follow-up scheduler, email sending, HubSpot |
| **C — Frontend** | Udbhav / Siddhanth / Keerthana *(to agree)* | Lead queue, lead detail, pipeline board, metrics |
| **D — RAG, data & pitch** *(also keeps time)* | Udbhav / Siddhanth / Keerthana *(to agree)* | Knowledge base, seed data, demo content, pitch deck, demo script, backup video |

Roles B, C and D are open for Udbhav, Siddhanth and Keerthana to split between them.

## How we work

Nobody pushes straight to `main`. Every change goes on a branch, into a pull request, and is merged only after the owner reviews it. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the step-by-step.

## The documents

Read these in order. Each has a short intro at the top before the detail.

| Doc | What it's for | Read it if you're… |
|---|---|---|
| [docs/differentiators.md](docs/differentiators.md) | Our one differentiator — signal-triggered timing — and the trust layer under it | …everyone — read this |
| [docs/01-requirements.md](docs/01-requirements.md) | What the challenge asks, what the sponsor values, what to build first | …anyone — start here |
| [docs/02-architecture.md](docs/02-architecture.md) | How the parts connect, the tech stack, the data model | …building (roles A, B, C, D) |
| [docs/03-build-plan.md](docs/03-build-plan.md) | Timeline (prep now → event 9–10 Oct), roles, demo script, risks | …everyone |
| [docs/agent-starter-guide.md](docs/agent-starter-guide.md) | Three tiny practice programs to learn agents before the event | …Shaivi (role A), and anyone curious |
| [docs/challenge-brief.png](docs/challenge-brief.png) | The original challenge card | …reference |

## The honest bottom line

The brief lists 12 features. **You can't build all 12 well in the event window.** Build steps 1–7 properly, freeze new features with time to spare, and spend the end fixing bugs and rehearsing the demo. A working, explainable loop beats twelve half-finished features.

**Event is 9–10 Oct; prep starts now.** See the build plan for what's safe to do before the event and what to hold back.
