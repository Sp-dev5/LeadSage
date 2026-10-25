"""
Leads and emails as CSV, for a CRM import or a spreadsheet. Plain columns, one row per lead
or per email; nothing here talks to a model or the network.
"""

from __future__ import annotations

import csv
import io

from agent.readiness import held_back_by
from agent.store import Email, StoredLead

LEAD_COLUMNS = ["company", "website", "verdict", "fit", "held_back_by", "country", "headcount", "location",
                "industry", "contact", "stage", "open_sales_roles", "summary", "scored_at"]
EMAIL_COLUMNS = ["company", "website", "contact", "step", "status", "subject", "body", "hook", "flags",
                 "drafted_at", "sent_at"]


def _csv(columns: list[str], rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


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
