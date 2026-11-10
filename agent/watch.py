"""
Signal watcher: read each lead's job board and news pages again, store what is there, and flag
what is new since the last look.

A lead's first successful watch of each kind (hiring, news) is its baseline: everything seen is
stored and nothing fires, or every lead would "fire" on day one. After that, a role or news item
not seen before fires, except a news item dated more than fresh_days ago (an old post first
spotted today is not news) or a plain blog post (the same rule as email hooks: a post is never
a reason to write). Posts are still stored, so the history stays complete.

A job board or news page that can't be read is unknown, never "no roles": nothing is recorded,
nothing is compared and the lead is not rescored. A lead is rescored only when a board that was
read shows a different set of sales roles, because roles are part of the score. News never
changes a score; it only moves the lead up its tab.

Funding news (agent/press.py) is read once per run, not per lead, and needs no model. Every lead
still in play is checked against it, with the same baseline rule. If no feed can be read, nothing
is recorded for any lead. --hiring-only skips it, like the other news.

Each lead's website is checked weekly (full runs): one that can't be reached shows on the lead,
and two down checks in a row suggest marking it lost. A site that refuses us has answered, so
that is not down. A full run also forgets people never emailed after forget_after_days.

When reply checking is set up (IMAP settings in .env, see agent/mailer.py), a full run also looks
for replies to contacted leads; a reply stops their follow-ups.

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

from agent import mailer, press, store, trace
from agent.fetch import UNREACHABLE, fetch_homepage
from agent.hooks import HOOK_KINDS, NewsItem
from agent.outreach import OutreachConfig, forget_people_pages, load_outreach, news_for
from agent.pipeline import score_one, signal_rows
from agent.score_lead import ICP, SalesRole, load_icp
from agent.signals import hiring_for
from agent.store import SignalEvent, StoredLead

SITE_CHECK_DAYS = 7


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


def news_events(items: list[NewsItem], kind: str = "news") -> list[SignalEvent]:
    return [SignalEvent(kind=kind, key=event_key(i.headline, i.date), detail=i.model_dump_json(), source=i.source,
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
    trace.step("Read the job board", {"sales roles": [r.title for r in hiring.roles]}, ok=hiring.ok, error=hiring.error)
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
    newsworthy = {event_key(i.headline, i.date) for i in search.items if i.kind in HOOK_KINDS}
    result.fired += [(lead.name, e) for e in record(
        lead.name, "news", news_events(search.items),
        lambda e: e.key in newsworthy and is_recent(e.item_date, today, cfg.fresh_days), db)]


def read_press(cfg: OutreachConfig, today: date, result: WatchResult, db: Path) -> list[NewsItem] | None:
    """Store what the feeds show today and return the recent history, or None if no feed could be read."""
    items, failed = press.read_feeds(cfg.news_max_age_days, today)
    result.failed += [f"news feed not read ({line})" for line in failed]
    if len(failed) == len(press.FEEDS):
        return None
    store.record_events(press.ALL_LEADS, news_events(items, "press"), db)
    return press.saved_news(press.ALL_LEADS, cfg.news_max_age_days, db, today)


def confirmed(lead: StoredLead, item: NewsItem) -> bool | None:
    """Whether a matched headline is really about this lead. None if the article can't be read, so
    the next run asks again."""
    if not press.needs_opening(lead.name):
        return True
    page = press.read_article(item.source)
    return press.opening_confirms(page.text, lead.url, lead.company.location) if page.ok else None


def watch_press(lead: StoredLead, items: list[NewsItem], cfg: OutreachConfig, today: date, result: WatchResult,
                db: Path) -> None:
    known = store.known_events(lead.name, "press", db)
    ruled_out = store.known_events(lead.name, "press_unconfirmed", db)
    matched, unconfirmed = [], []
    for item in items:
        key = event_key(item.headline, item.date)
        if key in ruled_out or not press.matches(lead.name, item.headline):
            continue
        answer = True if key in known else confirmed(lead, item)
        if answer:
            matched.append(item)
        elif answer is False:
            unconfirmed.append(item)
    store.record_events(lead.name, news_events(unconfirmed, "press_unconfirmed"), db)
    result.fired += [(lead.name, e) for e in record(lead.name, "press", news_events(matched, "press"),
                                                    lambda e: is_recent(e.item_date, today, cfg.fresh_days), db)]


def watch_site(lead: StoredLead, today: date, report: Callable[[str], None], db: Path) -> None:
    """Look at the lead's website if the last look was SITE_CHECK_DAYS ago or more."""
    last = store.site_checks(db).get(lead.name)
    if last and (today - date.fromisoformat(last.checked_at)).days < SITE_CHECK_DAYS:
        return
    page = fetch_homepage(lead.url)
    down = not page.ok and page.error.startswith(UNREACHABLE)
    check = store.record_site_check(lead.name, today.isoformat(), down, page.error, db)
    trace.step("Check the website", {"down": down, "down in a row": check.down_streak}, input=lead.url,
               ok=page.ok, error=page.error)
    if down:
        report(f"  website down ({check.down_streak} in a row): {page.error}")


