"""
Leads and emails as CSV, for a CRM import or a spreadsheet. Plain columns, one row per lead
or per email; nothing here talks to a model or the network. The CRM file uses HubSpot's import
column names, so it imports into its free CRM without mapping; LeadSage holds no CRM password.
"""

from __future__ import annotations

import csv
import io
from urllib.parse import urlsplit

from agent.contacts import Contact
from agent.readiness import held_back_by
from agent.store import Email, StoredLead

LEAD_COLUMNS = ["company", "website", "verdict", "fit", "held_back_by", "country", "headcount", "location",
                "industry", "contact", "stage", "open_sales_roles", "summary", "scored_at"]
EMAIL_COLUMNS = ["company", "website", "contact", "step", "status", "subject", "body", "hook", "flags",
                 "drafted_at", "sent_at"]


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def plain_cell(value):
    """A text cell a spreadsheet would read as a formula ("=HYPERLINK(...)" in a company's own
    one-liner) gets a leading apostrophe, so Excel shows it as text instead of running it."""
    return f"'{value}" if isinstance(value, str) and value.startswith(FORMULA_START) else value


def _csv(columns: list[str], rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    writer.writerows({k: plain_cell(v) for k, v in row.items()} for row in rows)
    return out.getvalue()


CRM_COLUMNS = ["Company name", "Company domain name", "First name", "Last name", "Job title", "Email",
               "Lifecycle stage", "LeadSage fit", "LeadSage verdict", "LeadSage stage", "Why", "Source"]


def split_name(name: str) -> tuple[str, str]:
    """First word, then the rest: "Rohit Taneja" -> ("Rohit", "Taneja"); one word is a first name."""
    first, _, last = " ".join(name.split()).partition(" ")
    return first, last


def lifecycle(lead: StoredLead) -> str:
    """HubSpot's lifecycle stage (its internal value, which its import accepts) for a lead."""
    if lead.stage == "meeting":
        return "opportunity"
    return "salesqualifiedlead" if lead.score.verdict == "qualify" else "lead"


def crm_csv(leads: list[StoredLead], people: dict[str, Contact | None]) -> str:
    """One row per lead, with HubSpot's import column names (Zoho and Pipedrive map them by hand).
    `people` maps a company name to the person to write to, or None."""
    rows = []
    for lead in leads:
        person = people.get(lead.name)
        first, last = split_name(person.name) if person else ("", "")
        rows.append({
            "Company name": lead.name,
            "Company domain name": urlsplit(lead.url).netloc.lower().removeprefix("www.") if lead.url else "",
            "First name": first,
            "Last name": last,
            "Job title": person.title if person else "",
            "Email": person.email if person else "",
            "Lifecycle stage": lifecycle(lead),
            "LeadSage fit": lead.score.fit,
            "LeadSage verdict": lead.score.verdict,
            "LeadSage stage": lead.stage,
            "Why": lead.score.summary,
            "Source": person.source if person and person.source.startswith("http") else lead.url,
        })
    return _csv(CRM_COLUMNS, rows)


def leads_csv(leads: list[StoredLead], contacts: dict[str, str]) -> str:
    """One row per lead. `contacts` maps a company name to the person to write to."""
    return _csv(LEAD_COLUMNS, [{
        "company": lead.name,
        "website": lead.url,
        "verdict": lead.score.verdict,
        "fit": lead.score.fit,
        "held_back_by": held_back_by(lead.score),
        "country": lead.score.location.country,
        "headcount": lead.company.size,
        "location": lead.company.location,
        "industry": lead.company.industry,
        "contact": contacts.get(lead.name, ""),
        "stage": lead.stage,
        "open_sales_roles": "; ".join(r.title for r in lead.score.sales_roles),
        "summary": lead.score.summary,
        "scored_at": lead.scored_at,
    } for lead in leads])


def emails_csv(leads: list[StoredLead], emails: dict[str, list[Email]], contacts: dict[str, str]) -> str:
    """One row per email, first email then follow-ups, for the leads given."""
    return _csv(EMAIL_COLUMNS, [{
        "company": lead.name,
        "website": lead.url,
        "contact": contacts.get(lead.name, ""),
        "step": "first" if e.step == 0 else f"follow-up {e.step}",
        "status": e.status,
        "subject": e.subject,
        "body": e.body,
        "hook": e.hook,
        "flags": "; ".join(e.flags),
        "drafted_at": e.drafted_at,
        "sent_at": e.sent_at or "",
    } for lead in leads for e in sorted(emails.get(lead.name, []), key=lambda e: e.step)])
