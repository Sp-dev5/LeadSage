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
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

from agent.contacts import ContactSearch
from agent.fetch import now
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
CREATE TABLE IF NOT EXISTS suppressed (
    lead TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    added_at TEXT NOT NULL
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
);
CREATE TABLE IF NOT EXISTS signal_events (
    lead TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    detail TEXT NOT NULL,
    source TEXT NOT NULL,
    quote TEXT NOT NULL,
    item_date TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    fired INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (lead, kind, key)
);
CREATE TABLE IF NOT EXISTS watched (
    lead TEXT NOT NULL,
    kind TEXT NOT NULL,
    first_watched TEXT NOT NULL,
    last_watched TEXT NOT NULL,
    PRIMARY KEY (lead, kind)
);
CREATE TABLE IF NOT EXISTS replies (
    lead TEXT NOT NULL,
    message_id TEXT NOT NULL,
    reply TEXT NOT NULL,
    PRIMARY KEY (lead, message_id)
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

    @field_validator("url")
    @classmethod
    def _web_only(cls, url: str) -> str:
        return web_url(url)


def web_url(url: str) -> str:
    """The address if it is a web page, else "". A javascript: or data: address from a lead list
    would run as the app when its link is clicked."""
    return url if url.strip().lower().startswith(("http://", "https://")) else ""


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
    hook_kind: str = ""
    citations: list[Citation] = []
    rejected: list[str] = []
    flags: list[str] = []
    status: Literal["draft", "approved", "sent", "stopped"] = "draft"
    model: str = ""
    drafted_at: str
    approved_at: str = ""
    sent_at: str = ""
    edited: bool = False
    message_id: str = ""


class Reply(BaseModel):
    """A message in the rep's inbox that answers one of our emails. Only these fields are kept:
    the start of the text, without the quoted email below it."""

    message_id: str
    received_at: str
    sender: str
    subject: str
    snippet: str
    opt_out: bool = False
    found_at: str = ""


class SignalEvent(BaseModel):
    """One signal the watcher saw: an open sales role or a dated news item, with where it was read.
    `quote` is the role title or headline as the source writes it; `detail` is the whole item as JSON.
    `fired` is set once, when the event is first stored, and never changes."""

    kind: Literal["hiring", "news"]
    key: str
    detail: str
    source: str
    quote: str
    item_date: str = ""
    first_seen: str = ""
    last_seen: str = ""
    fired: bool = False


def _connect(path: Path) -> sqlite3.Connection:
    """Open the store. secure_delete makes SQLite overwrite deleted or replaced rows with zeros;
    without it the old text stays in the file until the space is reused, so a deleted company
    could still be read back from data/leadsage.db."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA secure_delete = ON")
    conn.executescript(SCHEMA)
    return conn


def save(company: Company, score: LeadScore, url: str, text_source: str, path: Path = DB_PATH) -> None:
    """Save a scored company. One on the do-not-contact list starts out lost, so scoring it
    again can never reopen it."""
    with closing(_connect(path)) as conn, conn:
        stage = "lost" if _is_suppressed(conn, company.name) else "new"
        conn.execute(
            """INSERT INTO leads (name, url, text_source, company, score, scored_at, stage)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET url = excluded.url, text_source = excluded.text_source,
                   company = excluded.company, score = excluded.score, scored_at = excluded.scored_at""",
            (company.name, web_url(url), text_source, company.model_dump_json(), score.model_dump_json(), now(), stage),
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
        if stage not in STOPPED and _is_suppressed(conn, lead):
            raise ValueError(f"{lead} is on the do-not-contact list: remove it from the list first")
        conn.execute("UPDATE leads SET stage = ? WHERE name = ?", (stage, lead))
        if stage in STOPPED:
            for email in _emails(conn, lead):
                if email.status in ("draft", "approved"):
                    _put_email(conn, lead, email.model_copy(update={"status": "stopped"}))


def _is_suppressed(conn: sqlite3.Connection, lead: str) -> bool:
    return conn.execute("SELECT 1 FROM suppressed WHERE lead = ?", (lead,)).fetchone() is not None


def suppress(lead: str, reason: str = "", path: Path = DB_PATH) -> None:
    """Put a company on the do-not-contact list. If it is a lead, it becomes lost and its unsent
    emails are stopped. The company may not be a lead yet: the list also keeps it from being drafted
    for later."""
    with closing(_connect(path)) as conn, conn:
        conn.execute("INSERT INTO suppressed (lead, reason, added_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(lead) DO UPDATE SET reason = excluded.reason",
                     (lead, reason.strip(), now()))
        exists = conn.execute("SELECT 1 FROM leads WHERE name = ?", (lead,)).fetchone()
    if exists:
        set_stage(lead, "lost", path)


def unsuppress(lead: str, path: Path = DB_PATH) -> None:
    """Take a company off the list. Its stage stays lost until the rep sets another."""
    with closing(_connect(path)) as conn, conn:
        conn.execute("DELETE FROM suppressed WHERE lead = ?", (lead,))


def suppressed(path: Path = DB_PATH) -> dict[str, tuple[str, str]]:
    """Company -> (reason, when it was added)."""
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT lead, reason, added_at FROM suppressed ORDER BY added_at").fetchall()
    return {r["lead"]: (r["reason"], r["added_at"]) for r in rows}


def delete_lead(lead: str, path: Path = DB_PATH) -> None:
    """Remove everything stored about a company: its score, contacts, news, emails and watched
    signals. The do-not-contact list is kept, so a company that asked to be left alone stays left alone."""
    with closing(_connect(path)) as conn, conn:
        conn.execute("DELETE FROM replies WHERE lead = ?", (lead,))
        conn.execute("DELETE FROM signal_events WHERE lead = ?", (lead,))
        conn.execute("DELETE FROM watched WHERE lead = ?", (lead,))
        conn.execute("DELETE FROM emails WHERE lead = ?", (lead,))
        conn.execute("DELETE FROM news WHERE lead = ?", (lead,))
        conn.execute("DELETE FROM contacts WHERE lead = ?", (lead,))
        conn.execute("DELETE FROM leads WHERE name = ?", (lead,))


def save_contacts(lead: str, search: ContactSearch, path: Path = DB_PATH) -> None:
    with closing(_connect(path)) as conn, conn:
        conn.execute("INSERT INTO contacts (lead, search) VALUES (?, ?) "
                     "ON CONFLICT(lead) DO UPDATE SET search = excluded.search",
                     (lead, search.model_dump_json()))


def load_contacts(lead: str, path: Path = DB_PATH) -> ContactSearch | None:
    with closing(_connect(path)) as conn:
        r = conn.execute("SELECT search FROM contacts WHERE lead = ?", (lead,)).fetchone()
    return ContactSearch.model_validate_json(r["search"]) if r else None


def load_all_contacts(path: Path = DB_PATH) -> dict[str, ContactSearch]:
    """Every saved contact search in one query: company name -> its search."""
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT lead, search FROM contacts").fetchall()
    return {r["lead"]: ContactSearch.model_validate_json(r["search"]) for r in rows}


def save_news(lead: str, search: NewsSearch, path: Path = DB_PATH) -> None:
    with closing(_connect(path)) as conn, conn:
        conn.execute("INSERT INTO news (lead, search) VALUES (?, ?) "
                     "ON CONFLICT(lead) DO UPDATE SET search = excluded.search",
                     (lead, search.model_dump_json()))


def load_news(lead: str, path: Path = DB_PATH) -> NewsSearch | None:
    with closing(_connect(path)) as conn:
        r = conn.execute("SELECT search FROM news WHERE lead = ?", (lead,)).fetchone()
    return NewsSearch.model_validate_json(r["search"]) if r else None


def known_events(lead: str, kind: str, path: Path = DB_PATH) -> set[str]:
    """The keys of every event of this kind already stored for the lead."""
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT key FROM signal_events WHERE lead = ? AND kind = ?", (lead, kind)).fetchall()
    return {r["key"] for r in rows}


def record_events(lead: str, events: list[SignalEvent], path: Path = DB_PATH) -> list[SignalEvent]:
    """Store what the watcher saw. A key already stored only gets its last_seen moved on; a new one
    is inserted with first_seen now and its fired flag as given. Returns the events inserted."""
    stamp = now()
    inserted = []
    with closing(_connect(path)) as conn, conn:
        for e in events:
            cur = conn.execute(
                "UPDATE signal_events SET last_seen = ? WHERE lead = ? AND kind = ? AND key = ?",
                (stamp, lead, e.kind, e.key))
            if cur.rowcount:
                continue
            new = e.model_copy(update={"first_seen": stamp, "last_seen": stamp})
            conn.execute(
                "INSERT INTO signal_events (lead, kind, key, detail, source, quote, item_date, first_seen, last_seen, "
                "fired) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lead, new.kind, new.key, new.detail, new.source, new.quote, new.item_date, stamp, stamp,
                 int(new.fired)))
            inserted.append(new)
    return inserted


def fresh_events(since: str, path: Path = DB_PATH) -> dict[str, list[SignalEvent]]:
    """Fired events first seen at or after `since`, newest first: company name -> its events.
    Events stored on a baseline run never fired, so they never show here."""
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT * FROM signal_events WHERE fired = 1 AND first_seen >= ? "
                            "ORDER BY first_seen DESC", (since,)).fetchall()
    found: dict[str, list[SignalEvent]] = {}
    for r in rows:
        found.setdefault(r["lead"], []).append(SignalEvent(
            kind=r["kind"], key=r["key"], detail=r["detail"], source=r["source"], quote=r["quote"],
            item_date=r["item_date"], first_seen=r["first_seen"], last_seen=r["last_seen"], fired=bool(r["fired"])))
    return found


def is_baselined(lead: str, kind: str, path: Path = DB_PATH) -> bool:
    """True once the lead has had one successful watch of this kind."""
    with closing(_connect(path)) as conn:
        return conn.execute("SELECT 1 FROM watched WHERE lead = ? AND kind = ?", (lead, kind)).fetchone() is not None


def mark_watched(lead: str, kind: str, path: Path = DB_PATH) -> None:
    stamp = now()
    with closing(_connect(path)) as conn, conn:
        conn.execute("INSERT INTO watched (lead, kind, first_watched, last_watched) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(lead, kind) DO UPDATE SET last_watched = excluded.last_watched",
                     (lead, kind, stamp, stamp))


def _emails(conn: sqlite3.Connection, lead: str) -> list[Email]:
    rows = conn.execute("SELECT email FROM emails WHERE lead = ? ORDER BY step", (lead,)).fetchall()
    return [Email.model_validate_json(r["email"]) for r in rows]


def _put_email(conn: sqlite3.Connection, lead: str, email: Email) -> None:
    conn.execute("INSERT INTO emails (lead, step, email) VALUES (?, ?, ?) "
                 "ON CONFLICT(lead, step) DO UPDATE SET email = excluded.email",
                 (lead, email.step, email.model_dump_json()))


def _email(conn: sqlite3.Connection, lead: str, step: int) -> Email | None:
    r = conn.execute("SELECT email FROM emails WHERE lead = ? AND step = ?", (lead, step)).fetchone()
    return Email.model_validate_json(r["email"]) if r else None


def load_emails(lead: str, path: Path = DB_PATH) -> list[Email]:
    with closing(_connect(path)) as conn:
        return _emails(conn, lead)


def load_all_emails(path: Path = DB_PATH) -> dict[str, list[Email]]:
    """Every lead's emails in step order, in one query: company name -> its emails."""
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT lead, email FROM emails ORDER BY lead, step").fetchall()
    grouped: dict[str, list[Email]] = {}
    for r in rows:
        grouped.setdefault(r["lead"], []).append(Email.model_validate_json(r["email"]))
    return grouped


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


def _sendable(conn: sqlite3.Connection, lead: str, step: int) -> Email:
    old = _email(conn, lead, step)
    if old is None or old.status != "approved":
        raise ValueError(f"{lead}: email {step} is not approved")
    if step > 0 and not any(e.step == step - 1 and e.status == "sent" for e in _emails(conn, lead)):
        raise ValueError(f"{lead}: email {step - 1} has not been sent")
    return old


def check_sendable(lead: str, step: int, path: Path = DB_PATH) -> Email:
    """The approved email, if it may be sent now; raises the reason otherwise. Checked before the
    app sends, so a refusal never comes after the email has gone out."""
    with closing(_connect(path)) as conn:
        return _sendable(conn, lead, step)


def mark_sent(lead: str, step: int, path: Path = DB_PATH, message_id: str = "") -> None:
    """The email went out: sent by the rep from their own mailbox, or by the app (with the
    `message_id` it was sent under). Only an approved email can be sent."""
    with closing(_connect(path)) as conn, conn:
        old = _sendable(conn, lead, step)
        _put_email(conn, lead, old.model_copy(update={"status": "sent", "sent_at": now(), "message_id": message_id}))
        if step == 0:
            _advance(conn, lead, "contacted")


def sent_by_app_since(since: str, path: Path = DB_PATH) -> int:
    """How many emails the app itself has sent since `since` (an ISO time)."""
    return sum(1 for emails in load_all_emails(path).values() for e in emails
               if e.status == "sent" and e.message_id and e.sent_at >= since)


def save_reply(lead: str, reply: Reply, path: Path = DB_PATH) -> bool:
    """Keep a reply; False if it was already kept, so a second look at the inbox changes nothing."""
    with closing(_connect(path)) as conn, conn:
        stamped = reply.model_copy(update={"found_at": reply.found_at or now()})
        cur = conn.execute("INSERT OR IGNORE INTO replies (lead, message_id, reply) VALUES (?, ?, ?)",
                           (lead, reply.message_id, stamped.model_dump_json()))
        return cur.rowcount == 1


def load_replies(lead: str, path: Path = DB_PATH) -> list[Reply]:
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT reply FROM replies WHERE lead = ?", (lead,)).fetchall()
    return sorted((Reply.model_validate_json(r["reply"]) for r in rows), key=lambda r: r.received_at)
