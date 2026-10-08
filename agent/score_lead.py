"""
Lead scoring — the first atom of the LeadSage agent.

Takes an ideal-customer profile (ICP) and a company, asks Claude to rate the
company against each ICP criterion with evidence, then computes a transparent
fit score in plain Python. Nothing is a black box: every number shows its reasons.

Run it:  python -m agent.score_lead
(needs ANTHROPIC_API_KEY in your environment or a .env file — see .env.example)
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

from agent.llm import generate_structured

# How much a single disqualifier caps the fit score. A hard "no" shouldn't be
# rescued by a few strong criteria.
DISQUALIFIER_CAP = 20


class ICP(BaseModel):
    """The ideal customer we're selling to."""

    industry: str
    company_size: str          # e.g. "50-500 employees"
    geography: str             # e.g. "India / APAC"
    buyer_persona: str         # e.g. "Head of Sales / RevOps"
    pain_solved: str           # the problem our product fixes
    disqualifiers: list[str]   # things that make a company a hard no


class Company(BaseModel):
    """What we know about a prospect company before outreach."""

    name: str
    industry: str
    size: str
    location: str
    description: str           # a short brief; later this comes from the research step


class CriterionScore(BaseModel):
    """One ICP criterion, rated 0-5 with the evidence behind it."""

    criterion: str
    score: int = Field(ge=0, le=5)
    evidence: str


class Qualification(BaseModel):
    """Claude's structured verdict — the raw judgement, before we weight it."""

    criteria: list[CriterionScore]
    disqualifier_hit: bool
    disqualifier_reason: str = ""
    summary: str


class LeadScore(BaseModel):
    """The final, explainable score we hand to the rest of the app."""

    fit: int                   # 0-100
    verdict: Literal["qualify", "review", "disqualify"]
    criteria: list[CriterionScore]
    disqualifier_hit: bool
    disqualifier_reason: str
    summary: str


SYSTEM_PROMPT = """You qualify sales leads for a B2B seller.

Given an ideal-customer profile (ICP) and a company, rate the company against \
each ICP criterion on a 0-5 scale, where 0 = no match and 5 = perfect match. \
For every criterion, give one short sentence of concrete evidence drawn from \
the company description — never invent facts. If the description doesn't support \
a criterion, score it low and say the evidence is missing.

Rate these five criteria, using exactly these names:
- industry match
- company size match
- geography match
- buyer persona fit
- pain match

Then decide whether any of the ICP's disqualifiers apply to this company. \
A disqualifier applies only if the description gives real evidence for it.

Keep the summary to one or two plain sentences a salesperson could read at a glance."""


def _build_user_prompt(icp: ICP, company: Company) -> str:
    return (
        "IDEAL CUSTOMER PROFILE\n"
        f"- Industry: {icp.industry}\n"
        f"- Company size: {icp.company_size}\n"
        f"- Geography: {icp.geography}\n"
        f"- Buyer persona: {icp.buyer_persona}\n"
        f"- Problem we solve: {icp.pain_solved}\n"
        f"- Disqualifiers: {', '.join(icp.disqualifiers) or 'none'}\n\n"
        "COMPANY\n"
        f"- Name: {company.name}\n"
        f"- Industry: {company.industry}\n"
        f"- Size: {company.size}\n"
        f"- Location: {company.location}\n"
        f"- About: {company.description}\n"
    )


def _fit_from_criteria(criteria: list[CriterionScore], disqualifier_hit: bool) -> int:
    """Turn the 0-5 criterion scores into a 0-100 fit score, in plain Python.

    Equal weight across criteria for now; the weighting lives here (not in the
    model) so it stays transparent and tunable. A disqualifier caps the score.
    """
    if not criteria:
        return 0
    total = sum(c.score for c in criteria)
    fit = round(100 * total / (5 * len(criteria)))
    if disqualifier_hit:
        fit = min(fit, DISQUALIFIER_CAP)
    return fit


def _verdict(fit: int, disqualifier_hit: bool) -> Literal["qualify", "review", "disqualify"]:
    if disqualifier_hit or fit < 40:
        return "disqualify"
    if fit >= 70:
        return "qualify"
    return "review"


def score_lead(icp: ICP, company: Company) -> LeadScore:
    """Score one company against the ICP. Returns an explainable LeadScore."""
    q = generate_structured(
        system=SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(icp, company),
        schema=Qualification,
    )

    fit = _fit_from_criteria(q.criteria, q.disqualifier_hit)
    return LeadScore(
        fit=fit,
        verdict=_verdict(fit, q.disqualifier_hit),
        criteria=q.criteria,
        disqualifier_hit=q.disqualifier_hit,
        disqualifier_reason=q.disqualifier_reason,
        summary=q.summary,
    )


# --- A seeded ICP + company so this runs out of the box ----------------------

SAMPLE_ICP = ICP(
    industry="B2B SaaS",
    company_size="50-500 employees",
    geography="India / APAC",
    buyer_persona="Head of Sales or RevOps",
    pain_solved="Sales reps waste hours on manual prospecting and miss the right moment to reach out.",
    disqualifiers=["fewer than 10 employees", "not a software company", "no sales team"],
)

SAMPLE_COMPANY = Company(
    name="Northwind Analytics",
    industry="B2B SaaS — data analytics",
    size="120 employees",
    location="Bengaluru, India",
    description=(
        "Northwind Analytics builds a customer-data platform for mid-market retailers. "
        "They raised a Series A last quarter and just posted openings for a RevOps Lead "
        "and two account executives, signalling a sales-team build-out."
    ),
)


def _demo() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit(
            "GEMINI_API_KEY is not set.\n"
            "Copy .env.example to .env, add your free key from "
            "https://aistudio.google.com/, then:\n"
            "  export $(grep -v '^#' .env | xargs)   # load the key\n"
            "  python -m agent.score_lead"
        )

    result = score_lead(SAMPLE_ICP, SAMPLE_COMPANY)

    print(f"\n{SAMPLE_COMPANY.name}")
    print(f"  Fit: {result.fit}/100   Verdict: {result.verdict.upper()}")
    print(f"  {result.summary}\n")
    print("  Why:")
    for c in result.criteria:
        print(f"    [{c.score}/5] {c.criterion} — {c.evidence}")
    if result.disqualifier_hit:
        print(f"\n  Disqualified: {result.disqualifier_reason}")
    print()


if __name__ == "__main__":
    _demo()
