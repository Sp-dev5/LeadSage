# Tracker.md — who owns what, what's next

> The single source of truth for this build. Read this first. Update it when you
> finish something: flip the task's status and add a line to the Log.

## 0. The one goal
Build the explainable lead loop end to end: describe the ideal customer → find
companies + the right person → research → **score and explain** → draft a
personalised email → human approves → follow-ups → ranked queue → HubSpot sync.
A working, explainable slice beats a pile of half-finished features. See
`docs/differentiators.md` for what we lean on to stand out.

## 1. Lanes (work only in yours)
| Role | Owner | Area |
|---|---|---|
| A — Agents & AI | Repo owner | the agent loop, prompts, qualification, email drafting, the score + its explanation |
| B — Backend & integrations | *unassigned* | API, database, data providers, follow-up scheduler, email sending, HubSpot |
| C — Frontend | *unassigned* | lead queue, lead detail, pipeline board, metrics |
| D — RAG, data & pitch (also keeps time) | *unassigned* | knowledge base, seed data, demo content, deck, demo script, backup video |

Teammates: claim B / C / D by putting your name here (via a PR).

## 2. Status board
| Task | Owner | Status |
|---|---|---|
| Repo, workflow, branch protection | Repo owner | ✅ done |
| Agent starter practice (Steps 1–3) | Repo owner | ⬜ not started |
| Claim backend / frontend / data lanes | B/C/D | ⬜ not started |
| Decide the AI provider + get API keys | team | ⬜ not started |
| Pick the lead data source | B | ⬜ not started |

Status key: ⬜ not started · 🟡 in progress · ⏸ blocked · ✅ done

## 3. Decisions log
- 2026-10-07 — Repo name: **Odds in Your Favour**.
- 2026-10-07 — Review model: nobody pushes to `main`; every change is a PR the owner approves.

## 4. Log (newest first)
- 2026-10-07 — Lean workflow set up: CI auto-checks, coding rules, agent entry files, this tracker.
