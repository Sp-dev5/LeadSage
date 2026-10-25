"""
Lead scoring for LeadSage.

Takes an ideal-customer profile (ICP) and a company. The model answers factual
questions about the company (each backed by a quote) and rates buyer and pain fit.
Plain Python turns the facts into industry and size scores, applies the ICP's rules,
and computes a transparent fit score. Every number shows its reasons. Location is
never scored: it is reported (country, in the ICP region or not) for filtering.

A local model (Ollama) does both calls; see agent/llm.py. Borderline results are
scored again by a second local model; if the two disagree, the lead goes to review.

Run it:  python -m agent.score_lead   (needs `ollama serve` running)
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Generic, Literal, TypeVar, get_args

import geonamescache
import pycountry
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agent.llm import generate_structured, model_name, second_model_name

ALL_CRITERIA = ["industry match", "company size match", "buyer persona fit", "pain match"]

COUNTRY_ALIASES = {"USA": "US", "US": "US", "UK": "GB", "UAE": "AE"}

CITY_CLEAR_WIN = 3

NO_TEXT_FLAG = "needs research: no text about the company"

YesNo = Literal["yes", "no", "unknown"]
Customers = Literal["businesses", "consumers", "mixed", "unknown"]
SalesMotion = Literal["no_sales", "founder_led", "outbound_team", "sales_team_unclear", "unknown"]
Offering = Literal["own_software", "services", "marketplace", "non_software", "other_unclear", "unknown"]
PrimaryBusiness = Literal["software", "services", "unclear"]

V = TypeVar("V")


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
    primary_business: Fact[PrimaryBusiness]


FACT_VALUES: dict[str, tuple[str, ...]] = {
    name: get_args(field.annotation.model_fields["value"].annotation)
    for name, field in CompanyFacts.model_fields.items() if name != "what_they_sell"
} | {"offering": get_args(Offering)}
NO_QUOTE_NEEDED = ("no", "unknown", "unclear")
REPORTED_FACTS = [f for f in FACT_VALUES if f != "offering"]
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
    max_size_factor: float = Field(
        ge=1, description="more employees than company_size_max times this is a hard fail (3 = three times the largest)")
    size_tolerance: float = Field(
        ge=0, le=1, description="how far outside the size range still scores 3, as a fraction (0.1 = 10%)")
    unknown_score: int = Field(
        ge=0, le=5, description="score for size, buyer or pain when the text says nothing")
    gate_fail_max: int = Field(ge=0, le=5, description="a gate criterion scoring this or lower fails the lead")
    fail_cap: int = Field(
        ge=0, le=100, description="the highest fit a failed lead can get (failed gate, disqualify rule, hard fail)")
    disqualify_below: int = Field(
        ge=0, le=100, description="fit below this is disqualify; also where the second-opinion band starts")
    qualify_at: int = Field(
        ge=0, le=100, description="fit at or above this is qualify; also where the second-opinion band ends")
    disagree_gap: int = Field(
        ge=0, le=100, description="fit difference between the two models that sends a lead to review")
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


class SalesRolesConfig(BaseModel):
    """Which open jobs count as sales hiring. `titles` match job titles word for word;
    `definition` is what the model judges other titles against."""

    model_config = ConfigDict(extra="forbid")

    definition: str
    titles: list[str] = Field(min_length=1)


class ICP(BaseModel):
    """The ideal customer we're selling to. Unknown keys are rejected, so a typo or a
    leftover field can't sit in the file looking like it does something."""

    model_config = ConfigDict(extra="forbid")

    buyer_persona: str
    pain_solved: str
    notes: str = ""
    sales_roles: SalesRolesConfig
    company_size_min: int
    company_size_max: int
    geography_countries: list[str]
    gates: list[str]
    industry_map: list[IndustryRule]
    industry_default: int = Field(ge=0, le=5)
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
        unknown = [g for g in v if g not in ALL_CRITERIA]
        if unknown:
            raise ValueError(f"Unknown gate criteria {unknown}; use names from {ALL_CRITERIA}")
        return v


class SalesRole(BaseModel):
    """An open sales job, copied from the company's job board."""

    title: str
    match: Literal["exact", "inferred"]
    source: str
    fetched_at: str


