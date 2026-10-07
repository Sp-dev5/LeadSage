# District 02 — Build Plan & Milestones

**Event:** The Industry Games (ACM SIGGRAPH SRMIST), **9–10 Oct**. Team of 4. Prep starts now. Repo owned by Boss; teammates added as collaborators. Top teams get internship opportunities, so code quality and the pitch both matter.

## 1. Guiding rules

1. **Vertical slice first.** By hour 10 one lead must go from ICP to drafted email end to end, ugly UI and all.
2. **Seeded data from hour one.** Every provider has a fallback so no one is blocked by API keys or rate limits.
3. **Demo script drives the backlog.** If a feature isn't in the 5-minute demo, cut it.
4. **Freeze features with time to spare.** The last stretch of the event is bug fixing, demo data, and rehearsal, never new features. No exceptions.
5. **Sleep in shifts.** Two people rest at a time for ~2 h overnight; a tired team ships bugs at demo time.

## 2. Roles (4 people)

| Role | Owns |
|---|---|
| **A — Agents & LLM** (Boss) | Agent pipeline, prompts, qualification, email drafting, scoring |
| **B — Backend & integrations** | API, database, data providers, sequence scheduler, email sending, HubSpot |
| **C — Frontend** | Priority queue, lead detail, pipeline board, metrics |
| **D — RAG, data & pitch** (team coordinator) | Knowledge base ingestion + retrieval, seed dataset, demo content, pitch deck, demo script, backup video. Also runs the hour-10/16/20 checks and keeps the timeline, so A stays heads-down on the critical path. |

## 3. The timeline (today → the event on 9–10 Oct)

> **Read this first — the one rule that decides everything below.** Many hackathons require that *application code* be written during the event, and pre-building can get a team disqualified. **Check The Industry Games' rules (or ask a mentor) before writing any app code ahead of time.** Everything in the "always safe" prep below is fine under any ruleset. The "only if the rules allow" items are the ones to hold back if pre-building isn't permitted.

### Phase 1 — Prep (now → 8 Oct)

**Always safe, whatever the rules say** (this is learning and logistics, not building the product):
- [ ] **Learn the tools.** The repo owner (role A) works through the agent starter guide ([agent-starter-guide.md](agent-starter-guide.md)); everyone skims the doc for their role.
- [ ] Create accounts and API keys: LLM, search API (Tavily/Exa), Hunter, HubSpot free, Gmail test inbox.
- [ ] Check each free-tier limit with one real call, and write the limits down.
- [ ] Collect Spazor Labs pages / case studies as knowledge-base content.
- [ ] Prepare a seed list of ~50 real companies matching the demo ICP.
- [ ] Agree the stack, and who takes roles B, C and D.
- [ ] Sketch the screens and the API shape between backend and frontend on paper.

**Only if the rules allow pre-building:**
- [ ] Repo scaffolding, database schema, `.env.example` (no secrets in git).
- [ ] A bare "walking skeleton": one lead goes ICP → research → qualify → draft, ugly and hardcoded, just to prove the pieces connect.

If pre-building isn't allowed, that's fine — the prep above still means you walk in knowing the tools cold and with your data and keys ready, which is most of the battle.

### Phase 2 — The event (9–10 Oct)

The event is the real build. Work in this order, not all at once:

**First third — walking skeleton**
- [ ] ICP form → discover (seeded) → find people → research (live search) → qualify → draft.
- [ ] Knowledge base ingested; the draft cites one retrieved case study.
- [ ] Lead list + lead detail page showing research, reasons, draft.
- [ ] **Checkpoint:** one lead runs end to end in front of the team.

**Second third — the core loop**
- [ ] Approve / edit a draft before sending.
- [ ] Three-step sequence with time compression (1 day = 1 minute).
- [ ] Send to a team inbox; reply detection or a labelled "simulate reply" button.
- [ ] Scoring (fit + intent + engagement) with a breakdown; priority queue home screen.
- [ ] **Checkpoint:** a full sequence plays out in under 10 minutes and a reply moves the lead up the queue.

**Last third — polish and ship**
- [ ] HubSpot sync of qualified leads and stage changes.
- [ ] Pipeline board + 4 metrics including estimated hours saved.
- [ ] Agent activity log on lead detail; unsubscribe link + suppression list.
- [ ] **Feature freeze with time to spare.** Then: clean demo data + reset script, backup demo video, pitch deck, and rehearse twice with a timer.

### Leave out (mention as "next steps" in the pitch)
- Open/click tracking pixels, live paid lead discovery, reply classification, intent signals, ICP auto-derived from the knowledge base, multi-client workspaces, public deployment if it eats time (a local demo is fine).
- Don't learn a new framework during the event. Use LangGraph only if someone already knows it; otherwise a plain Python pipeline.

## 5. Demo script (5 minutes, draft)

1. **Problem (30 s):** reps spend most of their time researching and writing, not selling.
2. **Set ICP (30 s):** Spazor Labs as the seller; ICP drafted from its own KB.
3. **Agents at work (60 s):** launch discovery; watch the trace as leads get researched and qualified with reasons.
4. **Priority queue (45 s):** top leads, score breakdown, the drafted email citing a real case study. Approve.
5. **Sequence + engagement (60 s):** time-compressed sequence; a reply arrives, score jumps, lead moves to Engaged and syncs to HubSpot.
6. **Dashboard (30 s):** funnel, hours saved.
7. **Trust (30 s):** approval gates, audit log, suppression list, explainable scoring.
8. **Close (15 s):** how an agency deploys this per client.

## 6. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Lead data API blocked/limited | High | High | Seeded dataset + provider switch from hour 0 |
| LLM rate limits / credit exhaustion | Med | High | Haiku for bulk steps, cache research, per-lead budget |
| Email deliverability / spam folder | Med | Med | Team inboxes only; simulate-reply fallback |
| Scope creep across 12 objectives | High | High | Demo script gates the backlog; stretch items stay optional |
| Integration hell late in the build | Med | High | Contracts agreed in hours 0–2; walking skeleton by hour 10 |
| Live demo failure | Med | High | Backup video + reset script + seeded workspace |
| Prompt injection from prospect websites | Low | Med | Treat fetched content as data; no tool calls driven by page text |

## 7. Repo layout (proposed)

```
/docs          requirements, architecture, build plan, providers, demo script
/apps/web      Next.js frontend
/apps/api      FastAPI backend (agents, providers, scheduler, crm)
/data/seed     seed companies/contacts, demo KB docs
/scripts       seed + reset scripts
```
