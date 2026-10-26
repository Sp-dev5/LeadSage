"""
Fetcher: reads one web page and returns its main text, where it came from, and when.

Checks robots.txt first and names itself in the user agent. Refuses any address that is not
on the public internet (loopback, private ranges, link-local, cloud metadata), at every
redirect hop. Never raises: any problem
(blocked, timeout, 404, not HTML, no readable text) comes back as ok=False with a reason,
so one dead website can't stop a batch of leads.

PUBLIC_APIS skips the robots check. Only hosts whose owner documents a public API for this
use belong there (Ashby's job postings API answers robots.txt with 401, which reads as
"keep out" even though the API is meant to be read).

Run it:  python -m agent.fetch https://example.com
"""

from __future__ import annotations

import ipaddress
import re
import socket
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
import trafilatura
from protego import Protego
from pydantic import BaseModel

from agent.files import write_text_atomic

USER_AGENT = "LeadSage/0.1"
TIMEOUT = 10
MAX_PAGE_BYTES = 10_000_000
MAX_ROBOTS_BYTES = 500_000
MAX_DOWNLOAD_SECONDS = 30
CHUNK_BYTES = 16_384
MAX_CHARS = 4000
FULL_MAX_CHARS = 9000
MAX_REDIRECTS = 5
REDIRECT_CODES = (301, 302, 303, 307, 308)
PUBLIC_APIS = {"api.ashbyhq.com"}
RATE_LIMIT_WAIT = 10
MAX_SITE_LINKS = 6
MAX_LINK_DEPTH = 2
AMBIGUOUS_WORDS = ("people", "team")
SKIP_LINK_ENDINGS = (".pdf", ".jpg", ".jpeg", ".png", ".svg", ".zip", ".xml")


class Page(BaseModel):
    url: str
    text: str = ""
    fetched_at: str
    ok: bool
    error: str = ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fail(url: str, error: str) -> Page:
    return Page(url=url, fetched_at=now(), ok=False, error=error)


class _Refused(Exception):
    """A fetch we won't make or finish. The message is the reason."""


def _resolve(host: str) -> list[str]:
    """Every address a host name points to; empty when it doesn't resolve."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return []


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


def _require_public(url: str, reason: str) -> None:
    """Refuse a URL whose host is, or resolves to, a private, loopback, link-local or reserved
    address, so a lead's website (or a redirect) can't point us at the machine's own network
    or a cloud metadata address. A host that doesn't resolve passes: the request fails on its own."""
    host = urlsplit(url).hostname
    if not host:
        raise _Refused("no host in the URL")
    if not all(_is_public(address) for address in _resolve(host)):
        raise _Refused(reason)


def _read_capped(resp: requests.Response, max_bytes: int, what: str = "page") -> None:
    """Read a streamed response into `resp`, refusing one that is declared or turns out bigger than
    `max_bytes`, or that takes longer than MAX_DOWNLOAD_SECONDS, so one hostile site can't fill the
    memory or hold a batch up. The timeout alone only limits the wait for each piece."""
    declared = resp.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        resp.close()
        raise _Refused(f"{what} too large")
    body, started = bytearray(), time.monotonic()
    for chunk in resp.iter_content(CHUNK_BYTES):
        body += chunk
        if len(body) > max_bytes:
            resp.close()
            raise _Refused(f"{what} too large")
        if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
            resp.close()
            raise _Refused(f"{what} took too long to download")
    resp._content = bytes(body)
    resp._content_consumed = True


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
    for _ in range(MAX_REDIRECTS + 1):
        resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT},
                            timeout=TIMEOUT, allow_redirects=False, stream=True)
        location = resp.headers.get("Location")
        if resp.status_code not in REDIRECT_CODES or not location:
            _read_capped(resp, MAX_ROBOTS_BYTES, "robots.txt")
            break
        resp.close()
        robots_url = urljoin(robots_url, location)
        if urlsplit(robots_url).scheme not in ("http", "https"):
            return False
        _require_public(robots_url, "robots.txt redirected to an internal address")
    if resp.status_code in REDIRECT_CODES:
        return False
    if resp.status_code in (401, 403):
        return False
    if 400 <= resp.status_code < 500:
        return True
    if resp.status_code >= 500:
        return False
    return Protego.parse(resp.text).can_fetch(url, USER_AGENT)


def _robots_ok(url: str) -> bool:
    return urlsplit(url).netloc in PUBLIC_APIS or _allowed_by_robots(url)


