# District 02 — Requirements & Brief Analysis

**Challenge:** AI-Powered Lead Generation, Qualification & Sales Automation Platform
**Sponsor:** Spazor Labs · **Tags:** Agentic AI, Sales Automation, RAG
**Victory condition:** an intelligent sales automation system that *reduces manual prospecting effort* while *helping sales teams prioritize and act on the highest-potential leads*.

---

## 1. What the victory condition actually rewards

Read the victory condition literally. It names two outcomes, and every feature should be justified against one of them:

1. **Less manual prospecting** — the system does the research a rep would do (find companies, find the right person, read their site/news, write the first email, follow up).
2. **Better prioritization** — the system tells the rep *which* leads to act on *now* and *why*.

A platform that does twelve things shallowly but never shows "here are your top 5 leads today, here's why, here's the drafted email, click approve" will lose to one that nails that loop. **The demo must show time saved and a ranked, explained queue.**

## 2. Who Spazor Labs is (and what that implies)

Findings (sources at the end):

- Chennai-based digital agency/studio, founded 2025, roughly 10–50 people, operating across India, UK, UAE, Canada, Germany, Nigeria, Singapore.
- Core pitch: *"design, build and deploy AI assistants, workflow automation and scalable digital platforms."* Their own tagline for AI work: **"Assistants grounded in your own documents. Agents that finish the job."** That is literally RAG + agentic AI, the two advanced tags on this brief.
- Also sells CRM/ERP implementation, cloud/DevOps, and digital marketing. Clients skew small and mid-size businesses (≈85%), in healthcare, manufacturing, media, fintech, education.
- Holds ISO 27001 (security), 27701 (privacy) and **42001 (AI management system)** certifications.
- Minimal public GitHub; they are a services shop, not a product company.

**What that implies they will value (inference, not stated by them):**

| Signal | What to do about it |
|---|---|
| They sell agents + RAG to clients | Agents must visibly *do work* end to end, and RAG must visibly *change outputs* (cited proof points in emails, ICP match grounded in our own docs). Decorative RAG will be spotted. |
| Agency serving SMBs | Something they could plausibly white-label for a client: configurable ICP, pluggable data sources, real CRM sync, deployable. |
| ISO 42001 / 27701 | Responsible AI is a differentiator: human approval before anything is sent, explanations for every score, audit log of agent actions, PII handling, opt-out/suppression list. |
| Also do digital marketing | They know outreach. Generic "Hi {first_name}, I noticed your company…" emails will be judged harshly. Personalization must reference real, specific research. |
| Services shop, CRM implementers | Real integration with a real CRM (HubSpot free tier) beats a fake "CRM" table. |

## 3. Objectives decomposed

Priority: **P0** = must work live in the demo · **P1** = should work, can be thinner · **P2** = stretch / show as designed-not-built.

