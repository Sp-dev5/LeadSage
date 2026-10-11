"""
Lead scoring — the first atom of the LeadSage agent.

Takes an ideal-customer profile (ICP) and a company. The model answers factual
questions about the company (each backed by a quote) and rates buyer and pain fit.
Plain Python turns the facts into industry, size and geography scores, applies the
ICP's rules, and computes a transparent fit score. Every number shows its reasons.

Ollama (local) scores first. If it fails, Gemini takes over. Borderline results get
a Gemini second opinion; if the two disagree, the lead goes to "review".

Run it:  python -m agent.score_lead   (needs `ollama serve` running)
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Generic, Literal, TypeVar, get_args

import pycountry
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agent.llm import generate_structured

# Every number that depends on the customer lives in the ICP file (`settings`),
# not here. What stays in code is the mechanism and facts that are true for anyone.

ALL_CRITERIA = ["industry match", "company size match", "geography match",
                "buyer persona fit", "pain match"]

# Short forms pycountry doesn't know. Matched case-sensitively so "us" in text doesn't count.
COUNTRY_ALIASES = {"USA": "US", "US": "US", "UK": "GB", "UAE": "AE"}

# --- Facts the model reports. Closed value lists, so code can apply rules to them.

YesNo = Literal["yes", "no", "unknown"]
Customers = Literal["businesses", "consumers", "mixed", "unknown"]
SalesMotion = Literal["no_sales", "founder_led", "outbound_team", "sales_team_unclear", "unknown"]
Offering = Literal["own_software", "services", "marketplace", "non_software", "other_unclear", "unknown"]
PrimaryBusiness = Literal["software", "services", "unclear"]

V = TypeVar("V")


# `quote` has no default on purpose: a default makes it optional in the JSON schema,
# and the model then skips it. It comes first so the model finds evidence before answering.
class Fact(BaseModel, Generic[V]):
    quote: str
    value: V


class CompanyFacts(BaseModel):
    """Factual answers about the company. Exclusion checks come before the positive one."""

    what_they_sell: str
    is_services: Fact[YesNo]
    is_marketplace: Fact[YesNo]
    is_non_software: Fact[YesNo]
    builds_own_software: Fact[YesNo]
    customers: Fact[Customers]
    sales_motion: Fact[SalesMotion]
    sells_sales_tools: Fact[YesNo]
    primary_business: Fact[PrimaryBusiness]   # only used when software and services both apply


# Every fact a rule may test, with its allowed values, read from the schema above so the
# two can't drift. `offering` is derived by code, not reported by the model.
FACT_VALUES: dict[str, tuple[str, ...]] = {
    name: get_args(field.annotation.model_fields["value"].annotation)
    for name, field in CompanyFacts.model_fields.items() if name != "what_they_sell"
} | {"offering": get_args(Offering)}
# Answers that need no quote: they say "nothing found" or "can't tell".
NO_QUOTE_NEEDED = ("no", "unknown", "unclear")
REPORTED_FACTS = [f for f in FACT_VALUES if f != "offering"]
# Exclusion checks, in order, and the offering each one means.
EXCLUSIONS = [("is_services", "services"), ("is_marketplace", "marketplace"),
              ("is_non_software", "non_software")]


class IndustryRule(BaseModel):
    """One row of the industry table. A missing field matches anything."""

    offering: str | None = None
    customers: str | None = None
    score: int = Field(ge=0, le=5)

    @model_validator(mode="after")
    def _values_exist(self) -> IndustryRule:
        for fact in ("offering", "customers"):
            v = getattr(self, fact)
            if v is not None and v not in FACT_VALUES[fact]:
                raise ValueError(f"industry_map: {fact}={v!r} can never occur; allowed {FACT_VALUES[fact]}")
        return self


class FactRule(BaseModel):
    """If a fact has this value: disqualify the lead, or cap its verdict at review."""

    fact: str
    value: str
    label: str
    action: Literal["disqualify", "review"] = "disqualify"

    @model_validator(mode="after")
    def _value_exists(self) -> FactRule:
        # A value the model can never produce is a dead rule; fail loudly instead.
        if self.fact not in FACT_VALUES:
            raise ValueError(f"fact_rules: unknown fact {self.fact!r}; use one of {list(FACT_VALUES)}")
        if self.value not in FACT_VALUES[self.fact]:
            raise ValueError(f"fact_rules: {self.fact}={self.value!r} can never occur; "
                             f"allowed {FACT_VALUES[self.fact]}")
        return self


class Settings(BaseModel):
    """The customer's numbers. All required, no defaults: a missing one stops the load
    with a message naming it, so no hidden value decides a lead."""

    model_config = ConfigDict(extra="forbid")

    min_headcount: int = Field(ge=0, description="fewer employees than this is a hard fail")
    size_tolerance: float = Field(
        ge=0, le=1, description="how far outside the size range still scores 3, as a fraction (0.1 = 10%)")
    unknown_score: int = Field(
        ge=0, le=5, description="score for size, geography, buyer or pain when the text says nothing")
    gate_fail_max: int = Field(ge=0, le=5, description="a gate criterion scoring this or lower fails the lead")
    fail_cap: int = Field(
        ge=0, le=100, description="the highest fit a failed lead can get (failed gate, disqualify rule, hard fail)")
    disqualify_below: int = Field(
        ge=0, le=100, description="fit below this is disqualify; also where the second-opinion band starts")
    qualify_at: int = Field(
        ge=0, le=100, description="fit at or above this is qualify; also where the second-opinion band ends")
    disagree_gap: int = Field(
        ge=0, le=100, description="fit difference between two models that sends a lead to review")
    weights: dict[str, float] = Field(
        description=f"how much each criterion counts in the fit, one number each for {ALL_CRITERIA}")

    @model_validator(mode="after")
    def _consistent(self) -> Settings:
        if self.qualify_at <= self.disqualify_below:
            raise ValueError("qualify_at must be higher than disqualify_below")
        if set(self.weights) != set(ALL_CRITERIA):
            missing = [c for c in ALL_CRITERIA if c not in self.weights]
            extra = [c for c in self.weights if c not in ALL_CRITERIA]
            raise ValueError(f"weights need exactly {ALL_CRITERIA}; missing {missing}, unknown {extra}")
        if any(w < 0 for w in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError("weights can't be negative and can't all be 0")
        return self


class ICP(BaseModel):
    """The ideal customer we're selling to. Unknown keys are rejected, so a typo or a
    leftover field can't sit in the file looking like it does something."""

    model_config = ConfigDict(extra="forbid")

    buyer_persona: str         # e.g. "Head of Sales / RevOps"
    pain_solved: str           # the problem our product fixes
    notes: str = ""            # anything else: what we sell, buying signals
    # Structured versions of size and geography, scored by code (not the model).
    company_size_min: int
    company_size_max: int
    geography_countries: list[str]   # real country names, not regions
    gates: list[str]                 # criteria that fail the lead when they score <= gate_fail_max
    industry_map: list[IndustryRule]  # first matching row sets the industry score
    industry_default: int = Field(ge=0, le=5)  # industry score when no industry_map row matches
    fact_rules: list[FactRule]
    settings: Settings

    @field_validator("geography_countries")
    @classmethod
    def _countries_exist(cls, v: list[str]) -> list[str]:
        for name in v:
            try:
                pycountry.countries.lookup(name)
            except LookupError:
                raise ValueError(f"Unknown country {name!r} in geography_countries") from None
        return v

    @field_validator("gates")
    @classmethod
    def _gates_exist(cls, v: list[str]) -> list[str]:
        # A typo here would silently switch the gate off.
        unknown = [g for g in v if g not in ALL_CRITERIA]
        if unknown:
            raise ValueError(f"Unknown gate criteria {unknown}; use names from {ALL_CRITERIA}")
        return v


