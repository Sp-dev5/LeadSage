"""
A fuller wording for an ideal-customer field, from the few words a rep typed.

The rep types "Head or VP of Sales"; the local model suggests a description the scorer can
use (titles, seniority, team, what they own). It is only a suggestion: the page shows it and
the rep keeps, edits or ignores it. Nothing is saved until the rep saves the form.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.llm import generate_structured

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
- Only describe the field you are asked for. Use the other fields as context, never copy them.
- Plain, specific words a salesperson would use. No marketing language, no buzzwords, no \
em dashes, no lists, no headings.
- Don't invent facts about the rep's own product, prices or customers that the text doesn't give.
- Write it as a description, not as advice ("Head or VP of Sales at...", not "You should target...")."""


class Suggestion(BaseModel):
    text: str = Field(description="The fuller description, 2 to 4 plain sentences.")


def suggest(field: str, draft: str, context: dict[str, str]) -> str:
    """A fuller wording for `field` from the rep's `draft`, with the other fields as context."""
    if field not in FIELDS:
        raise ValueError(f"unknown field {field!r}")
    others = "\n".join(f"{k}: {v.strip() or '(empty)'}" for k, v in context.items() if k != field)
    prompt = (f"FIELD: {field} ({FIELDS[field]})\n"
              f"REP'S TEXT: {draft.strip() or '(empty: write a first version from the context)'}\n"
              f"OTHER FIELDS, for context only:\n{others}")
    result = generate_structured(system=SYSTEM, user_prompt=prompt, schema=Suggestion)
    return " ".join(result.text.replace(" — ", ", ").replace("—", ", ").split())