class Company(BaseModel):
    """What we know about a prospect company before outreach."""

    name: str
    industry: str
    size: str
    location: str
    description: str
    sales_roles: list[SalesRole] = []


class Rating(BaseModel):
    """A 0-5 score with the evidence behind it."""

    evidence: str
    score: int = Field(ge=0, le=5)


class CriterionScore(Rating):
    """A rating for one named ICP criterion."""

    criterion: str


class QuotedRating(BaseModel):
    """A model rating with the company's own words behind it. The quote comes first so
    the model finds the evidence before it picks a number."""

    quote: str
    evidence: str
    score: int = Field(ge=0, le=5)


class FitScores(BaseModel):
    """The scoring call's output. Named required fields, not a list, so the model
    can't leave one out."""

    buyer_persona_fit: QuotedRating
    pain_match: QuotedRating
    summary: str


class Location(BaseModel):
    """Where the company says it is. Shown and filtered on, never scored."""

    country: str = ""
    in_region: Literal["yes", "no", "unknown"] = "unknown"
    evidence: str = ""


class LeadScore(BaseModel):
    """The final, explainable score we hand to the rest of the app."""

    fit: int
    verdict: Literal["qualify", "review", "disqualify"]
    criteria: list[CriterionScore]
    location: Location = Location()
    disqualifier_hit: bool
    disqualifier_reason: str
    summary: str
    facts: dict[str, str] = {}
    review_flags: list[str] = []
    rejected_quotes: list[str] = []
    sales_roles: list[SalesRole] = []
    model: str = ""
    second_opinion: LeadScore | None = None


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
For each, first copy a short exact quote from the COMPANY DETAILS that supports the \
score, then write the evidence, then the score.
- buyer_persona_fit: does the company have, or need, the buyer we sell to?
- pain_match: does the company likely have the problem we solve?

Only the company's own words count as evidence. Text under WHAT WE SELL describes us, \
never the company: don't quote it.

These are often unstated. If the company details say nothing about them, score {unknown}, \
leave the quote empty and write "not mentioned." Score 0 or 1 only when a quote shows \
the company does not fit, for example no sales team, or a pain we don't solve.

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


TYPOGRAPHY = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
})


def _norm(text: str) -> str:
    """Lowercase, unify dashes and quote marks (hyphen, en and em dash, non-breaking hyphen,
    curly quotes), collapse spaces, trim quote marks and end punctuation."""
    text = text.lower().translate(TYPOGRAPHY)
    return re.sub(r"\s+", " ", text).strip().strip("\"'.,;: ")


def _source(company: Company) -> str:
    return _norm(f"{company.industry}. {company.description}")


def _quote_found(quote: str, source: str) -> bool:
    """True if the quote is in the source. A quote shortened with "..." counts when
    every piece is in the source, in the same order."""
    pieces = [p for p in (_norm(part) for part in re.split(r"\.\.\.|…", quote)) if p]
    pos = 0
    for piece in pieces:
        pos = source.find(piece, pos)
        if pos < 0:
            return False
        pos += len(piece)
    return bool(pieces)


def quote_in(quote: str, text: str) -> bool:
    """The quote rule for any text: True if the quote really appears in it."""
    return _quote_found(quote, _norm(text))


def _check_facts(facts: CompanyFacts, company: Company) -> tuple[dict[str, str], list[str]]:
    """Apply the quote rule, fill an unknown sales motion from hiring, then derive `offering`.

    Any answer other than no/unknown/unclear needs a quote that really appears in the
    company details; otherwise the fact drops to its "can't tell" value and is reported.
    Open sales jobs prove the company has salespeople, but not how they find customers,
    so they only turn an unknown sales motion into sales_team_unclear.
    """
    source = _source(company)
    checked, rejected = {}, []
    for name in REPORTED_FACTS:
        fact = getattr(facts, name)
        value = fact.value
        if value not in NO_QUOTE_NEEDED and not _quote_found(fact.quote, source):
            rejected.append(f"{name}={value}: quote {fact.quote!r} not in the company details")
            value = "unclear" if name == "primary_business" else "unknown"
        checked[name] = value
    if checked["sales_motion"] == "unknown" and company.sales_roles:
        checked["sales_motion"] = "sales_team_unclear"
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
    elif headcount > hi * s.max_size_factor:
        score, why = 0, f"{headcount} employees, over {s.max_size_factor:g} times the {hi} maximum (hard fail)"
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


