"""
Blind labels for data/blind_leads.csv, saved one company at a time from the web app's Label page.

Only the label and the reason are written; every other column stays as it was. Nothing here
scores a lead or reads a score, so a label can't be swayed by what the model said.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from agent.files import write_text_atomic

BLIND = Path(__file__).resolve().parent.parent / "data" / "blind_leads.csv"
LABELS = ("qualify", "review", "disqualify")
MAX_WHY = 500


def read_rows(path: Path = BLIND) -> list[dict]:
    """Every row of the blind file, in file order. An Excel-saved file (with a BOM) reads too."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def first_unlabelled(rows: list[dict], after: int = -1) -> int:
    """Index of the first row after `after` with no label, wrapping round; -1 if all are labelled."""
    order = list(range(after + 1, len(rows))) + list(range(0, after + 1))
    return next((i for i in order if not rows[i].get("my_label", "").strip()), -1)


def save_label(index: int, name: str, label: str, why: str, path: Path = BLIND) -> None:
    """Write one label. The row must still be the company the page showed (`name`), so a file
    edited in Excel meanwhile can't get a label on the wrong company."""
    label = label.strip().lower()
    if label not in LABELS:
        raise ValueError(f"Label must be one of {', '.join(LABELS)}.")
    rows = read_rows(path)
    if not 0 <= index < len(rows) or rows[index]["name"] != name:
        raise ValueError("The file changed since this page opened. Reload and try again.")
    rows[index]["my_label"] = label
    rows[index]["why"] = " ".join(why.split())[:MAX_WHY]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomic(path, out.getvalue())