class Company(BaseModel):
    """What we know about a prospect company before outreach."""

    name: str
    industry: str
    size: str
    location: str
    description: str           # a short brief; later this comes from the research step


class Rating(BaseModel):
    """A 0-5 score with the evidence behind it."""

    evidence: str              # before score, so the model reasons before it picks a number
    score: int = Field(ge=0, le=5)


class CriterionScore(Rating):
    """A rating for one named ICP criterion."""

    criterion: str


class FitScores(BaseModel):
    """The scoring call's output. Named required fields, not a list, so the model
    can't leave one out."""

    buyer_persona_fit: Rating
    pain_match: Rating
    summary: str


class LeadScore(BaseModel):
    """The final, explainable score we hand to the rest of the app."""

    fit: int                   # 0-100
    verdict: Literal["qualify", "review", "disqualify"]
    criteria: list[CriterionScore]
    disqualifier_hit: bool
    disqualifier_reason: str
    summary: str
    facts: dict[str, str] = {}               # checked facts, after the quote rule
    review_flags: list[str] = []             # review rules that capped the verdict
    rejected_quotes: list[str] = []          # facts dropped to unknown: quote not in the text
    provider: str = ""                       # which model produced this
    second_opinion: LeadScore | None = None  # set when a 2nd model also scored it


