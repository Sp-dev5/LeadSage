"""
Sales hiring: which open jobs on a company's job board are sales roles.

Reads data/signals_leads.csv (name, careers_url, job_board), downloads each company's open
jobs from its job board, and sorts the titles:
- exact: the title contains one of the ICP's sales titles, word for word
- inferred: the model says the title is a sales role, judged against the ICP's definition

Every role kept is a title copied from the downloaded list; a model answer that isn't one of
those titles is dropped, and "unsure" doesn't count. No board, no list or no roles means
unknown, never "no sales team".

`job_board` is "<board>:<company id on that board>", e.g. "ashby:atlan". Leave it blank and the
board is found on the careers page instead (see BOARDS). Supported: ashby, greenhouse, lever,
keka, trakstar. Adding one means one row in BOARDS and a test.

Run it:  python -m agent.signals
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from pathlib import Path
from typing import Callable, Literal, NamedTuple

from pydantic import BaseModel

from agent.fetch import fetch_html, fetch_json, load_or_fetch, slug
from agent.llm import EXTRACT_TEMPERATURE, generate_structured
from agent.score_lead import ICP, SalesRole, load_icp

DATA = Path(__file__).resolve().parent.parent / "data"
PAGES = DATA / "pages"
MAX_TITLES_TO_INFER = 60


class Board(NamedTuple):
    """One job platform: where its list is, how to read titles, how to spot it on a page."""

    list_url: Callable[[str], str]
    titles: Callable[[str], list[str]]
    spot: str
    kind: Literal["json", "html"] = "json"


def _ashby(text: str) -> list[str]:
    return [j["title"] for j in json.loads(text)["jobs"] if j.get("isListed", True)]


def _trakstar(text: str) -> list[str]:
    names = re.findall(r"js-job-list-opening-name[^>]*>\s*(.*?)\s*</h3>", text, re.S)
    return [html.unescape(n).strip() for n in names]


BOARDS = {
    "ashby": Board(
        lambda i: f"https://api.ashbyhq.com/posting-api/job-board/{i}",
        _ashby,
        r"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/([\w.-]+)"),
    "greenhouse": Board(
        lambda i: f"https://boards-api.greenhouse.io/v1/boards/{i}/jobs",
        lambda text: [j["title"] for j in json.loads(text)["jobs"]],
        r"(?:boards|job-boards)\.greenhouse\.io/([\w-]+)"),
    "lever": Board(
        lambda i: f"https://api.lever.co/v0/postings/{i}?mode=json",
        lambda text: [j["text"] for j in json.loads(text)],
        r"jobs\.lever\.co/([\w-]+)"),
    "keka": Board(
        lambda i: "https://{}.keka.com/careers/api/embedjobs/default/active/{}".format(*i.split("/")),
        lambda text: [j["title"] for j in json.loads(text)],
        r"([\w-]+)\.keka\.com/careers/api/embedjobs/(?:js|default/active)/([0-9a-f-]{36})"),
    "trakstar": Board(
        lambda i: f"https://{i}.hire.trakstar.com/",
        _trakstar,
        r"([\w-]+)\.hire\.trakstar\.com",
        "html"),
}


def detect_board(url: str, page_html: str) -> str:
    """An entry like "ashby:atlan" for the first platform spotted in the careers URL or HTML."""
    text = url + "\n" + page_html
    for name, board in BOARDS.items():
        if m := re.search(board.spot, text):
            return f"{name}:{'/'.join(g for g in m.groups() if g)}"
    return ""


class TitleCall(BaseModel):
    quote: str
    is_sales_role: Literal["yes", "no", "unsure"]


class TitleCalls(BaseModel):
    titles: list[TitleCall]


class Hiring(BaseModel):
    """What one company's job board says about sales hiring."""

    ok: bool
    error: str = ""
    board: str = ""
    jobs_seen: int = 0
    roles: list[SalesRole] = []


INFER_PROMPT = """You sort job titles. A sales role is {definition}.
Examples of sales roles: {examples}.

For every job title given, copy the title exactly as written, then answer:
- yes: the job is a sales role by the definition above
- no: it is not
- unsure: the title alone doesn't make it clear
Judge only from the title."""


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def exact_match(title: str, sales_titles: list[str]) -> bool:
    """True if one of the sales titles appears in the job title as whole words."""
    t = _plain(title)
    return any(re.search(rf"\b{re.escape(_plain(s))}\b", t) for s in sales_titles)


