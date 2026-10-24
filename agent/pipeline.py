"""
Research and score leads, and keep the results for the app.

For each company in a lead CSV: read its website (cached in data/pages/), find open
sales roles on its job board when data/signals_leads.csv lists it, score it (with a
second model on borderline leads), and save it in the lead store.

Run it:  python -m agent.pipeline --file real_leads.csv
         python -m agent.pipeline --file test_leads.csv --names "Hevo Data,Basecamp"
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Callable
from pathlib import Path

from agent import store
from agent.evaluate import DATA, _lead_description
from agent.llm import second_model_name
from agent.score_lead import ICP, Company, is_borderline, load_icp, score_lead, with_second_opinion
from agent.signals import hiring_for, read_rows

LEAD_COLUMNS = {"name", "industry", "size", "location", "url", "description"}
SIGNALS_FILE = "signals_leads.csv"


def lead_files() -> list[str]:
    """CSV files in data/ that have every lead column."""
    found = []
    for path in sorted(DATA.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            header = set(next(csv.reader(f), []))
        if LEAD_COLUMNS <= header:
            found.append(path.name)
    return found


def read_leads(file: str) -> list[dict]:
    with (DATA / file).open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("name") or "").strip()]


def signal_rows(file: str = SIGNALS_FILE) -> dict[str, dict]:
    """Company name (lowercase) -> its job-board row, if the signals file exists."""
    if not (DATA / file).exists():
        return {}
    return {r["name"].strip().lower(): r for r in read_rows(file)}


def research(row: dict, icp: ICP, signals: dict[str, dict]) -> tuple[Company, str]:
    """The company as the scorer sees it, and where its description came from."""
    description, text_source = _lead_description(row, "fetched")
    roles = []
    if signal := signals.get(row["name"].strip().lower()):
        roles = hiring_for(signal, icp).roles
    company = Company(name=row["name"].strip(), industry=row.get("industry", ""), size=row.get("size", ""),
                      location=row.get("location", ""), description=description, sales_roles=roles)
    return company, text_source


def score_all(rows: list[dict], icp: ICP, signals: dict[str, dict], report: Callable[[str], None] = print,
              db: Path = store.DB_PATH) -> None:
    """Research, score and save every lead, then give the borderline ones a second opinion.

    The main model scores every lead first and the second model goes after, so the
    GPU swaps models once per batch instead of twice per lead. Each lead is saved as
    soon as it is scored; a failure is reported and the batch moves on.
    """
    scored = []
    for i, row in enumerate(rows, 1):
        try:
            company, text_source = research(row, icp, signals)
            result = score_lead(icp, company)
        except Exception as e:
            report(f"[{i}/{len(rows)}] {row['name']}: failed ({str(e)[:90]})")
            continue
        url = (row.get("url") or "").strip()
        store.save(company, result, url=url, text_source=text_source, path=db)
        report(f"[{i}/{len(rows)}] {company.name}: {result.verdict} {result.fit}")
        if is_borderline(result, icp.settings):
            scored.append((company, result, url, text_source))

    if scored:
        report(f"Second opinion ({second_model_name()}) on {len(scored)} borderline leads")
    for company, result, url, text_source in scored:
        try:
            second = score_lead(icp, company, model=second_model_name())
        except Exception as e:
            report(f"{company.name}: second opinion failed ({str(e)[:90]}); main score kept")
            continue
        final = with_second_opinion(result, second, icp.settings)
        store.save(company, final, url=url, text_source=text_source, path=db)
        report(f"{company.name}: second model {second.verdict} {second.fit} -> {final.verdict}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="real_leads.csv", help="lead CSV in data/ (default: real_leads.csv)")
    ap.add_argument("--names", help="comma-separated company names to score (default: all)")
    args = ap.parse_args()

    icp = load_icp()
    rows = read_leads(args.file)
    if args.names:
        wanted = {n.strip().lower() for n in args.names.split(",")}
        rows = [r for r in rows if r["name"].strip().lower() in wanted]
    print(f"Scoring {len(rows)} leads from {args.file} into {store.DB_PATH.name}...\n", flush=True)
    score_all(rows, icp, signal_rows(), report=lambda msg: print(msg, flush=True))


if __name__ == "__main__":
    main()
