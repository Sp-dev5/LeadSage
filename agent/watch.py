"""
Signal watcher: read each lead's job board and news pages again, store what is there, and flag
what is new since the last look.

A lead's first successful watch of each kind (hiring, news) is its baseline: everything seen is
stored and nothing fires, or every lead would "fire" on day one. After that, a role or news item
not seen before fires, except a news item dated more than fresh_days ago (an old post first
spotted today is not news).

A job board or news page that can't be read is unknown, never "no roles": nothing is recorded,
nothing is compared and the lead is not rescored. A lead is rescored only when a board that was
read shows a different set of sales roles, because roles are part of the score. News never
changes a score; it only moves the lead up its tab.

Run it:  python -m agent.watch
         python -m agent.watch --names "Atlan,Fyle"
         python -m agent.watch --hiring-only          job boards only, no news (no GPU for news)
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agent import store
from agent.hooks import NewsItem
from agent.outreach import OutreachConfig, load_outreach, news_for
from agent.pipeline import score_one, signal_rows
from agent.score_lead import ICP, SalesRole, load_icp
from agent.signals import hiring_for
from agent.store import SignalEvent, StoredLead


@dataclass
class WatchResult:
    watched: int = 0
    fired: list[tuple[str, SignalEvent]] = field(default_factory=list)
    rescored: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def event_key(text: str, item_date: str = "") -> str:
    """The same item written slightly differently gives the same key: lowercase, no punctuation,
    single spaces, then the item's date if it has one."""
    words = " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())
    return f"{words}|{item_date}" if item_date else words


def is_recent(item_date: str, today: date, days: int) -> bool:
    try:
        return (today - date.fromisoformat(item_date)).days <= days
    except ValueError:
        return False


def hiring_events(roles: list[SalesRole]) -> list[SignalEvent]:
    return [SignalEvent(kind="hiring", key=event_key(r.title), detail=r.model_dump_json(), source=r.source,
                        quote=r.title) for r in roles]


def news_events(items: list[NewsItem]) -> list[SignalEvent]:
    return [SignalEvent(kind="news", key=event_key(i.headline, i.date), detail=i.model_dump_json(), source=i.source,
                        quote=i.headline, item_date=i.date) for i in items]


def record(lead: str, kind: str, events: list[SignalEvent], can_fire: Callable[[SignalEvent], bool],
           db: Path) -> list[SignalEvent]:
    """Store one kind of event for a lead after a successful read and return the ones that fired."""
    baseline = not store.is_baselined(lead, kind, db)
    known = store.known_events(lead, kind, db)
    unique = {e.key: e for e in events}.values()
    marked = [e.model_copy(update={"fired": not baseline and e.key not in known and can_fire(e)}) for e in unique]
    store.record_events(lead, marked, db)
    store.mark_watched(lead, kind, db)
    return [e for e in marked if e.fired]


def watch_hiring(lead: StoredLead, row: dict, icp: ICP, result: WatchResult, report: Callable[[str], None],
                 db: Path) -> None:
    hiring = hiring_for(row, icp, refetch=True)
    if not hiring.ok:
        result.failed.append(f"{lead.name}: job board not read ({hiring.error})")
        return
    result.fired += [(lead.name, e) for e in record(lead.name, "hiring", hiring_events(hiring.roles),
                                                    lambda e: True, db)]
    if {event_key(r.title) for r in hiring.roles} != {event_key(r.title) for r in lead.company.sales_roles}:
        company = lead.company.model_copy(update={"sales_roles": hiring.roles})
        before = lead.score
        after = score_one(icp, company, lead.url, lead.text_source, report, db)
        result.rescored.append(lead.name)
        report(f"  sales roles changed ({len(lead.company.sales_roles)} -> {len(hiring.roles)}): rescored "
               f"{before.verdict} {before.fit} -> {after.verdict} {after.fit}")


def watch_news(lead: StoredLead, cfg: OutreachConfig, today: date, result: WatchResult, db: Path) -> None:
    search = news_for(lead, cfg, db, refresh=True)
    if not search.pages_read:
        result.failed.append(f"{lead.name}: no news page could be read")
        return
    result.fired += [(lead.name, e) for e in record(lead.name, "news", news_events(search.items),
                                                    lambda e: is_recent(e.item_date, today, cfg.fresh_days), db)]


def leads_to_watch(db: Path, names: set[str] | None = None) -> list[StoredLead]:
    """Every stored lead still in play: not replied, meeting or lost, and not on the do-not-contact list."""
    blocked = store.suppressed(db)
    return [lead for lead in store.load_all(db)
            if lead.stage not in store.STOPPED and lead.name not in blocked
            and (not names or lead.name.lower() in names)]


def watch_leads(icp: ICP, cfg: OutreachConfig, db: Path = store.DB_PATH, report: Callable[[str], None] = print,
                names: set[str] | None = None, hiring_only: bool = False, signals: dict[str, dict] | None = None,
                today: date | None = None) -> WatchResult:
    """Watch every lead still in play. Hiring for leads listed in the signals file; news for leads
    whose verdict gets an email. One lead failing is reported and the run moves on."""
    signals = signal_rows() if signals is None else signals
    today = today or date.today()
    result = WatchResult()
    leads = leads_to_watch(db, names)
    for i, lead in enumerate(leads, 1):
        report(f"[{i}/{len(leads)}] {lead.name}")
        result.watched += 1
        if row := signals.get(lead.name.lower()):
            try:
                watch_hiring(lead, row, icp, result, report, db)
            except Exception as e:
                result.failed.append(f"{lead.name}: hiring check failed ({str(e)[:90]})")
        if not hiring_only and lead.url and lead.score.verdict in cfg.draft_verdicts:
            try:
                watch_news(lead, cfg, today, result, db)
            except Exception as e:
                result.failed.append(f"{lead.name}: news check failed ({str(e)[:90]})")
    for line in summary(result):
        report(line)
    return result


def summary(result: WatchResult) -> list[str]:
    lines = [f"Watched {result.watched} leads: {len(result.fired)} new signals, {len(result.rescored)} rescored, "
             f"{len(result.failed)} failures."]
    lines += [" ".join(filter(None, [f"New: {lead}: {e.kind}", repr(e.quote), e.item_date, e.source]))
              for lead, e in result.fired]
    lines += [f"Failed: {f}" for f in result.failed]
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", help="comma-separated company names to watch (default: all)")
    ap.add_argument("--hiring-only", action="store_true", help="job boards only, skip news")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    names = {n.strip().lower() for n in args.names.split(",")} if args.names else None
    watch_leads(load_icp(), load_outreach(), names=names, hiring_only=args.hiring_only,
                report=lambda msg: print(msg, flush=True))


if __name__ == "__main__":
    main()
