# Worked example: one lead, start to finish

Real output from LeadSage on a **made-up company**. Northwind Payroll, its website and Ananya Rao do not exist; the repo is public, so no real prospect is shown. Everything below the input is what the app produced (scoring on the local `qwen3.5:9b`, the email on Groq's free `gpt-oss-120b`), copied from the lead store and its trace. Run on 10 November 2026.

## 1. Input

What the app read as the company's website text:

> Northwind Payroll runs payroll, compliance and salary advances for growing Indian companies. Finance teams at more than 900 businesses use Northwind to pay employees on time and file PF, ESI and TDS without spreadsheets. Our sales team works with HR and finance leaders at companies of 50 to 2,000 people. We closed our Series A in August 2026 to expand across Southeast Asia, starting with Singapore and Malaysia.

Plus two open jobs from `northwind.example/careers` (Sales Development Representative; Account Executive, Southeast Asia), 120 employees, Bengaluru.

## 2. Scoring: facts first, each with a quote

The model answers fixed questions about the company and must quote the text for every "yes". Code then checks each quote is really in the text; a fact whose quote is missing becomes "unknown".

| Fact | Answer | Quote the model gave |
|---|---|---|
| builds own software | yes | "Northwind Payroll runs payroll, compliance and salary advances" |
| customers | businesses | "Finance teams at more than 900 businesses" |
| sales motion | outbound team | "Our sales team works with HR and finance leaders" |
| services / marketplace / non-profit | no | (no quote needed for "no") |

Quote check: 9 facts kept, 0 rejected. Rules from `data/icp.json` (size, country, disqualifiers): nothing fired.

## 3. Scoring: the verdict, with a reason per number

| Criterion | Score | Why |
|---|---|---|
| Industry match | 5/5 | payroll, compliance and salary advances (own software, sells to businesses). Looked up in a table, not judged by the model. |
| Company size | 5/5 | 120 employees, within 50 to 500. Checked in code. |
| Buyer persona fit | 4/5 | "Our sales team works with HR and finance leaders at companies of 50 to 2,000 people": an existing sales team, not a self-serve or founder-only business. |
| Pain match | 2/5 | The text says nothing about Northwind's own sales being slow or manual; it is hiring, so its sales process looks like it is scaling, not broken. |

**Fit 80, qualify.** Bengaluru, India: in the ideal-customer region.

The same run, for contrast: **Lumen Tutors** (a tutoring app parents book in) came back disqualify 20, "consumer (B2C) product", flagged "no sales team (self-serve)". **Brightdesk Studio** (a design agency) came back disqualify 20, industry 0/5 because it sells services, not its own software.

## 4. The email, every claim tied to a source

> **Subject:** Hiring a Sales Development Representative
>
> Hi Ananya,
>
> You are hiring a Sales Development Representative.<sup>1</sup> Your finance teams serve over 900 businesses.<sup>2</sup>
>
> We research the companies, check how well they fit and choose when to write, so your reps spend less time building lists and more time talking to buyers.
>
> Who will create the first prospect lists for the new SDR?
>
> Shaivi, LeadSage, 12 Example Road, Bengaluru
>
> If this isn't for you, just let me know and I won't email again.

| # | Their words | Source | Check |
|---|---|---|---|
| 1 | "Sales Development Representative" | northwind.example/careers | kept |
| 2 | "Finance teams at more than 900 businesses use Northwind" | company website text | kept |

Flag shown to the rep: *pitch or ask names SDR, found in neither their text nor ours: check it.*

**What the checks miss, shown on purpose.** Claim 2 passed because the quote is real, but the sentence gets it wrong: the 900 businesses are Northwind's customers, not something its finance teams serve. A quote check proves the words exist, not that the sentence reads them right. That is why nothing is sent until a person approves it, and here the rep would edit that line.

## 5. The audit trail

Every step is kept on the lead's page ("What the agent did"), with the full prompt and the raw answer for each model call.

**Scoring run, 7 steps:** Company text · Read the facts (`qwen3.5:9b`, 9.0 s) · Check the fact quotes · Apply the rules · Rate buyer and pain (`qwen3.5:9b`, 6.6 s) · Check the rating quotes · Fit and verdict

**Email run, 4 steps:** Find the person · Write the email (`gpt-oss-120b`, 3.3 s) · Check the claims · Saved the first email

The raw answer from "Write the email", before any check:

```json
{"subject": "Hiring a Sales Development Representative",
 "claims": [{"quote": "Sales Development Representative",
             "sentence": "You are hiring a Sales Development Representative."},
            {"quote": "Finance teams at more than 900 businesses use Northwind",
             "sentence": "Your finance teams serve over 900 businesses."}],
 "pitch": "We research the companies, check how well they fit and choose when to write, so your reps spend less time building lists and more time talking to buyers.",
 "ask": "Who will create the first prospect lists for the new SDR?"}
```

And the check that ran on it:

```json
{"kept": ["You are hiring a Sales Development Representative.",
          "Your finance teams serve over 900 businesses."],
 "cut": []}
```

![The audit trail on the lead page: each step, the prompt sent and the answer back](../images/trace.webp)
