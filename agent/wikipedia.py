"""
Wikipedia's summary of a company, for a lead whose own site can't be read.

Read through Wikipedia's documented REST API. A name alone can land on the wrong thing: in
November 2026 "Sprinto" was a French sailboat, "Deputy" a job title and "Swipe" a list of other
pages. So a page counts only if it is an ordinary article, Wikipedia's short description calls it
a company, and its text names the lead. Anything else gives nothing and the typed description stands.

The text is CC BY-SA, so a lead scored from it says so (SOURCE).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import quote

from agent.fetch import Page, fetch_json

API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
SOURCE = "from Wikipedia"
COMPANY_WORDS = re.compile(r"\b(company|corporation|conglomerate|startup|business|firm)\b", re.I)


def company_summary(name: str, fetch: Callable[[str], Page] = fetch_json) -> str:
    """The article's summary if the page is really about this company, else "". Never raises."""
    page = fetch(API + quote(name.strip().replace(" ", "_"), safe=""))
    if not page.ok:
        return ""
    try:
        data = json.loads(page.text)
    except ValueError:
        return ""
    if not isinstance(data, dict) or data.get("type") != "standard":
        return ""
    if not COMPANY_WORDS.search(str(data.get("description") or "")):
        return ""
    extract = str(data.get("extract") or "").strip()
    return extract if name.strip().lower() in extract.lower() else ""
