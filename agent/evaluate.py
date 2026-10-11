"""
Compare each model against your hand labels.

Scores every lead in data/labelled_leads.csv with Ollama and with Gemini, each on its
own (no fallback, no second-opinion rule), then shows where each one disagrees with you.

Run it:  python -m agent.evaluate               both models
         python -m agent.evaluate --only ollama one model (Gemini is often overloaded)
         python -m agent.evaluate --file unseen_leads.csv --runs 3 --only ollama
Needs `ollama serve` running; Gemini needs a key in .env.
Results are also saved to data/eval_results_<file>_<model>.csv.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from agent.score_lead import Company, _score_with, load_icp

DATA = Path(__file__).resolve().parent.parent / "data"
ORDER = {"disqualify": 0, "review": 1, "qualify": 2}


def _grade(label: str, verdict: str) -> str:
    """OK = same, near = one step apart (e.g. review vs qualify), MISS = opposite."""
    if verdict not in ORDER:
        return "ERR"
    gap = abs(ORDER[label] - ORDER[verdict])
    return "OK" if gap == 0 else "near" if gap == 1 else "MISS"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["ollama", "gemini"], help="score with just one model")
    ap.add_argument("--detail", action="store_true", help="print per-criterion scores for leads that aren't OK")
    ap.add_argument("--names", help="comma-separated company names to score (default: all labelled)")
    ap.add_argument("--file", default="labelled_leads.csv", help="lead CSV in data/ (default: labelled_leads.csv)")
    ap.add_argument("--runs", type=int, default=1, help="score each lead N times and take the majority verdict")
    args = ap.parse_args()
    only = args.only
    providers = (only,) if only else ("ollama", "gemini")
    runs = max(1, args.runs)
    lead_file = DATA / args.file

    icp = load_icp()
    rows = [r for r in csv.DictReader(lead_file.open(encoding="utf-8", newline=""))
            if r["my_label"].strip()]
    if args.names:
        wanted = [n.strip().lower() for n in args.names.split(",")]
        rows = [r for r in rows if r["name"].lower() in wanted]
    print(f"Scoring {len(rows)} labelled leads from {lead_file.name} with {' and '.join(providers)}, "
          f"{runs} run(s) each...\n", flush=True)

    results = []
    for i, r in enumerate(rows, 1):
        company = Company(name=r["name"], industry=r["industry"], size=r["size"],
                          location=r["location"], description=r["description"])
        out = {"name": r["name"], "label": r["my_label"].strip().lower()}
        for p in providers:
            scores = []
            for _ in range(runs):
                try:
                    scores.append(_score_with(p, icp, company))
                except Exception as e:
                    print(f"  {p} failed on {r['name']}: {str(e)[:90]}", flush=True)
            if not scores:
                out[p] = ("error", None, "ERR")
                continue
            # Majority verdict; its fit is the average over the runs that gave it.
            verdict, votes = Counter(s.verdict for s in scores).most_common(1)[0]
            agreeing = [s for s in scores if s.verdict == verdict]
            fit = round(sum(s.fit for s in agreeing) / len(agreeing))
            out[p] = (verdict, fit, _grade(out["label"], verdict))
            if runs > 1:
                out[p + "_votes"] = f"{votes}/{len(scores)}"
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
            f"{p} {out[p][0]}" + (f" ({out[p + '_votes']})" if p + "_votes" in out else "")
            for p in providers), flush=True)
        results.append(out)

    print(f"\n{'Company':26} {'You':11}" + "".join(f" | {p.capitalize():22}" for p in providers))
    for o in results:
        cells = ""
        for p in providers:
            v, fit, g = o[p]
            cells += f" | {v} {fit if fit is not None else '-':>3} {g}".ljust(25)
        print(f"{o['name'][:26]:26} {o['label']:11}{cells}")

    print()
    for p in providers:
        grades = [o[p][2] for o in results]
        print(f"{p:7} OK {grades.count('OK')}  near {grades.count('near')}  "
              f"MISS {grades.count('MISS')}  errors {grades.count('ERR')}  (of {len(results)})")
    if len(providers) == 2:
        split = [o["name"] for o in results if o["ollama"][0] != o["gemini"][0]]
        both_wrong = [o["name"] for o in results
                      if o["ollama"][2] in ("near", "MISS") and o["gemini"][2] in ("near", "MISS")]
        print(f"\nModels gave different verdicts on {len(split)}: {', '.join(split) or 'none'}")
        print(f"Both differ from you on {len(both_wrong)}: {', '.join(both_wrong) or 'none'}")

    out_path = DATA / f"eval_results_{lead_file.stem}_{'_'.join(providers)}.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "my_label"] + [f"{p}_{c}" for p in providers for c in ("verdict", "fit", "grade")])
        for o in results:
            w.writerow([o["name"], o["label"]] + [x for p in providers for x in o[p]])
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