# Two calls per lead. The facts call never sees our ICP, so it can't confuse our
# product with theirs.
FACTS_PROMPT = """You extract facts about a company from its details. Judge only from \
the text given; never invent facts.

Answer each question. For each one, first copy a short exact quote from the text that \
supports the answer, then give the answer. A "yes" or any specific value needs a quote. \
If the text gives no sign of it, answer "no" (yes/no questions) or "unknown", with an \
empty quote.

- what_they_sell: in a few words, what the company sells.
- is_services: does it earn money by billing for staff time or projects \
(IT outsourcing, agencies, consultancies)? A software subscription or licence \
is not services.
- is_marketplace: does it earn fees or commission on transactions between other \
buyers and sellers?
- is_non_software: does it sell physical goods or run a physical business \
(food, restaurants, retail shops, manufacturing)?
- builds_own_software: does it sell a software product it built itself \
(licences or subscriptions)?
- customers: who pays it?
  - businesses: companies of any size; SMEs, small businesses and enterprises all count.
  - consumers: individuals or households.
  - mixed: both, substantially.
  - unknown: the text doesn't say.
- sales_motion: who sells today? Check in this order and pick the first that fits:
  - no_sales: customers sign up and buy without talking to anyone (self-serve, \
product-led, no sales team).
  - founder_led: founders or executives do the selling; no dedicated sales hires.
  - outbound_team: dedicated sales staff who reach out to prospects (SDRs, BDRs, \
outbound described).
  - sales_team_unclear: has salespeople, but the text doesn't say how they find customers.
  - unknown: the text doesn't say.
- sells_sales_tools: does its product help sales teams find, research or contact prospects?
- primary_business: where does most of its revenue come from? Go by revenue. If the \
text gives one side's share, the other side is the rest: software at "a quarter of \
revenue" means three quarters come from services, so services is primary. A small \
product line beside client work means services.
  - software: its own software product.
  - services: staff time, projects, implementation or consulting.
  - unclear: the text doesn't make it clear which is bigger."""

SCORE_PROMPT = """You qualify sales leads for a B2B seller. Judge only from the \
company details given; never invent facts.

Rate these two criteria on a 0-5 scale (0 = no match, 5 = perfect match). \
Write the evidence first, then the score.
- buyer_persona_fit: does the company have, or need, the buyer we sell to?
- pain_match: does the company likely have the problem we solve?

These are often unstated. If the description says nothing about them, score {unknown} and \
write "not mentioned." Score 0 or 1 only when there is positive evidence the company \
does not fit, for example no sales team, or a pain we don't solve.

Then write a summary of one or two plain sentences a salesperson could read at a glance."""


def _company_text(company: Company) -> str:
    return (
        "COMPANY DETAILS\n"
        f"- Name: {company.name}\n"
        f"- Industry: {company.industry}\n"
        f"- Size: {company.size}\n"
        f"- Location: {company.location}\n"
        f"- About: {company.description}\n"
    )


def _build_user_prompt(icp: ICP, company: Company) -> str:
    """Prompt for the scoring call: the company plus what we sell."""
    return (
        _company_text(company)
        + "\nWHAT WE SELL\n"
        f"- Buyer persona: {icp.buyer_persona}\n"
        f"- Problem we solve: {icp.pain_solved}\n"
        + (f"- Notes: {icp.notes}\n" if icp.notes else "")
    )


