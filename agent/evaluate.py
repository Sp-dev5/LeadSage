"""
Compare the scorer against your hand labels.

Scores every lead in data/labelled_leads.csv, then shows where it disagrees with you.

Run it:  python -m agent.evaluate
         python -m agent.evaluate --file unseen_leads.csv --runs 3
         python -m agent.evaluate --icp my_icp.json   compare an ICP variant
         python -m agent.evaluate --file real_leads.csv --text fetched   score the real website
Needs `ollama serve` running.
Results are also saved to data/eval_results_<file>_ollama.csv.

--text fetched scores each lead's website (its `url` column) instead of the typed
description. Pages are cached in data/pages/ so every run scores the same text;
--refetch downloads them again. A failed fetch falls back to the typed description.

--jobs signals_leads.csv adds each lead's open sales roles from its job board (see
agent/signals.py). Roles found fill an unknown sales motion; none found changes nothing.

Location is a filter, not a criterion: grades compare verdicts only, and leads outside
the ICP region are listed separately (the region filter would hide them).
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from agent.fetch import fetch_page, load_or_fetch, slug
from agent.score_lead import Company, load_icp, locate, score_lead, score_with_second_opinion
from agent.signals import hiring_for, read_rows

DATA = Path(__file__).resolve().parent.parent / "data"
PAGES = DATA / "pages"
ORDER = {"disqualify": 0, "review": 1, "qualify": 2}


def _grade(label: str, verdict: str) -> str:
    """OK = same, near = one step apart (e.g. review vs qualify), MISS = opposite."""
    if verdict not in ORDER:
        return "ERR"
    gap = abs(ORDER[label] - ORDER[verdict])
    return "OK" if gap == 0 else "near" if gap == 1 else "MISS"


def _lead_description(row: dict, mode: str, pages: Path = PAGES,
                      refetch: bool = False) -> tuple[str, str]:
    """The text to score for one lead, and where it came from."""
    typed = row["description"]
    url = (row.get("url") or "").strip()
    if mode == "typed" or not url:
        return typed, "typed"

    page = load_or_fetch(url, pages / f"{slug(row['name'])}.json", fetch_page, refetch)
    if not page.ok:
        print(f"  fetch failed for {row['name']}: {page.error}, using typed", flush=True)
        return typed, "typed (fetch failed)"
    return page.text, "fetched"


def _signal_rows(file: str) -> dict[str, dict]:
    """Company name (lowercase) -> its row from a signals CSV in data/."""
    return {r["name"].strip().lower(): r for r in read_rows(file)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true", help="print per-criterion scores for leads that aren't OK")
    ap.add_argument("--names", help="comma-separated company names to score (default: all labelled)")
    ap.add_argument("--file", default="labelled_leads.csv", help="lead CSV in data/ (default: labelled_leads.csv)")
    ap.add_argument("--icp", default="icp.json", help="ICP JSON in data/ (default: icp.json); use a variant to compare rules")
    ap.add_argument("--runs", type=int, default=1, help="score each lead N times and take the majority verdict")
    ap.add_argument("--text", choices=["typed", "fetched"], default="typed",
                    help="score the typed description (default) or the lead's website (needs a url column)")
    ap.add_argument("--refetch", action="store_true", help="with --text fetched: ignore cached pages, download again")
    ap.add_argument("--jobs", metavar="CSV", help="signals CSV in data/ (name,job_board): add open sales roles as evidence")
    ap.add_argument("--second-opinion", action="store_true",
                    help="score borderline leads again with the second model; disagreement -> review")
    args = ap.parse_args()
    providers = ("ollama",)
    scorer = score_with_second_opinion if args.second_opinion else score_lead
    runs = max(1, args.runs)
    lead_file = DATA / args.file

    icp_file = DATA / args.icp
    icp = load_icp(icp_file)
    rows = [r for r in csv.DictReader(lead_file.open(encoding="utf-8", newline=""))
            if r["my_label"].strip()]
    if args.names:
        wanted = [n.strip().lower() for n in args.names.split(",")]
        rows = [r for r in rows if r["name"].lower() in wanted]
    signal_rows = _signal_rows(args.jobs) if args.jobs else {}
    print(f"Scoring {len(rows)} labelled leads from {lead_file.name} (ICP: {icp_file.name}) "
          f"with {' and '.join(providers)}, "
          f"{runs} run(s) each, {args.text} text"
          + (f", sales roles from {args.jobs}" if args.jobs else "") + "...\n", flush=True)

    results = []
    for i, r in enumerate(rows, 1):
        description, text_source = _lead_description(r, args.text, refetch=args.refetch)
        roles = []
        if signal_row := signal_rows.get(r["name"].strip().lower()):
            hiring = hiring_for(signal_row, icp, refetch=args.refetch)
            if not hiring.ok:
                print(f"  no job evidence for {r['name']}: {hiring.error}", flush=True)
            roles = hiring.roles
        company = Company(name=r["name"], industry=r["industry"], size=r["size"],
                          location=r["location"], description=description, sales_roles=roles)
        where = locate(icp, r["location"])
        out = {"name": r["name"], "label": r["my_label"].strip().lower(), "text_source": text_source,
               "sales_roles": len(roles), "country": where.country, "in_region": where.in_region}
        for p in providers:
            scores = []
            for _ in range(runs):
                try:
                    scores.append(scorer(icp, company))
                except Exception as e:
                    print(f"  {p} failed on {r['name']}: {str(e)[:90]}", flush=True)
            if not scores:
                out[p] = ("error", None, "ERR", "")
                continue
            verdict, votes = Counter(s.verdict for s in scores).most_common(1)[0]
            agreeing = [s for s in scores if s.verdict == verdict]
            fit = round(sum(s.fit for s in agreeing) / len(agreeing))
            out[p] = (verdict, fit, _grade(out["label"], verdict), f"{votes}/{len(scores)}")
            if args.detail and out[p][2] != "OK":
                s = agreeing[0]
                print(f"  {p} on {r['name']} (you: {out['label']}, got {verdict} {fit})", flush=True)
                for c in s.criteria:
                    print(f"    {c.score}/5 {c.criterion}: {c.evidence[:110]}", flush=True)
                if s.disqualifier_hit:
                    print(f"    disqualifier: {s.disqualifier_reason[:110]}", flush=True)
                for flag in s.review_flags:
                    print(f"    review rule: {flag[:110]}", flush=True)
        print(f"[{i}/{len(rows)}] {r['name']}: " + ", ".join(
            f"{p} {out[p][0]}" + (f" ({out[p][3]})" if runs > 1 else "") for p in providers), flush=True)
        results.append(out)

    print(f"\n{'Company':26} {'You':11} {'In region':9}" + "".join(f" | {p.capitalize():22}" for p in providers))
    for o in results:
        cells = ""
        for p in providers:
            v, fit, g, _ = o[p]
            cells += f" | {v} {fit if fit is not None else '-':>3} {g}".ljust(25)
        print(f"{o['name'][:26]:26} {o['label']:11} {o['in_region']:9}{cells}")

    print()
    for p in providers:
        grades = [o[p][2] for o in results]
        print(f"{p:7} OK {grades.count('OK')}  near {grades.count('near')}  "
              f"MISS {grades.count('MISS')}  errors {grades.count('ERR')}  (of {len(results)})")
    outside = [f"{o['name']} ({o['country']})" for o in results if o["in_region"] == "no"]
    print(f"Outside the ICP region (hidden by the region filter): {', '.join(outside) or 'none'}")

    icp_tag = "" if icp_file.name == "icp.json" else f"_{icp_file.stem}"
    text_tag = "_fetched" if args.text == "fetched" else ""
    jobs_tag = "_jobs" if args.jobs else ""
    second_tag = "_second" if args.second_opinion else ""
    out_path = DATA / f"eval_results_{lead_file.stem}{icp_tag}{text_tag}{jobs_tag}{second_tag}_{'_'.join(providers)}.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "my_label", "text_source", "sales_roles", "country", "in_region"]
                   + [f"{p}_{c}" for p in providers for c in ("verdict", "fit", "grade", "votes")])
        for o in results:
            w.writerow([o["name"], o["label"], o["text_source"], o["sales_roles"], o["country"], o["in_region"]]
                       + [x for p in providers for x in o[p]])
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
