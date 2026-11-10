"""Precision, recall and false positives for the scorer, from the saved evaluation results.

Reads the data/eval_results_*.csv files that agent.evaluate writes (your label next to the
scorer's verdict) and prints a confusion matrix and the numbers that matter for a sales team:
how many leads it sends to a rep that should have been dropped, and how many good leads it drops.
Nothing here calls a model.

Run it:  python -m agent.benchmark
         python -m agent.benchmark --files eval_results_real_leads_fetched_jobs_ollama.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import NamedTuple

DATA = Path(__file__).resolve().parent.parent / "data"
VERDICTS = ("qualify", "review", "disqualify")
HUMAN_SETS = {
    "eval_results_labelled_leads_ollama.csv": "47 made-up companies, part 1 (20)",
    "eval_results_unseen_leads_ollama.csv": "47 made-up companies, part 2 (15)",
    "eval_results_unseen_leads_2_ollama.csv": "47 made-up companies, part 3 (12, labelled blind)",
    "eval_results_real_leads_fetched_jobs_ollama.csv": "10 real companies, scored from their websites",
}


class Pair(NamedTuple):
    label: str
    verdict: str


class Rates(NamedTuple):
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0


def read_pairs(path: Path, column: str = "ollama_verdict") -> list[Pair]:
    """Your label and the scorer's verdict for every row that has both."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [Pair(r["my_label"].strip(), r[column].strip()) for r in rows
            if r.get("my_label", "").strip() in VERDICTS and r.get(column, "").strip() in VERDICTS]


def confusion(pairs: list[Pair]) -> dict[str, dict[str, int]]:
    """Rows are your labels, columns the scorer's verdicts."""
    table = {label: {verdict: 0 for verdict in VERDICTS} for label in VERDICTS}
    for p in pairs:
        table[p.label][p.verdict] += 1
    return table


def rates(pairs: list[Pair], positive: set[str]) -> Rates:
    """Counts for one yes/no question: is the lead in `positive`, by your label and by the scorer?"""
    tp = sum(p.label in positive and p.verdict in positive for p in pairs)
    fp = sum(p.label not in positive and p.verdict in positive for p in pairs)
    fn = sum(p.label in positive and p.verdict not in positive for p in pairs)
    return Rates(tp, fp, fn, len(pairs) - tp - fp - fn)


def exact(pairs: list[Pair]) -> int:
    return sum(p.label == p.verdict for p in pairs)


def report(pairs: list[Pair]) -> str:
    worth = rates(pairs, {"qualify", "review"})
    qualify = rates(pairs, {"qualify"})
    table = confusion(pairs)
    lines = [f"{len(pairs)} leads. Exact match {exact(pairs)} ({exact(pairs) / len(pairs):.0%}).", "",
             "| Your label \\ scorer | " + " | ".join(VERDICTS) + " |", "|---|---|---|---|"]
    lines += [f"| {label} | " + " | ".join(str(table[label][v]) for v in VERDICTS) + " |" for label in VERDICTS]
    lines += ["", "| Question | Precision | Recall | False positives | False negatives |", "|---|---|---|---|---|",
              f"| Worth a rep's time (qualify or review) | {worth.precision:.0%} | {worth.recall:.0%} | "
              f"{worth.fp} | {worth.fn} |",
              f"| Qualify | {qualify.precision:.0%} | {qualify.recall:.0%} | {qualify.fp} | {qualify.fn} |"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", help="comma-separated result files in data/ (default: the human-labelled sets)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    names = [n.strip() for n in args.files.split(",")] if args.files else list(HUMAN_SETS)
    every: list[Pair] = []
    for name in names:
        pairs = read_pairs(DATA / name)
        every += pairs
        print(f"## {HUMAN_SETS.get(name, name)}\n\n{report(pairs)}\n")
    if len(names) > 1:
        print(f"## All together\n\n{report(every)}")


if __name__ == "__main__":
    main()
