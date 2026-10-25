"""
The lead store: every scored company with its research, its score, the person to write
to, the emails drafted for it and its stage in the sales pipeline, in one SQLite file
(data/leadsage.db, git-ignored).

Scoring a company again replaces its research and score but keeps its stage.

Stages move forward on their own as the rep works (drafted, approved, contacted) and
never back. Replied, meeting and lost are set by the rep and stop the sequence: emails
not yet sent are marked stopped.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from agent.contacts import ContactSearch
from agent.hooks import NewsSearch
from agent.score_lead import Company, LeadScore

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "leadsage.db"

STAGES = ["new", "drafted", "approved", "contacted", "replied", "meeting", "lost"]
STOPPED = {"replied", "meeting", "lost"}

SCHEMA = """CREATE TABLE IF NOT EXISTS leads (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    text_source TEXT NOT NULL,
    company TEXT NOT NULL,
    score TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS contacts (
    lead TEXT PRIMARY KEY,
    search TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news (
    lead TEXT PRIMARY KEY,
    search TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS emails (
    lead TEXT NOT NULL,
    step INTEGER NOT NULL,
    email TEXT NOT NULL,
    PRIMARY KEY (lead, step)
);"""


class StoredLead(BaseModel):
    """One company as the app shows it."""

    name: str
    url: str
    text_source: str
    company: Company
    score: LeadScore
    scored_at: str
    stage: str


class Citation(BaseModel):
    """One sentence about the prospect and the company's own words behind it."""

    sentence: str
    quote: str
    source: str
    fetched_at: str


class Email(BaseModel):
    """One email in a lead's sequence. Step 0 is the first email, 1 and up are follow-ups."""

    step: int
    subject: str
    body: str
    hook: str = ""
    citations: list[Citation] = []
    rejected: list[str] = []
    flags: list[str] = []
    status: Literal["draft", "approved", "sent", "stopped"] = "draft"
    model: str = ""
    drafted_at: str
    approved_at: str = ""
    sent_at: str = ""
    edited: bool = False


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save(company: Company, score: LeadScore, url: str, text_source: str, path: Path = DB_PATH) -> None:
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            """INSERT INTO leads (name, url, text_source, company, score, scored_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET url = excluded.url, text_source = excluded.text_source,
                   company = excluded.company, score = excluded.score, scored_at = excluded.scored_at""",
            (company.name, url, text_source, company.model_dump_json(), score.model_dump_json(), now()),
        )


def _lead(r: sqlite3.Row) -> StoredLead:
    return StoredLead(name=r["name"], url=r["url"], text_source=r["text_source"],
                      company=Company.model_validate_json(r["company"]),
                      score=LeadScore.model_validate_json(r["score"]),
                      scored_at=r["scored_at"], stage=r["stage"])


def load_all(path: Path = DB_PATH) -> list[StoredLead]:
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT * FROM leads").fetchall()
    return [_lead(r) for r in rows]


def load(name: str, path: Path = DB_PATH) -> StoredLead:
    with closing(_connect(path)) as conn:
        r = conn.execute("SELECT * FROM leads WHERE name = ?", (name,)).fetchone()
    if r is None:
        raise KeyError(f"no lead named {name!r}")
    return _lead(r)


def _stage(conn: sqlite3.Connection, lead: str) -> str:
    r = conn.execute("SELECT stage FROM leads WHERE name = ?", (lead,)).fetchone()
    if r is None:
        raise KeyError(f"no lead named {lead!r}")
    return r["stage"]


def _advance(conn: sqlite3.Connection, lead: str, stage: str) -> None:
    """Move the lead forward to `stage`; never back, never out of a stopped stage."""
    current = _stage(conn, lead)
    if current not in STOPPED and STAGES.index(stage) > STAGES.index(current):
        conn.execute("UPDATE leads SET stage = ? WHERE name = ?", (stage, lead))


def set_stage(lead: str, stage: str, path: Path = DB_PATH) -> None:
    """The rep sets the stage. A stopping stage marks every unsent email stopped."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; use one of {STAGES}")
    with closing(_connect(path)) as conn, conn:
        _stage(conn, lead)
        conn.execute("UPDATE leads SET stage = ? WHERE name = ?", (stage, lead))
        if stage in STOPPED:
            for email in _emails(conn, lead):
                if email.status in ("draft", "approved"):
                    _put_email(conn, lead, email.model_copy(update={"status": "stopped"}))


def save_contacts(lead: str, search: ContactSearch, path: Path = DB_PATH) -> None:
    with closing(_connect(path)) as conn, conn:
        conn.execute("INSERT INTO contacts (lead, search) VALUES (?, ?) "
                     "ON CONFLICT(lead) DO UPDATE SET search = excluded.search",
                     (lead, search.model_dump_json()))


def load_contacts(lead: str, path: Path = DB_PATH) -> ContactSearch | None:
    with closing(_connect(path)) as conn:
        r = conn.execute("SELECT search FROM contacts WHERE lead = ?", (lead,)).fetchone()
    return ContactSearch.model_validate_json(r["search"]) if r else None


def save_news(lead: str, search: NewsSearch, path: Path = DB_PATH) -> None:
    with closing(_connect(path)) as conn, conn:
        conn.execute("INSERT INTO news (lead, search) VALUES (?, ?) "
                     "ON CONFLICT(lead) DO UPDATE SET search = excluded.search",
                     (lead, search.model_dump_json()))


def load_news(lead: str, path: Path = DB_PATH) -> NewsSearch | None:
    with closing(_connect(path)) as conn:
        r = conn.execute("SELECT search FROM news WHERE lead = ?", (lead,)).fetchone()
    return NewsSearch.model_validate_json(r["search"]) if r else None


def _emails(conn: sqlite3.Connection, lead: str) -> list[Email]:
    rows = conn.execute("SELECT email FROM emails WHERE lead = ? ORDER BY step", (lead,)).fetchall()
    return [Email.model_validate_json(r["email"]) for r in rows]


def _put_email(conn: sqlite3.Connection, lead: str, email: Email) -> None:
    conn.execute("INSERT INTO emails (lead, step, email) VALUES (?, ?, ?) "
                 "ON CONFLICT(lead, step) DO UPDATE SET email = excluded.email",
                 (lead, email.step, email.model_dump_json()))


def _email(conn: sqlite3.Connection, lead: str, step: int) -> Email | None:
    return next((e for e in _emails(conn, lead) if e.step == step), None)


def load_emails(lead: str, path: Path = DB_PATH) -> list[Email]:
    with closing(_connect(path)) as conn:
        return _emails(conn, lead)


def save_email(lead: str, email: Email, path: Path = DB_PATH) -> None:
    """Save a new draft (or replace an unsent one). A sent email is never replaced."""
    with closing(_connect(path)) as conn, conn:
        if _stage(conn, lead) in STOPPED:
            raise ValueError(f"{lead}: sequence stopped, no new drafts")
        old = _email(conn, lead, email.step)
        if old and old.status == "sent":
            raise ValueError(f"{lead}: email {email.step} was already sent")
        _put_email(conn, lead, email)
        if email.step == 0:
            _advance(conn, lead, "drafted")


def edit_email(lead: str, step: int, subject: str, body: str, path: Path = DB_PATH) -> None:
    """The rep's own wording. An edited email needs approving again."""
    with closing(_connect(path)) as conn, conn:
        old = _email(conn, lead, step)
        if old is None or old.status in ("sent", "stopped"):
            raise ValueError(f"{lead}: email {step} can't be edited")
        _put_email(conn, lead, old.model_copy(update={"subject": subject, "body": body, "edited": True,
                                                      "status": "draft", "approved_at": ""}))


def approve_email(lead: str, step: int, path: Path = DB_PATH) -> None:
    with closing(_connect(path)) as conn, conn:
        old = _email(conn, lead, step)
        if old is None or old.status != "draft":
            raise ValueError(f"{lead}: email {step} is not a draft")
        _put_email(conn, lead, old.model_copy(update={"status": "approved", "approved_at": now()}))
        if step == 0:
            _advance(conn, lead, "approved")


def mark_sent(lead: str, step: int, path: Path = DB_PATH) -> None:
    """The rep sent it from their own mailbox. Only an approved email can be sent."""
    with closing(_connect(path)) as conn, conn:
        old = _email(conn, lead, step)
        if old is None or old.status != "approved":
            raise ValueError(f"{lead}: email {step} is not approved")
        if step > 0 and not any(e.step == step - 1 and e.status == "sent" for e in _emails(conn, lead)):
            raise ValueError(f"{lead}: email {step - 1} has not been sent")
        _put_email(conn, lead, old.model_copy(update={"status": "sent", "sent_at": now()}))
        if step == 0:
            _advance(conn, lead, "contacted")