def _fold(text: str) -> str:
    """Lowercase, drop accents, and strip LinkedIn-style "Greater ... Area" wrapping."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()
    plain = re.sub(r"^greater\s+|\s+(metropolitan\s+)?area$", "", plain)
    return re.sub(r"\s+", " ", plain)


@lru_cache
def _places() -> tuple[dict[str, str], dict[str, str]]:
    """Name -> country code for states/provinces (only names that belong to one country)
    and for cities (only when one country's city of that name is at least CITY_CLEAR_WIN
    times bigger than any other's: Sydney is Australian, Kochi stays unknown)."""
    owners: dict[str, set[str]] = {}
    for sub in pycountry.subdivisions:
        owners.setdefault(_fold(sub.name), set()).add(sub.country_code)
    subdivisions = {name: next(iter(codes)) for name, codes in owners.items() if len(codes) == 1}

    biggest: dict[str, dict[str, int]] = {}
    for city in geonamescache.GeonamesCache().get_cities().values():
        names = [city["name"], *(a for a in city.get("alternatenames", []) if a.isascii() and len(a) > 3)]
        for name in names:
            by_country = biggest.setdefault(_fold(name), {})
            by_country[city["countrycode"]] = max(by_country.get(city["countrycode"], 0), city["population"])
    cities = {}
    for name, by_country in biggest.items():
        ranked = sorted(by_country.items(), key=lambda kv: kv[1], reverse=True)
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if ranked[0][1] >= CITY_CLEAR_WIN * max(runner_up, 1):
            cities[name] = ranked[0][0]
    return subdivisions, cities


def _place_country(location: str) -> tuple[str, bool] | None:
    """(country code, found via a state) for a "City, State" style location.

    The parts are read from the end backwards and a state beats a city.
    """
    subdivisions, cities = _places()
    parts = [_fold(p) for p in reversed(location.split(",")) if p.strip()]
    for key in parts:
        if code := subdivisions.get(key):
            return code, True
    for key in parts:
        if code := cities.get(key):
            return code, False
    return None


def _country_code(name: str) -> str:
    return COUNTRY_ALIASES.get(name) or pycountry.countries.lookup(name).alpha_2


def locate(icp: ICP, location: str) -> Location:
    """The country a location names, and whether it is in the ICP's region.

    A country named in the text decides first. Failing that, the state or city
    ("Sydney, New South Wales") is looked up to find its country. A state can place a
    lead outside the region; a bare city can only place it inside, since a city name
    shared with a faraway one must not hide a lead from the filter.
    """
    loc = location.strip()
    in_pat, out_pat = _country_patterns(tuple(icp.geography_countries))
    region = {pycountry.countries.lookup(n).alpha_2 for n in icp.geography_countries}
    if not loc:
        return Location(evidence="No location given")
    if m := in_pat.search(loc) or out_pat.search(loc):
        code, how = _country_code(m.group(0)), f"{m.group(0)} named"
    elif (placed := _place_country(loc)) and (placed[0] in region or placed[1]):
        code, how = placed[0], "placed from the state or city"
    else:
        return Location(evidence=f"{loc}: country not recognised")
    country = pycountry.countries.get(alpha_2=code).name
    inside = code in region
    side = "in" if inside else "outside"
    return Location(country=country, in_region="yes" if inside else "no",
                    evidence=f"{loc}: {country} ({how}), {side} the ICP region")


def _check_ratings(q: FitScores, company: Company, unknown: int) -> tuple[list[CriterionScore], list[str]]:
    """The quote rule for buyer and pain.

    A score other than `unknown` needs a quote that really appears in the company
    details. Our own ICP text or an invented line is not evidence about the company,
    so the score drops to unknown and is reported. A real quote keeps the score,
    low or high.
    """
    source = _source(company)
    checked, rejected = [], []
    for name, r in (("buyer persona fit", q.buyer_persona_fit), ("pain match", q.pain_match)):
        score, evidence = r.score, r.evidence
        if score != unknown and not _quote_found(r.quote, source):
            rejected.append(f"{name}={score}: quote {r.quote!r} not in the company details")
            score, evidence = unknown, f"Score {r.score} had no quote from the company, so unknown. {r.evidence}"
        checked.append(CriterionScore(criterion=name, score=score, evidence=evidence))
    return checked, rejected


def _merge_criteria(*groups: list[CriterionScore]) -> list[CriterionScore]:
    """All criteria in a fixed order."""
    by_name = {c.criterion: c for group in groups for c in group}
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
    if capped or _failed_gates(criteria, icp):
        fit = min(fit, s.fail_cap)
    return fit


def _failed_gates(criteria: list[CriterionScore], icp: ICP) -> list[str]:
    return [c.criterion for c in criteria if c.criterion in icp.gates and c.score <= icp.settings.gate_fail_max]


def _size_fail(icp: ICP, headcount: int | None) -> str:
    """Why the headcount alone fails the lead, or "" when it doesn't."""
    if headcount is None:
        return ""
    if headcount < icp.settings.min_headcount:
        return "under the minimum headcount"
    if headcount > icp.company_size_max * icp.settings.max_size_factor:
        return "far over the maximum headcount"
    return ""


def _ruled_out(disqualified: list[str], size_fail: str, code_criteria: list[CriterionScore], icp: ICP) -> str:
    """Why the lead is already a disqualify before buyer and pain are scored, or "".

    A disqualify rule always is. A capped lead (too small, far too large, or a failed
    gate on industry or size) is too, when the cap sits below the disqualify cut-off.
    """
    if disqualified:
        return "; ".join(disqualified)
    s = icp.settings
    capped = ([size_fail] if size_fail else []) + _failed_gates(code_criteria, icp)
    if capped and s.fail_cap < s.disqualify_below:
        return "failed " + ", ".join(capped)
    return ""


def _not_rated(reason: str, unknown: int) -> list[CriterionScore]:
    return [CriterionScore(criterion=name, score=unknown, evidence=f"Not scored, already ruled out: {reason}")
            for name in ("buyer persona fit", "pain match")]


def _verdict(fit: int, disqualifier_hit: bool, s: Settings) -> Literal["qualify", "review", "disqualify"]:
    if disqualifier_hit or fit < s.disqualify_below:
        return "disqualify"
    if fit >= s.qualify_at:
        return "qualify"
    return "review"


def score_lead(icp: ICP, company: Company, model: str | None = None) -> LeadScore:
    """Score one company against the ICP with one model (default: the main one).

    A company with no description is not sent to the model: there is nothing to quote,
    so any score would be a guess. It comes back as review, fit 0, flagged for research.
    A company the facts already rule out skips the buyer and pain call.
    """
    if not company.description.strip():
        return LeadScore(
            fit=0, verdict="review", criteria=[], location=locate(icp, company.location),
            disqualifier_hit=False, disqualifier_reason="",
            summary="Not scored: no text about this company (website unreadable and no description). "
                    "Needs research.",
            review_flags=[NO_TEXT_FLAG], sales_roles=company.sales_roles,
        )
    headcount = _parse_headcount(company.size)
    size_fail = _size_fail(icp, headcount)

    raw_facts = generate_structured(
        system=FACTS_PROMPT,
        user_prompt=_company_text(company),
        schema=CompanyFacts,
        model=model,
    )
    facts, rejected = _check_facts(raw_facts, company)
    code_criteria = [_industry_criterion(icp, facts), _size_criterion(icp, headcount)]
    disqualified, review_flags = _apply_rules(icp, facts)
    unknown = icp.settings.unknown_score

    if reason := _ruled_out(disqualified, size_fail, code_criteria, icp):
        rated, summary = _not_rated(reason, unknown), f"Ruled out before buyer and pain were scored: {reason}."
    else:
        q = generate_structured(
            system=SCORE_PROMPT.replace("{unknown}", str(unknown)),
            user_prompt=_build_user_prompt(icp, company),
            schema=FitScores,
            model=model,
        )
        rated, rejected_ratings = _check_ratings(q, company, unknown)
        rejected += rejected_ratings
        summary = q.summary

    criteria = _merge_criteria(code_criteria, rated)
    hit = bool(disqualified)
    fit = _fit_from_criteria(criteria, hit or bool(size_fail), icp)
    verdict = _verdict(fit, hit, icp.settings)
    if review_flags and verdict == "qualify":
        verdict = "review"
    return LeadScore(
        fit=fit,
        verdict=verdict,
        criteria=criteria,
        location=locate(icp, company.location),
        disqualifier_hit=hit,
        disqualifier_reason="; ".join(disqualified),
        summary=summary,
        facts=facts,
        review_flags=review_flags,
        rejected_quotes=rejected,
        sales_roles=company.sales_roles,
        model=model or model_name(),
    )


def is_borderline(s: LeadScore, settings: Settings) -> bool:
    """Worth a second opinion: disqualified (by a rule or a low fit, since a wrongly
    dropped lead is the costly mistake), or in the band between the verdict cut-offs."""
    return s.verdict == "disqualify" or settings.disqualify_below <= s.fit < settings.qualify_at


def _models_disagree(a: LeadScore, b: LeadScore, settings: Settings) -> bool:
    """Different verdicts, or fits disagree_gap apart. Two disqualify verdicts agree,
    however far apart their fits: both say drop it."""
    if a.verdict == b.verdict == "disqualify":
        return False
    return a.verdict != b.verdict or abs(a.fit - b.fit) >= settings.disagree_gap


def with_second_opinion(primary: LeadScore, second: LeadScore, settings: Settings) -> LeadScore:
    """The main score with the second model's kept beside it. If the two disagree,
    the lead goes to review for a human, with a flag naming both scores."""
    result = primary.model_copy(update={"second_opinion": second})
    if _models_disagree(primary, second, settings):
        result.review_flags = [*primary.review_flags,
                               f"models disagree: {primary.model} {primary.verdict} {primary.fit}, "
                               f"{second.model} {second.verdict} {second.fit}"]
        result.verdict = "review"
    return result


def score_with_second_opinion(icp: ICP, company: Company) -> LeadScore:
    """Score with the main model; a borderline result is scored again by the second
    model. If the second model fails, the main score stands."""
    primary = score_lead(icp, company)
    if not is_borderline(primary, icp.settings):
        return primary
    try:
        second = score_lead(icp, company, model=second_model_name())
    except Exception as e:
        print(f"Second opinion unavailable ({e}); keeping the main score.")
        return primary
    return with_second_opinion(primary, second, icp.settings)


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
    industry="B2B SaaS, data analytics",
    size="120 employees",
    location="Bengaluru, India",
    description=(
        "Northwind Analytics builds a customer-data platform for mid-market retailers. "
        "They raised a Series A last quarter and just posted openings for a RevOps Lead "
        "and two account executives, signalling a sales-team build-out."
    ),
)