| # | Objective | What "done" means for a hackathon | Priority | Honest risk |
|---|---|---|---|---|
| 1 | ICP identification & lead discovery | User defines ICP (industry, size, geo, tech, pain signals) in a form *or* the agent derives it from the seller's docs via RAG. System returns matching companies. | P0 | **Data access is the #1 risk.** No free, legal, high-volume B2B database exists. LinkedIn scraping violates ToS. Need a provider API with a free tier + a seeded fallback dataset. |
| 2 | Company & decision-maker identification | For each company, find 1–3 likely buyers (title/role match to ICP persona) with name, title, LinkedIn URL, email if available. | P0 | Email-finding APIs have tiny free quotas. Plan for demo-scale (tens of leads), not thousands. |
| 3 | Automated company/person research | Research agent reads company site, recent news, job posts; produces a structured brief (what they do, recent triggers, likely pains) with source links. | P0 | Web search API needed; cap pages per lead to control latency/cost. |
| 4 | AI-powered lead qualification | Score fit against ICP with a written rationale (e.g., BANT/fit criteria), grounded in research + our knowledge base. Qualified / nurture / disqualify. | P0 | Must be explainable, not a bare number. |
| 5 | Personalized email generation | First-touch email referencing specific research findings + a relevant proof point retrieved from the KB, with citations visible to the rep. Editable. | P0 | Quality is judged subjectively; spend real time on prompts and examples. |
| 6 | Three-step outreach & follow-up | Configurable sequence (e.g., Day 0 intro, Day 3 value-add, Day 7 breakup), each step generated in context of prior steps, auto-stopped on reply. | P0 | Real cold sending in a demo is a deliverability and legal risk. Use a sandbox inbox and **time compression** (1 day = 1 minute) to show the full sequence live. |
| 7 | Engagement & lifecycle tracking | Lead states (New → Researched → Qualified → Contacted → Engaged → Meeting → Won/Lost), event timeline (sent, opened, clicked, replied). | P1 | Opens/clicks need tracking pixels/links; replies need inbox polling. Simulated events are acceptable if labelled. |
| 8 | AI opportunity / conversion scoring | Combined score = fit + intent signals + engagement, recomputed on every event, with a "why" breakdown. Drives the priority queue. | P0 | **No historical conversion data exists**, so a "trained ML model" would be trained on synthetic data. Judges from an AI shop will see through that. Use a transparent weighted model with LLM-extracted features; say so. |
| 9 | Agentic AI workflows | Multi-step agent graph (discover → research → qualify → draft → sequence) with tool use, retries, and human-in-the-loop approval gates. Agent trace visible in UI. | P0 | Easy to over-engineer. One orchestrator graph with clear nodes beats a "swarm". |
| 10 | RAG-based knowledge base | Upload seller's docs (product sheet, case studies, pricing, objection handling). Used in ICP derivation, qualification, email proof points, and reply handling. Citations shown. | P0 | Must measurably affect outputs or it is decoration. |
| 11 | CRM integration | Push qualified leads as Contacts/Companies/Deals to HubSpot (free CRM, private-app token), sync stage changes. | P1 | Stick to one CRM, one direction (+ stage sync). Salesforce sandbox setup eats hours. |
| 12 | Pipeline & analytics dashboard | Kanban of lifecycle stages, priority queue, funnel metrics (discovered → qualified → contacted → replied), time-saved estimate. | P1 | Don't burn hours on charts; 4–5 meaningful metrics. |

### Stretch (P2) — only after P0/P1 are solid
- AI reply classification (interested / not now / objection / unsubscribe) with suggested response drafted from KB.
- Intent signals: hiring posts, funding news, tech-stack changes as score boosters.
- Multi-tenant / white-label config (one workspace per client) — directly relevant to an agency.
- A/B variants of email step 1 with per-variant reply rates.
- Chat assistant over the pipeline ("which leads in fintech replied this week?").

## 4. Non-functional requirements

- **Human-in-the-loop by default**: nothing is sent without approval (with an optional "auto-approve above score X" toggle).
- **Explainability**: every score and qualification decision has a rationale and source links.
- **Auditability**: agent action log per lead (which tool, what input, what result).
- **Compliance basics**: unsubscribe link, suppression list, no scraping of sites whose ToS forbids it, PII stored minimally. Relevant law: India DPDP Act 2023, GDPR, CAN-SPAM.
- **Cost/latency guardrails**: per-lead budget on search calls and LLM tokens; cache research.
- **Demo resilience**: seeded dataset + recorded fallback so a rate-limited API doesn't kill the live demo.

## 5. Scope risks, stated plainly

1. **Twelve objectives is too many to build deeply.** Treat 1–6, 8, 9, 10 as the core loop; 7, 11, 12 as thin-but-real; everything else is stretch.
2. **Lead data is the bottleneck, not the AI.** Decide data providers on day 0 and verify free-tier limits with a real API call before building on them.
3. **"AI conversion scoring" without outcome data is not machine learning.** Be upfront in the pitch; show the scoring is explainable and designed to learn once real outcomes exist.
4. **Real cold email in a demo is a liability.** Send to team-owned inboxes only.
5. **Multi-day sequences can't be shown in real time.** Build time compression in from the start, not as a hack at the end.

## 6. Open questions for the team

- Hackathon duration and team size? Answered: 24 hours, team of 4 (The Industry Games, ACM SIGGRAPH SRMIST).
- Is there an LLM provider / API credit given by organisers?
- Do judges want a live deployed URL or a local demo?
- Who is the demo "seller"? Recommendation: **pitch Spazor Labs itself as the seller** (their services as the product, their case studies as the KB). It's concrete, it flatters the sponsor, and the ICP is easy to define.

---
Sources: [spazorlabs.com](https://www.spazorlabs.com/), [GoodFirms profile](https://www.goodfirms.co/company/spazorlabs), [GitHub org](https://github.com/spazorlabs)