def infer_sales_titles(titles: list[str], icp: ICP) -> list[str]:
    """The titles the model calls sales roles. Only answers that copy a given title count."""
    if not titles:
        return []
    asked = titles[:MAX_TITLES_TO_INFER]
    calls = generate_structured(
        system=INFER_PROMPT.format(definition=icp.sales_roles.definition,
                                   examples=", ".join(icp.sales_roles.titles)),
        user_prompt="JOB TITLES\n" + "\n".join(f"- {t}" for t in asked),
        schema=TitleCalls,
        temperature=EXTRACT_TEMPERATURE,
    )
    by_plain = {_plain(t): t for t in asked}
    said_yes = {_plain(c.quote) for c in calls.titles if c.is_sales_role == "yes"}
    return [by_plain[p] for p in by_plain if p in said_yes]


def job_board_for(name: str, job_board: str, careers_url: str, pages: Path = PAGES,
                  refetch: bool = False) -> str:
    """The typed `job_board`, or else the one spotted on the careers page ("" if none)."""
    if job_board or not careers_url:
        return job_board
    page = load_or_fetch(careers_url, pages / f"{slug(name)}-careers-html.json", fetch_html, refetch)
    return detect_board(page.url, page.text) if page.ok else ""


def sales_hiring(name: str, job_board: str, icp: ICP, pages: Path = PAGES,
                 refetch: bool = False) -> Hiring:
    """Open sales roles for one company, from its job board. Never raises on a bad board."""
    kind, _, board_id = job_board.partition(":")
    if kind not in BOARDS or not board_id:
        return Hiring(ok=False, error=f"unsupported job board {job_board!r}")
    board = BOARDS[kind]
    fetch = fetch_html if board.kind == "html" else fetch_json
    page = load_or_fetch(board.list_url(board_id), pages / f"{slug(name)}-jobs.json", fetch, refetch)
    if not page.ok:
        return Hiring(ok=False, board=job_board, error=page.error)
    try:
        titles = list(dict.fromkeys(board.titles(page.text)))
    except (ValueError, KeyError, TypeError) as e:
        return Hiring(ok=False, board=job_board, error=f"unreadable job list: {type(e).__name__}")

    exact = [t for t in titles if exact_match(t, icp.sales_roles.titles)]
    inferred = infer_sales_titles([t for t in titles if t not in exact], icp)
    roles = [SalesRole(title=t, match=m, source=page.url, fetched_at=page.fetched_at)
             for ts, m in ((exact, "exact"), (inferred, "inferred")) for t in ts]
    return Hiring(ok=True, board=job_board, jobs_seen=len(titles), roles=roles)


def hiring_for(row: dict, icp: ICP, pages: Path = PAGES, refetch: bool = False) -> Hiring:
    """Sales hiring for one signals CSV row (name, careers_url, job_board)."""
    name = row["name"].strip()
    board = job_board_for(name, (row.get("job_board") or "").strip(),
                          (row.get("careers_url") or "").strip(), pages, refetch)
    if not board:
        return Hiring(ok=False, error="no supported job board found")
    return sales_hiring(name, board, icp, pages, refetch)


def read_rows(file: str) -> list[dict]:
    with (DATA / file).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="signals_leads.csv", help="CSV in data/ with name,careers_url,job_board")
    ap.add_argument("--refetch", action="store_true", help="ignore saved pages, download again")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    icp = load_icp()
    results = []
    for row in read_rows(args.file):
        name = row["name"].strip()
        h = hiring_for(row, icp, refetch=args.refetch)
        results.append((name, h))
        if not h.ok:
            print(f"{name[:24]:24} no evidence   {h.error}")
            continue
        print(f"{name[:24]:24} {h.jobs_seen:>3} jobs, {len(h.roles)} sales   ({h.board})")
        for r in h.roles:
            print(f"{'':24}   {r.match:8} {r.title}")

    readable = [h for _, h in results if h.ok]
    print(f"\nJob list read for {len(readable)} of {len(results)} companies; "
          f"sales hiring found at {sum(1 for h in readable if h.roles)}.")

    out_path = DATA / "signals_results.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "job_board", "status", "jobs_seen", "sales_roles", "error"])
        for name, h in results:
            w.writerow([name, h.board, "ok" if h.ok else "no evidence", h.jobs_seen,
                        "; ".join(f"{r.title} ({r.match})" for r in h.roles), h.error])
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
