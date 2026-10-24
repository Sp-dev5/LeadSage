"""
Fetcher: reads one web page and returns its main text, where it came from, and when.

Checks robots.txt first and names itself in the user agent. Never raises: any problem
(blocked, timeout, 404, not HTML, no readable text) comes back as ok=False with a reason,
so one dead website can't stop a batch of leads.

PUBLIC_APIS skips the robots check. Only hosts whose owner documents a public API for this
use belong there (Ashby's job postings API answers robots.txt with 401, which reads as
"keep out" even though the API is meant to be read).

Run it:  python -m agent.fetch https://example.com
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
import trafilatura
from protego import Protego
from pydantic import BaseModel

USER_AGENT = "LeadSage/0.1"
TIMEOUT = 10
MAX_CHARS = 4000
FULL_MAX_CHARS = 9000
MAX_REDIRECTS = 5
REDIRECT_CODES = (301, 302, 303, 307, 308)
PUBLIC_APIS = {"api.ashbyhq.com"}


class Page(BaseModel):
    url: str
    text: str = ""
    fetched_at: str
    ok: bool
    error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fail(url: str, error: str) -> Page:
    return Page(url=url, fetched_at=_now(), ok=False, error=error)


def _allowed_by_robots(url: str) -> bool:
    """True if the site's robots.txt lets LeadSage fetch this URL.

    401/403 means keep out, any other 4xx (no robots.txt) means allowed, a server error
    means "no". The rules are read with protego, which picks the most specific matching
    rule as the standard says (Python's own parser takes the first match, so "Disallow: /"
    followed by "Allow: /careers" blocked everything). Network errors are raised for
    fetch_page to report, so a dead site doesn't read as "blocked".
    """
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if resp.status_code in (401, 403):
        return False
    if 400 <= resp.status_code < 500:
        return True
    if resp.status_code >= 500:
        return False
    return Protego.parse(resp.text).can_fetch(url, USER_AGENT)


def _robots_ok(url: str) -> bool:
    return urlsplit(url).netloc in PUBLIC_APIS or _allowed_by_robots(url)


class _Refused(Exception):
    """A fetch we won't make or finish. The message is the reason."""


def _get(url: str) -> tuple[requests.Response, str]:
    """GET a URL, checking robots.txt first and again at every redirect hop.

    Returns the final response and its URL. Raises _Refused with a reason.
    """
    if urlsplit(url).scheme not in ("http", "https"):
        raise _Refused("not an http(s) URL")
    if not _robots_ok(url):
        raise _Refused("blocked by robots.txt")

    current = url
    redirects = 0
    while True:
        resp = requests.get(current, headers={"User-Agent": USER_AGENT},
                            timeout=TIMEOUT, allow_redirects=False)
        location = resp.headers.get("Location")
        if resp.status_code not in REDIRECT_CODES or not location:
            break
        if redirects == MAX_REDIRECTS:
            raise _Refused("too many redirects")
        current = urljoin(current, location)
        if urlsplit(current).scheme not in ("http", "https"):
            raise _Refused("redirected to a non-http(s) URL")
        if not _robots_ok(current):
            raise _Refused("redirect blocked by robots.txt")
        redirects += 1

    if resp.status_code >= 400:
        raise _Refused(f"HTTP {resp.status_code}")
    return resp, current


def _reason(e: Exception) -> str:
    if isinstance(e, _Refused):
        return str(e)
    if isinstance(e, requests.Timeout):
        return f"timed out after {TIMEOUT}s"
    if isinstance(e, requests.ConnectionError):
        return "could not connect (site down or no such domain)"
    if isinstance(e, requests.RequestException):
        return f"request failed: {type(e).__name__}"
    return f"{type(e).__name__}: {e}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


def fetch_page(url: str) -> Page:
    """Download one page and return its main readable text. Never raises."""
    try:
        resp, final_url = _get(url)
        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            raise _Refused(f"not HTML ({content_type or 'no content type'})")
        text = _clean(trafilatura.extract(resp.content, url=final_url) or "")
        if not text:
            raise _Refused("no readable text")
        return Page(url=final_url, text=text, fetched_at=_now(), ok=True)
    except Exception as e:
        return _fail(url, _reason(e))


def fetch_full_text(url: str, max_chars: int = FULL_MAX_CHARS) -> Page:
    """Every visible line of a page, not only the main article. Team and leadership blocks
    sit outside the main article, so fetch_page drops them. Never raises."""
    try:
        resp, final_url = _get(url)
        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            raise _Refused(f"not HTML ({content_type or 'no content type'})")
        text = re.sub(r"\s+", " ", trafilatura.html2txt(resp.text) or "").strip()[:max_chars]
        if not text:
            raise _Refused("no readable text")
        return Page(url=final_url, text=text, fetched_at=_now(), ok=True)
    except Exception as e:
        return _fail(url, _reason(e))


def _fetch_raw(url: str, kind: str) -> Page:
    """Download a response whose Content-Type names `kind`; `text` is the raw body, not capped."""
    try:
        resp, final_url = _get(url)
        content_type = resp.headers.get("Content-Type", "")
        if kind not in content_type.lower():
            raise _Refused(f"not {kind.upper()} ({content_type or 'no content type'})")
        return Page(url=final_url, text=resp.text, fetched_at=_now(), ok=True)
    except Exception as e:
        return _fail(url, _reason(e))


def fetch_json(url: str) -> Page:
    """A JSON API response as raw text. Never raises."""
    return _fetch_raw(url, "json")


def fetch_html(url: str) -> Page:
    """A page's raw HTML, links and markup included (fetch_page keeps only the readable text)."""
    return _fetch_raw(url, "html")


def slug(name: str) -> str:
    """Company name as a safe file name: "Acme Co." -> "acme-co"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_or_fetch(url: str, path: Path, fetch: Callable[[str], Page] = fetch_page,
                  refetch: bool = False) -> Page:
    """The page saved at `path`, or a fresh fetch if there is none (or `refetch`).

    Only pages that fetched fine are saved, so a site that was down is tried again next time.
    """
    if path.exists() and not refetch:
        return Page.model_validate_json(path.read_text(encoding="utf-8"))
    page = fetch(url)
    if page.ok:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page.model_dump_json(indent=2), encoding="utf-8")
    return page


def _demo() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m agent.fetch <url>")
        sys.exit(1)
    sys.stdout.reconfigure(encoding="utf-8")
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
