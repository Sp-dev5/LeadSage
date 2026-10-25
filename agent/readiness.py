"""
How ready each lead is for outreach, in numbers the dashboard can show.

A lead is worth contacting when its verdict gets an email drafted (data/outreach.json).
It is ready to review when its first email is still a draft, opens with a hook (a timely
reason to write) and has a named person to write to. Nothing here talks to a model or the
network: it only reads what the lead store already holds.
"""

from __future__ import annotations

from typing import NamedTuple

from agent.outreach import OutreachConfig
from agent.score_lead import LeadScore
from agent.store import Email, StoredLead

HOOK_LABELS = {"open sales roles": "Hiring", "recent news": "News"}
ROLES_HOOK_TEXT = "they have open sales roles"


class Readiness(NamedTuple):
    worth_contacting: bool
    drafted: bool
    hook: str
    contact: str
    status: str
    ready: bool


def hook_label(email: Email | None) -> str:
    """"Hiring", "News" or "" for the hook a first email opens with. Emails drafted before the kind
    was stored are told apart by their text."""
    if email is None or not email.hook:
        return ""
    kind = email.hook_kind or ("open sales roles" if email.hook.startswith(ROLES_HOOK_TEXT) else "recent news")
    return HOOK_LABELS.get(kind, "News")


def readiness(lead: StoredLead, emails: list[Email], contact_name: str, cfg: OutreachConfig) -> Readiness:
    first = next((e for e in emails if e.step == 0), None)
    hook = hook_label(first)
    status = first.status if first else ""
    return Readiness(
        worth_contacting=lead.score.verdict in cfg.draft_verdicts,
        drafted=first is not None,
        hook=hook,
        contact=contact_name,
        status=status,
        ready=bool(first and status == "draft" and hook and contact_name),
    )


def funnel(rows: list[Readiness]) -> list[tuple[str, int]]:
    """Counts for the readiness chart, from everything scored to emails sent."""
    return [
        ("Scored", len(rows)),
        ("Worth contacting", sum(r.worth_contacting for r in rows)),
        ("Email drafted", sum(r.drafted for r in rows)),
        ("Has a hook", sum(bool(r.hook) for r in rows)),
        ("Has a named contact", sum(r.drafted and bool(r.contact) for r in rows)),
        ("Ready to review", sum(r.ready for r in rows)),
        ("Approved or sent", sum(r.status in ("approved", "sent") for r in rows)),
        ("Sent", sum(r.status == "sent" for r in rows)),
    ]


def todo(r: Readiness) -> str:
    """The one thing to do next for a lead whose first email is not yet sent, in plain words."""
    if not r.drafted:
        return "Draft the first email" if r.worth_contacting else "No email for this verdict"
    if r.status == "approved":
        return "Send it, then mark it sent"
    if r.status == "stopped":
        return "Sequence stopped"
    missing = [what for what, have in (("a contact", r.contact), ("a hook", r.hook)) if not have]
    return "Review and approve" if not missing else "Add " + " and ".join(missing)


GROUPS = ["Qualify", "Strong fit", "Review", "Disqualify", "All"]


def group(score: LeadScore, qualify_at: int) -> str:
    """The dashboard tab a lead sits in. A review lead whose fit reaches the qualify cut-off was
    held back by a rule (sales motion unknown, models disagree...), so it is shown as a strong fit
    with that reason instead of being lost among the other review leads."""
    if score.verdict == "review" and score.fit >= qualify_at:
        return "Strong fit"
    return score.verdict.capitalize()


def held_back_by(score: LeadScore) -> str:
    """Why a lead is not a qualify, in a few words, or "" when nothing held it back."""
    if score.disqualifier_hit:
        return score.disqualifier_reason
    return "; ".join(score.review_flags)


QUEUES = ["All", "Ready to review", "Needs a contact", "Needs a hook", "Approved", "Sent"]


def in_queue(r: Readiness, queue: str) -> bool:
    waiting = r.drafted and r.status == "draft"
    return {
        "All": True,
        "Ready to review": r.ready,
        "Needs a contact": waiting and not r.contact,
        "Needs a hook": waiting and not r.hook,
        "Approved": r.status == "approved",
        "Sent": r.status == "sent",
    }[queue]