def leads_to_watch(db: Path, names: set[str] | None = None) -> list[StoredLead]:
    """Every stored lead still in play: not replied, meeting or lost, and not on the do-not-contact list."""
    blocked = store.suppressed(db)
    return [lead for lead in store.load_all(db)
            if lead.stage not in store.STOPPED and lead.name not in blocked
            and (not names or lead.name.lower() in names)]


def _watch_one(lead: StoredLead, icp: ICP, cfg: OutreachConfig, signals: dict[str, dict],
               press_items: list[NewsItem] | None, hiring_only: bool, today: date, result: WatchResult,
               report: Callable[[str], None], db: Path) -> None:
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
    if press_items is not None:
        try:
            watch_press(lead, press_items, cfg, today, result, db)
        except Exception as e:
            result.failed.append(f"{lead.name}: funding news check failed ({str(e)[:90]})")
    if not hiring_only and lead.url:
        try:
            watch_site(lead, today, report, db)
        except Exception as e:
            result.failed.append(f"{lead.name}: website check failed ({str(e)[:90]})")


def watch_leads(icp: ICP, cfg: OutreachConfig, db: Path = store.DB_PATH, report: Callable[[str], None] = print,
                names: set[str] | None = None, hiring_only: bool = False, signals: dict[str, dict] | None = None,
                today: date | None = None) -> WatchResult:
    """Watch every lead still in play. Hiring for leads listed in the signals file; news for leads
    whose verdict gets an email; funding news for all of them. One lead failing is reported and
    the run moves on."""
    signals = signal_rows() if signals is None else signals
    today = today or date.today()
    result = WatchResult()
    leads = leads_to_watch(db, names)
    press_items = None if hiring_only else read_press(cfg, today, result, db)
    for i, lead in enumerate(leads, 1):
        report(f"[{i}/{len(leads)}] {lead.name}")
        result.watched += 1
        with trace.run(lead.name, "watch", db):
            _watch_one(lead, icp, cfg, signals, press_items, hiring_only, today, result, report, db)
            trace.step("Watch result", {"fired": [e.quote for name, e in result.fired if name == lead.name],
                                        "failed": [f for f in result.failed if f.startswith(f"{lead.name}:")]})
    if not hiring_only and names is None and cfg.forget_after_days:
        for name in store.forget_stale_contacts(cfg.forget_after_days, today, db):
            forget_people_pages(name, db)
            report(f"{name}: person forgotten, not emailed within {cfg.forget_after_days} days")
    if mailer.can_read() and names is None:
        try:
            mailer.check_replies(cfg, db, report)
        except Exception as e:
            result.failed.append(f"reply check failed ({str(e)[:90]})")
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