def _norm(text: str) -> str:
    """Lowercase, unify dashes, collapse spaces, trim quote marks and end punctuation."""
    text = text.lower().replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", text).strip().strip("\"'.,;: ")


def _check_facts(facts: CompanyFacts, company: Company) -> tuple[dict[str, str], list[str]]:
    """Apply the quote rule, then derive `offering`.

    Any answer other than no/unknown/unclear needs a quote that really appears in the
    company details; otherwise the fact drops to its "can't tell" value and is reported.
    """
    source = _norm(f"{company.industry}. {company.description}")
    checked, rejected = {}, []
    for name in REPORTED_FACTS:
        fact = getattr(facts, name)
        value = fact.value
        if value not in NO_QUOTE_NEEDED:
            quote = _norm(fact.quote)
            if not quote or quote not in source:
                rejected.append(f"{name}={value}: quote {fact.quote!r} not in the company details")
                value = "unclear" if name == "primary_business" else "unknown"
        checked[name] = value
    checked["offering"] = _derive_offering(checked)
    checked["what_they_sell"] = facts.what_they_sell
    return checked, rejected


def _derive_offering(facts: dict[str, str]) -> str:
    """Exclusions first: one yes picks that bucket, two or more is a conflict.

    Own software plus a services arm (and nothing else) is settled by the primary
    business; if that's unclear it stays a conflict, which can't trip the gate.
    """
    hits = [offering for fact, offering in EXCLUSIONS if facts[fact] == "yes"]
    if hits == ["services"] and facts["builds_own_software"] == "yes":
        return {"software": "own_software", "services": "services"}.get(
            facts["primary_business"], "other_unclear")
    if len(hits) > 1:
        return "other_unclear"
    if hits:
        return hits[0]
    if facts["builds_own_software"] == "yes":
        return "own_software"
    return "unknown"


def _industry_criterion(icp: ICP, facts: dict[str, str]) -> CriterionScore:
    offering, customers = facts["offering"], facts["customers"]
    score = icp.industry_default
    for rule in icp.industry_map:
        if rule.offering in (None, offering) and rule.customers in (None, customers):
            score = rule.score
            break
    why = f"{facts['what_they_sell']} (offering: {offering}, customers: {customers})"
    return CriterionScore(criterion="industry match", score=score, evidence=why)


def _apply_rules(icp: ICP, facts: dict[str, str]) -> tuple[list[str], list[str]]:
    """Labels of the disqualify rules and the review rules that fired."""
    fired = [r for r in icp.fact_rules if facts.get(r.fact) == r.value]
    return ([r.label for r in fired if r.action == "disqualify"],
            [r.label for r in fired if r.action == "review"])