def _demo() -> None:
    result = score_with_second_opinion(load_icp(), SAMPLE_COMPANY)

    print(f"\n{SAMPLE_COMPANY.name}")
    print(f"  Fit: {result.fit}/100   Verdict: {result.verdict.upper()}   (scored by {result.model})")
    print(f"  {result.summary}\n")
    print(f"  Location (filter only): {result.location.evidence}")
    print("  Facts: " + ", ".join(f"{k}={v}" for k, v in result.facts.items()))
    print("  Why:")
    for c in result.criteria:
        print(f"    [{c.score}/5] {c.criterion}: {c.evidence}")
    if result.disqualifier_hit:
        print(f"\n  Disqualified: {result.disqualifier_reason}")
    for flag in result.review_flags:
        print(f"  Capped at review: {flag}")
    for r in result.rejected_quotes:
        print(f"  Quote rejected: {r}")
    for role in result.sales_roles:
        print(f"  Hiring ({role.match}): {role.title}  [{role.source}]")
    if so := result.second_opinion:
        print(f"\n  Second opinion ({so.model}): {so.fit}/100 {so.verdict.upper()}")
        for c in so.criteria:
            print(f"    [{c.score}/5] {c.criterion}: {c.evidence}")
    print()


if __name__ == "__main__":
    _demo()
