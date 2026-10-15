"""
Probe: do company careers pages come back as readable text that mentions sales?

Reads data/signals_leads.csv (name,careers_url), fetches each careers page, prints one
line per company, and saves each readable page to data/pages/<name>-careers.json.

Run it:  python -m agent.fetch_careers
         python -m agent.fetch_careers --file other_list.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from agent.fetch import MAX_CHARS, fetch_page

DATA = Path(__file__).resolve().parent.parent / "data"
PAGES = DATA / "pages"
SALES_WORDS = ("sales", "account executive", "sdr", "revenue")


def cache_path(name: str, pages: Path = PAGES) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return pages / f"{slug}-careers.json"


def sales_words_in(text: str) -> list[str]:
    lower = text.lower()
    return [w for w in SALES_WORDS if w in lower]


def probe_row(row: dict, pages: Path = PAGES) -> dict:
    name = row["name"].strip()
    url = (row.get("careers_url") or "").strip()
    if not url:
        return {"name": name, "url": "", "ok": False, "error": "no careers_url", "chars": 0,
                "words": [], "final_url": ""}
    page = fetch_page(url)
    if page.ok:
        pages.mkdir(parents=True, exist_ok=True)
        cache_path(name, pages).write_text(page.model_dump_json(indent=2), encoding="utf-8")
    return {"name": name, "url": url, "ok": page.ok, "error": page.error, "chars": len(page.text),
            "words": sales_words_in(page.text), "final_url": page.url}


def gate(results: list[dict]) -> tuple[int, int, bool]:
    with_link = [r for r in results if r["url"]]
    passing = [r for r in with_link if r["ok"] and r["words"]]
    return len(passing), len(with_link), bool(with_link) and 2 * len(passing) >= len(with_link)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="signals_leads.csv", help="CSV in data/ with name,careers_url")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    lead_file = DATA / args.file
    if not lead_file.exists():
        print(f"{lead_file.name} not found in data/. Create it with two columns: name,careers_url")
        sys.exit(1)
    rows = list(csv.DictReader(lead_file.open(encoding="utf-8", newline="")))
    if rows and not {"name", "careers_url"} <= rows[0].keys():
        print(f"{lead_file.name} needs the columns name,careers_url (found: {', '.join(rows[0])})")
        sys.exit(1)

    print(f"Probing {len(rows)} careers pages from {lead_file.name}...\n", flush=True)
    results = []
    for row in rows:
        r = probe_row(row)
        results.append(r)
        if not r["url"]:
            status = "skipped     no careers_url"
        elif not r["ok"]:
            status = f"FAILED      {r['error']}"
        else:
            words = ", ".join(r["words"]) if r["words"] else "none"
            host = urlsplit(r["final_url"]).netloc
            capped = " (capped, roles further down are cut off)" if r["chars"] >= MAX_CHARS else ""
            status = f"ok {r['chars']:>6} chars  sales words: {words}  ({host}){capped}"
        print(f"{r['name'][:24]:24} {status}", flush=True)

    passing, with_link, passed = gate(results)
    no_link = len(results) - with_link
    share = f" ({round(100 * passing / with_link)}%)" if with_link else ""
    print(f"\nReadable and mentions sales: {passing} of {with_link} with a link{share}. "
          f"{no_link} had no link.")
    print(f"Gate (at least half): {'PASS, build Phase 2' if passed else 'FAIL, find another source'}")


if __name__ == "__main__":
    main()
