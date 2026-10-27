"""
Finding companies: pull real companies from the open Y Combinator company list into a lead CSV.

The list (yc-oss.github.io/api, free, no key) gives each company's name, website, team size,
location, industry and a one-line description, so every column the pipeline reads is filled.
Only active companies with a website and a location are kept. By default the team size must be
inside the ICP's size range (`company_size_min` to `company_size_max` in icp.json); `--any-size`
drops that check, for a labelling set that should include clear no's. Companies already in the
lead store or in any lead CSV in data/ are skipped, matched by name or website domain.
`--in-region` keeps only companies with a location in the ICP's countries (`geography_countries`),
and writes that location. `--hiring` keeps only companies the list marks as hiring now.

The picks are a shuffled sample (fixed seed, so the same command gives the same list), written to
data/<out> with `my_label` and `why` empty. An existing file is never overwritten, so labels
can't be lost. Nothing is scored here: run the pipeline or label the file next.

Known limits: Y Combinator companies only, so the list leans to young, US-based startups.

Run it:  python -m agent.find --industry B2B --limit 50
         python -m agent.find --industry B2B,Fintech --limit 40 --any-size --out blind_leads_2.csv
         python -m agent.find --industry B2B,Fintech --in-region --hiring --out region_leads.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from agent import store
from agent.fetch import fetch_json
from agent.score_lead import ICP, load_icp, locate

DATA = Path(__file__).resolve().parent.parent / "data"
SOURCE = "https://yc-oss.github.io/api/industries/{}.json"
COLUMNS = ["name", "industry", "size", "location", "url", "description", "my_label", "why"]


def source_url(industry: str) -> str:
    return SOURCE.format(industry.strip().lower().replace(" ", "-"))


def domain(url: str) -> str:
    host = urlsplit(url if "//" in url else "//" + url).netloc.lower()
    return host.removeprefix("www.")


def known_leads(db: Path = store.DB_PATH, data: Path = DATA) -> tuple[set[str], set[str]]:
    """Names and website domains already in the lead store or in any lead CSV."""
    names, domains = set(), set()
    for lead in store.load_all(db):
        names.add(lead.company.name.strip().lower())
        domains.add(domain(lead.url))
    for path in data.glob("*leads*.csv"):
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                names.add((row.get("name") or "").strip().lower())
                if row.get("url"):
                    domains.add(domain(row["url"]))
    names.discard("")
    domains.discard("")
    return names, domains


def fix_text(text: str) -> str:
    """Undo text that was saved as UTF-8 and read back as Windows-1252 ('â€¦' -> '…')."""
    try:
        return text.encode("cp1252").decode("utf-8").strip()
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text.strip()


def region_location(c: dict, icp: ICP) -> str:
    """The first of a company's locations that is inside the ICP's region, or "" if none is."""
    for place in c.get("all_locations", "").split(";"):
        if place.strip() and locate(icp, place).in_region == "yes":
            return place.strip()
    return ""


def candidates(companies: list[dict], icp: ICP, known: tuple[set[str], set[str]],
               any_size: bool = False, in_region: bool = False, hiring: bool = False) -> list[dict]:
    """Companies worth a row. With `in_region`, the in-region location becomes the one written."""
    names, domains = known
    keep = []
    for c in companies:
        size = c.get("team_size") or 0
        if c.get("status") != "Active" or c.get("nonprofit"):
            continue
        if not (c.get("website") and c.get("all_locations") and c.get("one_liner") and size):
            continue
        if not any_size and not icp.company_size_min <= size <= icp.company_size_max:
            continue
        if c["name"].strip().lower() in names or domain(c["website"]) in domains:
            continue
        if hiring and not c.get("isHiring"):
            continue
        if in_region:
            if not (place := region_location(c, icp)):
                continue
            c = {**c, "all_locations": place}
        keep.append(c)
    return keep


def pick(companies: list[dict], limit: int, seed: int) -> list[dict]:
    """A repeatable shuffle: each site is ordered by a hash of the seed and its domain, so the same
    seed gives the same picks, and new companies on the list don't reshuffle the old ones."""
    unique = {domain(c["website"]): c for c in companies}
    order = sorted(unique, key=lambda d: hashlib.sha256(f"{seed}:{d}".encode()).hexdigest())
    return [unique[d] for d in order[:limit]]


def to_row(c: dict) -> dict:
    industry = (c.get("subindustry") or c.get("industry") or "").replace(" -> ", ", ")
    return {"name": c["name"].strip(), "industry": industry, "size": f"{c['team_size']} employees",
            "location": c["all_locations"].split(";")[0].strip(), "url": c["website"].strip().rstrip("/"),
            "description": fix_text(c["one_liner"]), "my_label": "", "why": ""}


def download(industry: str) -> list[dict]:
    page = fetch_json(source_url(industry))
    if not page.ok:
        raise RuntimeError(f"could not read the {industry} list: {page.error}")
    return json.loads(page.text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", required=True,
                    help="comma-separated Y Combinator industries, e.g. B2B or B2B,Fintech")
    ap.add_argument("--limit", type=int, default=50, help="how many companies to write (default 50)")
    ap.add_argument("--any-size", action="store_true", help="don't limit team size to the ICP's range")
    ap.add_argument("--in-region", action="store_true",
                    help="only companies with a location in the ICP's countries (geography_countries)")
    ap.add_argument("--hiring", action="store_true", help="only companies Y Combinator lists as hiring now")
    ap.add_argument("--seed", type=int, default=7, help="random seed for the sample (default 7)")
    ap.add_argument("--out", default="found_leads.csv", help="CSV to create in data/ (default found_leads.csv)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    out = DATA / args.out
    if out.exists():
        print(f"{out.name} already exists in data/; pick another --out so its labels aren't lost.")
        sys.exit(1)
    icp = load_icp()
    companies = []
    for industry in args.industry.split(","):
        try:
            companies += download(industry)
        except RuntimeError as e:
            print(e)
            sys.exit(1)
    pool = candidates(companies, icp, known_leads(), args.any_size, args.in_region, args.hiring)
    chosen = pick(pool, args.limit, args.seed)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(to_row(c) for c in chosen)
    checks = ["any size" if args.any_size else f"{icp.company_size_min} to {icp.company_size_max} people",
              "active", "not already a lead"]
    checks += ["in region"] * args.in_region + ["hiring now"] * args.hiring
    print(f"{len(companies)} companies read, {len(pool)} fit ({', '.join(checks)}). "
          f"Wrote {len(chosen)} to {out}.")


if __name__ == "__main__":
    main()
