"""
Funding news from Indian startup media: a reason to write that the company's own site may not carry.

Reads Inc42, YourStory and Entrackr through fetch.py, so robots.txt, our own name and the size and
time caps all apply. Only the headline, link and date are kept: never the article body, and never
anything for training. Feeds show the latest few days only, so every headline is stored under
ALL_LEADS and history builds up run by run; a lead added later can still match it.

A headline counts for a lead only if the lead's full name comes directly before a raise word and
that word is followed by an amount or a round. "Delhivery posts Rs 41 Cr profit", "Cashfree-backed
Foo raises $3 Mn", a weekly roundup and "Foo in talks to raise" all stay unmatched. A one-word name
could be another company, so it also needs the lead's domain or city in the article's opening,
which is read once and dropped. Better to miss than to pin someone else's round on a lead.

A feed that can't be read is unknown, never "no news".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from agent import store
from agent.fetch import Page, fetch_page, fetch_xml
from agent.hooks import NewsItem, parse_date


class Feed(NamedTuple):
    outlet: str
    url: str


FEEDS = (
    Feed("Inc42", "https://inc42.com/feed/"),
    Feed("YourStory", "https://yourstory.com/feed"),
    Feed("Entrackr", "https://entrackr.com/news-sitemap.xml"),
)
ALL_LEADS = ""
OPENING_CHARS = 1500
SITEMAP = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
SITEMAP_NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"
RAISE_WORDS = r"(?:raises|raised|secures|bags|closes)"
ROUND = (r"(?:[$₹€£]\s?\d|(?:rs\.?|inr|usd)\s?\d|\d[\d.,]*\s?(?:mn|cr|crore|million|bn|billion|lakh)\b"
         r"|(?:pre[- ]?)?(?:series|seed)\b|funding\b)")


def _entries(root) -> list[tuple[str, str, str]]:
    """(headline, link, date) from an RSS feed or a news sitemap. Summaries and bodies are left unread."""
    rss = [(i.findtext("title"), i.findtext("link"), i.findtext("pubDate")) for i in root.iter("item")]
    sitemap = [(u.findtext(f"{SITEMAP_NEWS}news/{SITEMAP_NEWS}title"), u.findtext(f"{SITEMAP}loc"),
                u.findtext(f"{SITEMAP_NEWS}news/{SITEMAP_NEWS}publication_date"))
               for u in root.iter(f"{SITEMAP}url")]
    return [(" ".join(title.split()), link.strip(), when.strip()) for title, link, when in rss + sitemap
            if title and link and when and link.strip().lower().startswith(("https://", "http://"))]


def _is_recent(when: date, today: date, max_age_days: int) -> bool:
    return -1 <= (today - when).days <= max_age_days


def parse_feed(outlet: str, page: Page, max_age_days: int, today: date) -> list[NewsItem]:
    """The recent items in a feed. Raises ValueError if it holds no items at all: a feed whose format
    changed must not read as a quiet day."""
    entries = _entries(ElementTree.fromstring(page.text))
    if not entries:
        raise ValueError("no items found")
    items = []
    for headline, link, written in entries:
        when = parse_date(written)
        if when and _is_recent(when, today, max_age_days):
            items.append(NewsItem(headline=headline, date_text=f"{when.day} {when:%B} {when.year}",
                                  date=when.isoformat(), kind="funding", source=link, outlet=outlet,
                                  fetched_at=page.fetched_at))
    return items


def read_feeds(max_age_days: int, today: date,
               fetch: Callable[[str], Page] = fetch_xml) -> tuple[list[NewsItem], list[str]]:
    """Every recent item from the feeds that could be read, and one line for each that could not."""
    items, failed = [], []
    for feed in FEEDS:
        page = fetch(feed.url)
        if not page.ok:
            failed.append(f"{feed.outlet}: {page.error}")
            continue
        try:
            items += parse_feed(feed.outlet, page, max_age_days, today)
        except (ElementTree.ParseError, DefusedXmlException, ValueError) as e:
            failed.append(f"{feed.outlet}: {str(e)[:90]}")
    return items, failed


def matches(name: str, headline: str) -> bool:
    """True if the headline says this company raised a round: its full name, directly a raise word,
    then an amount or round, with at most one word between ("raises fresh $5 Mn")."""
    return bool(re.search(rf"(?<![\w-]){re.escape(name)}\s+{RAISE_WORDS}\s+(?:\S+\s+)?{ROUND}", headline, re.I))


def needs_opening(name: str) -> bool:
    return len(name.split()) == 1


def _city(location: str) -> str:
    """The first part of "City, State" or "City, Country". A location with one part ("Remote",
    "India") names no city."""
    parts = [p.strip() for p in location.split(",")]
    return parts[0] if len(parts) > 1 else ""


def opening_confirms(text: str, url: str, location: str) -> bool:
    """True if an article's opening carries the lead's domain or city."""
    opening = text[:OPENING_CHARS].lower()
    domain = (urlsplit(url).hostname or "").removeprefix("www.")
    city = _city(location).lower()
    return bool(domain and domain in opening) or bool(city and re.search(rf"\b{re.escape(city)}\b", opening))


def saved_news(lead: str, max_age_days: int, db: Path = store.DB_PATH, today: date | None = None) -> list[NewsItem]:
    """The press items stored for a lead (or ALL_LEADS) that are still recent enough to use."""
    today = today or date.today()
    items = [NewsItem.model_validate_json(e.detail) for e in store.events(lead, "press", db)]
    return [i for i in items if _is_recent(date.fromisoformat(i.date), today, max_age_days)]


def read_article(url: str) -> Page:
    """The article's main text, for opening_confirms only: the caller keeps the answer, not the text."""
    return fetch_page(url)