def _parse_headcount(size: str) -> int | None:
    """First number in the size text: "1,200 employees" -> 1200, "1.5k" -> 1500."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kK]\b)?", size.replace(",", ""))
    if not m:
        return None
    n = float(m.group(1))
    return round(n * 1000 if m.group(2) else n)


def _size_criterion(icp: ICP, headcount: int | None) -> CriterionScore:
    lo, hi, s = icp.company_size_min, icp.company_size_max, icp.settings
    if headcount is None:
        score, why = s.unknown_score, "Headcount not stated (unknown)"
    elif headcount < s.min_headcount:
        score, why = 0, f"{headcount} employees, under the {s.min_headcount} minimum (hard fail)"
    elif lo <= headcount <= hi:
        score, why = 5, f"{headcount} employees, within {lo}-{hi}"
    elif lo * (1 - s.size_tolerance) <= headcount <= hi * (1 + s.size_tolerance):
        score, why = 3, f"{headcount} employees, just outside {lo}-{hi}"
    else:
        score, why = 1, f"{headcount} employees, well outside {lo}-{hi}"
    return CriterionScore(criterion="company size match", score=score, evidence=why)


def _country_names(country) -> set[str]:
    return {getattr(country, a) for a in ("name", "common_name", "official_name")
            if getattr(country, a, None)}


@lru_cache
def _country_patterns(in_region: tuple[str, ...]) -> tuple[re.Pattern, re.Pattern]:
    """Whole-word patterns for in-region countries and every other country.

    In-region names expand to all their spellings ("Vietnam" also matches "Viet Nam").
    """
    region = {pycountry.countries.lookup(n).alpha_2 for n in in_region}
    inside, outside = set(), set()
    for c in pycountry.countries:
        (inside if c.alpha_2 in region else outside).update(_country_names(c))

    def words(names):
        return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))

    in_alias = [a for a, code in COUNTRY_ALIASES.items() if code in region]
    out_alias = [a for a, code in COUNTRY_ALIASES.items() if code not in region]
    in_pat = rf"(?i:\b(?:{words(inside)})\b)" + (rf"|\b(?:{words(in_alias)})\b" if in_alias else "")
    out_pat = rf"(?i:\b(?:{words(outside)})\b)" + (rf"|\b(?:{words(out_alias)})\b" if out_alias else "")
    return re.compile(in_pat), re.compile(out_pat)


def _geography_criterion(icp: ICP, location: str) -> CriterionScore:
    """In-region country -> 5, a recognised other country -> 1, anything else -> unknown_score.

    Only a positively identified out-of-region country scores low enough to trip the gate.
    """
    loc = location.strip()
    in_pat, out_pat = _country_patterns(tuple(icp.geography_countries))
    if not loc:
        score, why = icp.settings.unknown_score, "No location given (unknown)"
    elif m := in_pat.search(loc):
        score, why = 5, f"{loc}: {m.group(0)} is in the target region"
    elif m := out_pat.search(loc):
        score, why = 1, f"{loc}: {m.group(0)} is outside the target region"
    else:
        score, why = icp.settings.unknown_score, f"{loc}: country not recognised (unknown)"
    return CriterionScore(criterion="geography match", score=score, evidence=why)


def _merge_criteria(model: FitScores, code: list[CriterionScore]) -> list[CriterionScore]:
    """The model's two ratings plus the code criteria, in a fixed order."""
    by_name = {
        "buyer persona fit": CriterionScore(criterion="buyer persona fit", **model.buyer_persona_fit.model_dump()),
        "pain match": CriterionScore(criterion="pain match", **model.pain_match.model_dump()),
    }
    by_name.update({c.criterion: c for c in code})
    return [by_name[n] for n in ALL_CRITERIA]


def _fit_from_criteria(criteria: list[CriterionScore], capped: bool, icp: ICP) -> int:
    """Turn the 0-5 criterion scores into a 0-100 fit score, in plain Python.

    A weighted average, with the customer's weights. A hard fail (`capped`) or a
    gate criterion scoring <= gate_fail_max caps the score at fail_cap, so strong
    other scores can't float it.
    """
    s = icp.settings
    total_weight = sum(s.weights[c.criterion] for c in criteria)
    if not criteria or total_weight <= 0:
        return 0
    weighted = sum(s.weights[c.criterion] * c.score for c in criteria)
    fit = round(100 * weighted / (5 * total_weight))
    gate_failed = any(c.criterion in icp.gates and c.score <= s.gate_fail_max for c in criteria)
    if capped or gate_failed:
        fit = min(fit, s.fail_cap)
    return fit


def _verdict(fit: int, disqualifier_hit: bool, s: Settings) -> Literal["qualify", "review", "disqualify"]:
    if disqualifier_hit or fit < s.disqualify_below:
        return "disqualify"
    if fit >= s.qualify_at:
        return "qualify"
    return "review"


def score_once(provider: str, icp: ICP, company: Company) -> LeadScore:
    """Score one company with one specific model: no fallback, no second opinion."""
    # Facts code can read straight off the form: identical whichever model runs.
    headcount = _parse_headcount(company.size)
    too_small = headcount is not None and headcount < icp.settings.min_headcount

    raw_facts = generate_structured(
        system=FACTS_PROMPT,
        user_prompt=_company_text(company),
        schema=CompanyFacts,
        provider=provider,
    )
    q = generate_structured(
        system=SCORE_PROMPT.replace("{unknown}", str(icp.settings.unknown_score)),
        user_prompt=_build_user_prompt(icp, company),
        schema=FitScores,
        provider=provider,
    )

    facts, rejected = _check_facts(raw_facts, company)
    code_criteria = [_industry_criterion(icp, facts), _size_criterion(icp, headcount),
                     _geography_criterion(icp, company.location)]
    criteria = _merge_criteria(q, code_criteria)
    disqualified, review_flags = _apply_rules(icp, facts)
    hit = bool(disqualified)
    fit = _fit_from_criteria(criteria, hit or too_small, icp)
    verdict = _verdict(fit, hit, icp.settings)
    if review_flags and verdict == "qualify":
        verdict = "review"
    return LeadScore(
        fit=fit,
        verdict=verdict,
        criteria=criteria,
        disqualifier_hit=hit,
        disqualifier_reason="; ".join(disqualified),
        summary=q.summary,
        facts=facts,
        review_flags=review_flags,
        rejected_quotes=rejected,
        provider=provider,
    )