def _get(url: str, max_bytes: int | None = None) -> tuple[requests.Response, str]:
    """GET a URL, checking robots.txt first and again at every redirect hop.

    The body is read in pieces and refused past `max_bytes` (default MAX_PAGE_BYTES). Returns the final response and its
    URL. Raises _Refused with a reason.
    """
    if urlsplit(url).scheme not in ("http", "https"):
        raise _Refused("not an http(s) URL")
    _require_public(url, "internal address")
    if not _robots_ok(url):
        raise _Refused("blocked by robots.txt")

    current = url
    redirects = 0
    waited = False
    while True:
        resp = requests.get(current, headers={"User-Agent": USER_AGENT},
                            timeout=TIMEOUT, allow_redirects=False, stream=True)
        if resp.status_code == 429 and not waited:
            waited = True
            resp.close()
            time.sleep(_wait_for(resp))
            continue
        location = resp.headers.get("Location")
        if resp.status_code not in REDIRECT_CODES or not location:
            break
        resp.close()
        if redirects == MAX_REDIRECTS:
            raise _Refused("too many redirects")
        current = urljoin(current, location)
        if urlsplit(current).scheme not in ("http", "https"):
            raise _Refused("redirected to a non-http(s) URL")
        _require_public(current, "redirect to an internal address")
        if not _robots_ok(current):
            raise _Refused("redirect blocked by robots.txt")
        redirects += 1

    if resp.status_code >= 400:
        resp.close()
        raise _Refused(f"HTTP {resp.status_code}")
    _read_capped(resp, max_bytes or MAX_PAGE_BYTES)
    return resp, current


def _wait_for(resp: requests.Response) -> float:
    """How long a site asked us to wait before trying again, capped so a batch keeps moving."""
    try:
        return min(float(resp.headers.get("Retry-After", RATE_LIMIT_WAIT)), RATE_LIMIT_WAIT)
    except ValueError:
        return RATE_LIMIT_WAIT


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs += [v for k, v in attrs if k == "href" and v]


def _host(netloc: str) -> str:
    return netloc.lower().removeprefix("www.")


def site_links(url: str, words: tuple[str, ...], limit: int = MAX_SITE_LINKS) -> list[str]:
    """Paths on the same site that the homepage links to and whose path has one of `words` as a
    whole word (for example "team" or "press", not "management" inside "expense-management"),
    at most two levels deep, in page order. Robots.txt applies. Never raises: a
    homepage we can't read gives an empty list."""
    try:
        resp, final_url = _get(url)
        parser = _Links()
        parser.feed(resp.text)
    except Exception:
        return []
    found: list[str] = []
    for href in parser.hrefs:
        parts = urlsplit(urljoin(final_url, href))
        path = parts.path.rstrip("/").lower()
        if parts.scheme not in ("http", "https") or _host(parts.netloc) != _host(urlsplit(final_url).netloc):
            continue
        if not path or path.endswith(SKIP_LINK_ENDINGS) or path in found:
            continue
        segments = [s for s in path.split("/") if s]
        tokens = set(re.split(r"[-_]", "-".join(segments)))
        last = re.split(r"[-_]", segments[-1])[-1] if segments else ""
        named = [w for w in words if w in tokens or (("-" in w) and w in segments)]
        named = [w for w in named if w not in AMBIGUOUS_WORDS or w == last]
        if len(segments) <= MAX_LINK_DEPTH and named:
            found.append(path)
    return found[:limit]


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
        return Page(url=final_url, text=text, fetched_at=now(), ok=True)
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
        return Page(url=final_url, text=text, fetched_at=now(), ok=True)
    except Exception as e:
        return _fail(url, _reason(e))


def _fetch_raw(url: str, kind: str) -> Page:
    """Download a response whose Content-Type names `kind`; `text` is the raw body, not capped."""
    try:
        resp, final_url = _get(url)
        content_type = resp.headers.get("Content-Type", "")
        if kind not in content_type.lower():
            raise _Refused(f"not {kind.upper()} ({content_type or 'no content type'})")
        return Page(url=final_url, text=resp.text, fetched_at=now(), ok=True)
    except Exception as e:
        return _fail(url, _reason(e))


def fetch_json(url: str) -> Page:
    """A JSON API response as raw text. Never raises."""
    return _fetch_raw(url, "json")


def fetch_html(url: str) -> Page:
    """A page's raw HTML, links and markup included (fetch_page keeps only the readable text)."""
    return _fetch_raw(url, "html")


MAX_IMAGE_BYTES = 300_000


def fetch_image(url: str) -> tuple[bytes, str] | None:
    """An image's bytes and content type, robots.txt respected; None if it can't be had. Never raises."""
    try:
        resp, _ = _get(url, MAX_IMAGE_BYTES)
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            return None
        return resp.content, content_type
    except Exception:
        return None


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
        write_text_atomic(path, page.model_dump_json(indent=2))
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
