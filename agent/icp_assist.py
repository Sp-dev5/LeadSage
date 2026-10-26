"""
A fuller wording for an ideal-customer field, from the few words a rep typed.

The rep types "Head or VP of Sales"; the local model suggests a description the scorer can
use (titles, seniority, team, what they own). It is only a suggestion: the page shows it and
the rep keeps, edits or ignores it. Nothing is saved until the rep saves the form.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.llm import WRITE_TEMPERATURE, generate_structured

FIELDS = {
    "buyer_persona": (
        "the person who buys: job titles (with the common variants), seniority, the team they run "
        "and its size, what they are measured on, and who they report to"),
    "pain_solved": (
        "the problem the product solves: what the buyer's team does today, what that costs them in "
        "time or missed deals, and what they would notice if it went away"),
    "notes": (
        "what is sold and the signals that a company is ready to buy: the product in one plain "
        "sentence, who it needs on the customer side, strong signals and weak signals"),
}

SYSTEM = """You help a B2B sales team describe its ideal customer so leads can be scored and \
searched against it. Rewrite the rep's short text for one field into a fuller description of \
2 to 4 plain sentences.

Rules:
- Keep the rep's meaning. Expand it; don't change who or what it is about.
- Only describe the field you are asked for. The other fields only tell you what the product is. \
Never carry a fact, number, funding stage, company type or buying signal from them into this field \
unless the rep's own text for this field says it.
- Work from the rep's words alone. You have no list of companies, no customer data and no web pages.
- Do add what is generally true of the role or problem itself, so the result is longer and fuller \
than the rep's text: for a buyer, the seniority, the team they run, what they are measured on and \
who they report to.
- Plain, specific words a salesperson would use. No marketing language, no buzzwords, no \
em dashes, no lists, no headings.
- Don't invent facts about the rep's own product, prices or customers that the text doesn't give.
- Write it as a description, not as advice ("Head or VP of Sales at...", not "You should target...")."""


class Suggestion(BaseModel):
    text: str = Field(description="The fuller description, 2 to 4 plain sentences.")


TITLES_RULES = """
- Also list 6 to 8 other job titles for this same role at the same seniority and function, the ones a \
salesperson would look for on a team page. Real, common titles only, two to five words each, \
no company names, none the rep already wrote, no em dashes. Leave out more junior titles and \
other functions."""

MAX_TITLES = 8
MAX_TITLE_WORDS = 5


class BuyerSuggestion(BaseModel):
    text: str = Field(description="The fuller description, 2 to 4 plain sentences.")
    titles: list[str] = Field(description="6 to 8 other job titles that hold the same role.")


def _prompt(field: str, draft: str, context: dict[str, str]) -> str:
    if field not in FIELDS:
        raise ValueError(f"unknown field {field!r}")
    others = "\n".join(f"{k}: {v.strip() or '(empty)'}" for k, v in context.items() if k != field)
    return (f"FIELD: {field} ({FIELDS[field]})\n"
            f"REP'S TEXT: {draft.strip() or '(empty: write a first version from the context)'}\n"
            f"OTHER FIELDS, for context only:\n{others}")


def _plain(text: str) -> str:
    return " ".join(text.replace(" — ", ", ").replace("—", ", ").split())


def clean_titles(titles: list[str], draft: str) -> list[str]:
    """Titles that are short, new to the rep's text and not repeated."""
    kept, seen, written = [], set(), draft.lower()
    for raw in titles:
        title = _plain(raw).strip(" .,;")
        key = title.lower()
        if not title or len(title.split()) > MAX_TITLE_WORDS or key in seen or key in written:
            continue
        seen.add(key)
        kept.append(title)
    return kept[:MAX_TITLES]


def suggest(field: str, draft: str, context: dict[str, str]) -> str:
    """A fuller wording for `field` from the rep's `draft`, with the other fields as context."""
    result = generate_structured(system=SYSTEM, user_prompt=_prompt(field, draft, context), schema=Suggestion,
                                 temperature=WRITE_TEMPERATURE)
    return _plain(result.text)


def suggest_buyer(draft: str, context: dict[str, str]) -> tuple[str, list[str]]:
    """The buyer-persona wording plus other titles for the same role, from one model call."""
    result = generate_structured(system=SYSTEM + TITLES_RULES, user_prompt=_prompt("buyer_persona", draft, context),
                                 schema=BuyerSuggestion, temperature=WRITE_TEMPERATURE)
    return _plain(result.text), clean_titles(result.titles, draft)