def _is_borderline(s: LeadScore, settings: Settings) -> bool:
    """Worth a second opinion: disqualified, or in the band between the verdict cut-offs."""
    return s.disqualifier_hit or settings.disqualify_below <= s.fit < settings.qualify_at


def _models_disagree(a: LeadScore, b: LeadScore, settings: Settings) -> bool:
    return a.verdict != b.verdict or abs(a.fit - b.fit) >= settings.disagree_gap


def score_lead(icp: ICP, company: Company) -> LeadScore:
    """Score one company against the ICP. Returns an explainable LeadScore.

    Ollama first. If it fails, Gemini does the job. If the result is borderline,
    Gemini also scores it, and a disagreement turns the verdict into "review".
    """
    try:
        primary = score_once("ollama", icp, company)
    except Exception as e:
        print(f"Ollama failed ({e}); falling back to Gemini.")
        return score_once("gemini", icp, company)

    if not _is_borderline(primary, icp.settings):
        return primary

    try:
        second = score_once("gemini", icp, company)
    except Exception as e:
        print(f"Gemini second opinion unavailable ({e}); keeping the Ollama score.")
        return primary

    primary.second_opinion = second
    if _models_disagree(primary, second, icp.settings):
        primary.verdict = "review"
        primary.summary = (
            f"Models disagree (Ollama {primary.fit}, Gemini {second.fit}); needs a human look. "
            + primary.summary
        )
    return primary


# --- A seeded company so this runs out of the box ----------------------

ICP_PATH = Path(__file__).resolve().parent.parent / "data" / "icp.json"


def load_icp(path: Path = ICP_PATH) -> ICP:
    """Read the ideal-customer profile from a JSON file, so it can change without code edits.

    A missing or bad setting stops here with a message naming it and what it means.
    """
    try:
        return ICP.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        lines = []
        for err in e.errors():
            loc = err["loc"]
            hint = ""
            if loc == ("settings",) and err["type"] == "missing":
                hint = f" (needs: {', '.join(Settings.model_fields)})"
            elif len(loc) >= 2 and loc[0] == "settings" and loc[1] in Settings.model_fields:
                hint = f" ({Settings.model_fields[loc[1]].description})"
            lines.append(f"- {'.'.join(str(p) for p in loc) or 'settings'}: {err['msg']}{hint}")
        raise ValueError(f"{path.name} is not a valid ICP:\n" + "\n".join(lines)) from None


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
    result = score_lead(load_icp(), SAMPLE_COMPANY)

    print(f"\n{SAMPLE_COMPANY.name}")
    print(f"  Fit: {result.fit}/100   Verdict: {result.verdict.upper()}   (scored by {result.provider})")
    print(f"  {result.summary}\n")
    print("  Facts: " + ", ".join(f"{k}={v}" for k, v in result.facts.items()))
    print("  Why:")
    for c in result.criteria:
        print(f"    [{c.score}/5] {c.criterion} — {c.evidence}")
    if result.disqualifier_hit:
        print(f"\n  Disqualified: {result.disqualifier_reason}")
    for flag in result.review_flags:
        print(f"  Capped at review: {flag}")
    for r in result.rejected_quotes:
        print(f"  Quote rejected: {r}")
    if result.second_opinion:
        so = result.second_opinion
        print(f"\n  Second opinion ({so.provider}): {so.fit}/100 {so.verdict.upper()}")
        for c in so.criteria:
            print(f"    [{c.score}/5] {c.criterion} — {c.evidence}")
    print()


if __name__ == "__main__":
    _demo()
