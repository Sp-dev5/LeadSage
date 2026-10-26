"""
What each page shows, worked out from the lead store. Nothing here talks to a model or the
network, and nothing here writes: the routes in web/app.py do the writing.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import datetime
from typing import NamedTuple

from markupsafe import Markup

from agent import store
from agent.outreach import OutreachConfig, with_fallbacks
from agent.readiness import GROUPS, Readiness, funnel, group, held_back_by, readiness
from agent.score_lead import ICP, _parse_headcount

DB = store.DB_PATH
UNKNOWN = "Unknown"
VERDICT_ORDER = ["Qualify", "Strong fit", "Review", "Disqualify"]


class LeadRow(NamedTuple):
    lead: store.StoredLead
    emails: list[store.Email]
    ready: Readiness
    group: str

    @property
    def country(self) -> str:
        return self.lead.score.location.country or UNKNOWN

    @property
    def headcount(self) -> int | None:
        return _parse_headcount(self.lead.company.size)

    @property
    def held_back_by(self) -> str:
        return held_back_by(self.lead.score)


def lead_rows(icp: ICP, cfg: OutreachConfig) -> list[LeadRow]:
    all_emails, searches = store.load_all_emails(DB), store.load_all_contacts(DB)
    rows = []
    for lead in store.load_all(DB):
        emails = all_emails.get(lead.name, [])
        search = searches.get(lead.name)
        contact = with_fallbacks(search, lead, cfg).contact if search else None
        rows.append(LeadRow(lead, emails, readiness(lead, emails, contact.name if contact else "", cfg),
                            group(lead.score, icp.settings.qualify_at)))
    return sorted(rows, key=lambda r: (-r.lead.score.fit, r.lead.name.lower()))


def share(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def home(rows: list[LeadRow]) -> dict:
    ready = [r.ready for r in rows]
    groups = Counter(r.group for r in rows)
    emails = [e for r in rows for e in r.emails]
    steps = funnel(ready)
    scored = len(rows)
    models = Counter(r.lead.score.model for r in rows if r.lead.score.model)
    return {
        "scored": scored,
        "worth": sum(r.worth_contacting for r in ready),
        "drafted": sum(r.drafted for r in ready),
        "with_hook": sum(r.drafted and bool(r.hook) for r in ready),
        "ready_now": sum(r.ready for r in ready),
        "needs_contact": sum(r.drafted and r.status == "draft" and not r.contact for r in ready),
        "approved": sum(r.status == "approved" for r in ready),
        "sent": sum(r.status == "sent" for r in ready),
        "replied": sum(r.lead.stage in ("replied", "meeting") for r in rows),
        "kept": sum(len(e.citations) for e in emails),
        "cut": sum(len(e.rejected) for e in emails),
        "last_scored": max((r.lead.scored_at for r in rows), default=""),
        "model": models.most_common(1)[0][0] if models else "",
        "by_group": groups,
        "verdicts": [(g, groups[g], share(groups[g], scored)) for g in VERDICT_ORDER],
        "funnel": [(step, n, share(n, scored)) for step, n in steps],
        "up_next": [r for r in rows if r.ready.ready][:7],
        "strong": [r for r in rows if r.group == "Strong fit"],
    }


class LeadFilters(NamedTuple):
    group: str = "Qualify"
    countries: tuple[str, ...] = ()
    region_only: bool = False
    min_fit: int = 0
    hiring_only: bool = False

    def location_ok(self, r: LeadRow) -> bool:
        if self.countries and r.country not in self.countries:
            return False
        return not (self.region_only and r.lead.score.location.in_region == "no")

    def other_ok(self, r: LeadRow) -> bool:
        return r.lead.score.fit >= self.min_fit and (not self.hiring_only or bool(r.lead.score.sales_roles))


def leads(rows: list[LeadRow], f: LeadFilters) -> dict:
    shown = [r for r in rows if f.location_ok(r) and f.other_ok(r)]
    counts = Counter(r.group for r in shown)
    counts["All"] = len(shown)
    table = shown if f.group == "All" else [r for r in shown if r.group == f.group]
    return {
        "shown": shown,
        "table": table,
        "counts": counts,
        "groups": GROUPS,
        "hidden": len(rows) - len(shown),
        "hidden_by_location": [r for r in rows if not f.location_ok(r) and f.other_ok(r)],
        "countries": sorted({r.country for r in rows} - {UNKNOWN}) + ([UNKNOWN] if any(
            r.country == UNKNOWN for r in rows) else []),
    }


def domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else (url or "")


def bare(url: str) -> str:
    return re.sub(r"^https?://(?:www\.)?", "", url or "").rstrip("/")


def day(iso: str) -> str:
    try:
        return f"{datetime.fromisoformat(iso):%d %b %Y}"
    except ValueError:
        return iso[:10]


def stamp(iso: str) -> str:
    try:
        return f"{datetime.fromisoformat(iso).astimezone():%d %b %H:%M}"
    except ValueError:
        return iso[:16]


def letter(email: store.Email) -> Markup:
    """The email body as paragraphs, each cited sentence highlighted and numbered like a footnote."""
    body = html.escape(email.body)
    for n, c in enumerate(email.citations, 1):
        sentence = html.escape(c.sentence.strip())
        if sentence and sentence in body:
            body = body.replace(sentence, f'<span class="hl">{sentence}</span><sup>{n}</sup>', 1)
    paragraphs = [p.strip().replace("\n", "<br>") for p in re.split(r"\n\s*\n", body) if p.strip()]
    return Markup("".join(f"<p>{p}</p>" for p in paragraphs))


def is_link(source: str) -> bool:
    return source.startswith("http") and " " not in source
