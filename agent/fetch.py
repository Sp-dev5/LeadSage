"""
Fetcher: reads one web page and returns its main text, where it came from, and when.

Checks robots.txt first and names itself in the user agent. Never raises: any problem
(blocked, timeout, 404, not HTML, no readable text) comes back as ok=False with a reason,
so one dead website can't stop a batch of leads.

Not yet called by score_lead.py.

Run it:  python -m agent.fetch https://example.com
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
import trafilatura
from pydantic import BaseModel

USER_AGENT = "LeadSage/0.1"
TIMEOUT = 10        # seconds, per request
MAX_CHARS = 4000    # prompts must stay short: the local model has a small context window


class Page(BaseModel):
    url: str
    text: str = ""
    fetched_at: str     # UTC, ISO 8601
    ok: bool
    error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fail(url: str, error: str) -> Page:
    return Page(url=url, fetched_at=_now(), ok=False, error=error)


def _allowed_by_robots(url: str) -> bool:
    """True if the site's robots.txt lets LeadSage fetch this URL.

    Same rules as RobotFileParser.read(): 401/403 means keep out, any other 4xx (no
    robots.txt) means allowed, a server error means "no". Network errors are raised
    for fetch_page to report, so a dead site doesn't read as "blocked".
    """
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    parser = RobotFileParser(robots_url)
    resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if resp.status_code in (401, 403):
        return False
    if 400 <= resp.status_code < 500:
        return True
    if resp.status_code >= 500:
        return False
    parser.parse(resp.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


def fetch_page(url: str) -> Page:
    """Download one page and return its main readable text. Never raises."""
    try:
        if urlsplit(url).scheme not in ("http", "https"):
            return _fail(url, "not an http(s) URL")
        if not _allowed_by_robots(url):
            return _fail(url, "blocked by robots.txt")

        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code >= 400:
            return _fail(url, f"HTTP {resp.status_code}")
        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return _fail(url, f"not HTML ({content_type or 'no content type'})")

        # Bytes, not resp.text: trafilatura reads the page's own charset declaration.
        text = _clean(trafilatura.extract(resp.content, url=resp.url) or "")
        if not text:
            return _fail(url, "no readable text")
        return Page(url=resp.url, text=text, fetched_at=_now(), ok=True)
    except requests.Timeout:
        return _fail(url, f"timed out after {TIMEOUT}s")
    except requests.ConnectionError:
        return _fail(url, "could not connect (site down or no such domain)")
    except requests.RequestException as e:
        return _fail(url, f"request failed: {type(e).__name__}")
    except Exception as e:  # never raise: one bad page must not stop a batch
        return _fail(url, f"{type(e).__name__}: {e}")


def _demo() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m agent.fetch <url>")
        sys.exit(1)
    sys.stdout.reconfigure(errors="replace")   # Windows consoles can't print every character
    page = fetch_page(sys.argv[1])
    print(f"ok:         {page.ok}")
    print(f"url:        {page.url}")
    print(f"fetched_at: {page.fetched_at}")
    if not page.ok:
        print(f"error:      {page.error}")
        return
    print(f"characters: {len(page.text)}")
    print()
    print(page.text[:500])


if __name__ == "__main__":
    _demo()
