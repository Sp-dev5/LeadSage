"""
Send an approved email from the rep's own mailbox, and notice replies to it.

Sending uses SMTP with the rep's own address and an app password (Gmail, Outlook and most
others offer one, free). Replies are found over IMAP, read-only: the inbox is searched on the
server for messages from a contacted person or answering one of our emails, and only those are
read. Nothing is sent without the rep's approval and click, one email at a time, and never more
than `max_sends_per_day` in a day.

Settings come from .env: SMTP_HOST, SMTP_PORT (587, or 465 for SSL), SMTP_USER (the address
emails are sent from), SMTP_PASSWORD, and IMAP_HOST, IMAP_PORT (993) for replies. Without them
the app works as before: the rep sends from their own mail program and clicks "I sent it".
"""

from __future__ import annotations

import imaplib
import os
import re
import smtplib
import ssl
import threading
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import NamedTuple

from agent import store
from agent.contacts import Contact
from agent.outreach import OutreachConfig, saved_contact, sending_problems

TIMEOUT = 30
REPLY_DAYS = 30
SNIPPET_CHARS = 500
MAX_FETCH_BYTES = 200_000
MAX_ADDRESS = 254
ADDRESS = re.compile(r"^[^@\s<>,;:\"()\[\]\\]+@[^@\s<>,;:\"()\[\]\\]+\.[A-Za-z]{2,}$")
QUOTE_START = re.compile(r"^(On .{0,200}wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+)$", re.I | re.M)
OPT_OUT = re.compile(r"\b(unsubscribe|remove me|opt[ -]?out|take me off|stop emailing|stop contacting|"
                     r"do not (email|contact)|don'?t (email|contact)|not interested|no thanks)\b", re.I)

SEND_LOCK = threading.Lock()


class MailSettings(NamedTuple):
    smtp_host: str
    smtp_port: int
    user: str
    password: str
    imap_host: str
    imap_port: int


class SendError(ValueError):
    """Why an email was not sent. Nothing left the mailbox."""


def settings() -> MailSettings | None:
    """The mailbox settings from the environment, or None when sending is not set up."""
    host, user, password = (os.environ.get(k, "").strip() for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    if not (host and user and password):
        return None
    return MailSettings(host, int(os.environ.get("SMTP_PORT", "587") or 587), user, password,
                        os.environ.get("IMAP_HOST", "").strip(), int(os.environ.get("IMAP_PORT", "993") or 993))


def can_send() -> bool:
    return settings() is not None


def can_read() -> bool:
    s = settings()
    return bool(s and s.imap_host)


def valid_address(address: str) -> str:
    """A plain email address the rep typed, trimmed; raises if it isn't one. No name part, no
    lists, no characters that could add a second recipient or a header."""
    address = address.strip()
    if len(address) > MAX_ADDRESS or not ADDRESS.match(address):
        raise ValueError("That is not an email address (like name@company.com).")
    return address


def build_message(email: store.Email, contact: Contact, cfg: OutreachConfig, sender: str,
                  earlier_ids: list[str]) -> EmailMessage:
    """The email exactly as approved, from the rep's address to the contact, in the same thread as
    the earlier emails. Header values with a line break are refused by the email library."""
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = formataddr((cfg.sender_name, sender))
    msg["To"] = formataddr((contact.name, valid_address(contact.email)))
    msg["Subject"] = email.subject
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    if earlier_ids:
        msg["In-Reply-To"] = earlier_ids[-1]
        msg["References"] = " ".join(earlier_ids)
    msg.set_content(email.body)
    return msg


def _smtp(s: MailSettings) -> smtplib.SMTP:
    if s.smtp_port == 465:
        return smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=TIMEOUT, context=ssl.create_default_context())
    conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT)
    conn.starttls(context=ssl.create_default_context())
    return conn


def _today_start() -> str:
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat()


def send(lead: store.StoredLead, step: int, cfg: OutreachConfig, db: Path = store.DB_PATH) -> str:
    """Send one approved email to the lead's contact and mark it sent. Every check runs before the
    mailbox is touched; returns the address it went to."""
    s = settings()
    if s is None:
        raise SendError("Sending is not set up: add SMTP_HOST, SMTP_USER and SMTP_PASSWORD to .env.")
    with SEND_LOCK:
        email = store.check_sendable(lead.name, step, db)
        if lead.stage in store.STOPPED:
            raise SendError(f"{lead.name}: the sequence is stopped ({lead.stage}).")
        if problems := sending_problems(email.body, cfg):
            raise SendError("can't send: " + "; ".join(problems))
        contact = saved_contact(lead, cfg, db)
        if contact is None or not contact.email:
            raise SendError("Add the contact's email address first. LeadSage never guesses one.")
        if store.sent_by_app_since(_today_start(), db) >= cfg.max_sends_per_day:
            raise SendError(f"Today's limit of {cfg.max_sends_per_day} emails is reached (max_sends_per_day).")
        earlier = [e.message_id for e in store.load_emails(lead.name, db)
                   if e.step < step and e.status == "sent" and e.message_id]
        msg = build_message(email, contact, cfg, s.user, earlier)
        conn = _smtp(s)
        try:
            conn.login(s.user, s.password)
            conn.send_message(msg)
        finally:
            try:
                conn.quit()
            except smtplib.SMTPException:
                pass
        store.mark_sent(lead.name, step, db, message_id=msg["Message-ID"])
        return contact.email


def reply_text(msg) -> str:
    """The new words of a reply: the plain text part, cut where the quoted earlier email starts."""
    part = msg.get_body(preferencelist=("plain", "html"))
    try:
        text = part.get_content() if part is not None else ""
    except (LookupError, ValueError):
        text = ""
    if part is not None and part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    if m := QUOTE_START.search(text):
        text = text[:m.start()]
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">")).strip()


def is_opt_out(text: str, cfg: OutreachConfig) -> bool:
    """The reply asks not to be emailed again. Our own opt-out line, if quoted back, doesn't count."""
    text = text.replace(cfg.opt_out_line, " ")
    first = re.sub(r"[^a-z ]", "", (text.strip().splitlines() or [""])[0].lower()).strip()
    return first == "stop" or bool(OPT_OUT.search(text))


def is_automatic(msg) -> bool:
    """An out-of-office or other machine reply, which is not the person answering."""
    auto = msg.get("Auto-Submitted", "").lower()
    return (auto not in ("", "no") or msg.get("X-Autoreply") is not None
            or msg.get("Precedence", "").lower() in ("auto_reply", "bulk", "junk"))


def _when(msg) -> str:
    try:
        return parsedate_to_datetime(msg.get("Date", "")).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def _search(conn: imaplib.IMAP4, *criteria: str) -> list[bytes]:
    status, data = conn.search(None, *criteria)
    return data[0].split() if status == "OK" and data and data[0] else []


def _criteria(emails: list[store.Email], contact: Contact | None, since: str) -> list[tuple[str, ...]]:
    found = [("SINCE", since, "HEADER", "In-Reply-To", e.message_id) for e in emails if e.message_id]
    found += [("SINCE", since, "HEADER", "References", e.message_id) for e in emails if e.message_id]
    if contact and contact.email:
        found.append(("SINCE", since, "FROM", f'"{valid_address(contact.email)}"'))
    return found


def check_replies(cfg: OutreachConfig, db: Path = store.DB_PATH, report=print,
                  connect=None) -> list[tuple[str, store.Reply]]:
    """Look in the rep's inbox (read-only) for replies to contacted leads. A reply stops the follow-ups
    (stage replied); one that asks not to be emailed also puts the company on the do-not-contact list."""
    s = settings()
    if s is None or not s.imap_host:
        raise SendError("Reply checking is not set up: add IMAP_HOST (and the SMTP settings) to .env.")
    leads = [lead for lead in store.load_all(db) if lead.stage == "contacted"]
    if not leads:
        report("No contacted leads to check.")
        return []
    conn = connect(s) if connect else imaplib.IMAP4_SSL(s.imap_host, s.imap_port, timeout=TIMEOUT,
                                                         ssl_context=ssl.create_default_context())
    found: list[tuple[str, store.Reply]] = []
    try:
        conn.login(s.user, s.password)
        conn.select("INBOX", readonly=True)
        oldest = datetime.now(timezone.utc) - timedelta(days=REPLY_DAYS)
        for lead in leads:
            emails = [e for e in store.load_emails(lead.name, db) if e.status == "sent"]
            first_sent = min((e.sent_at for e in emails), default="")
            start = max(oldest, datetime.fromisoformat(first_sent)) if first_sent else oldest
            ids: list[bytes] = []
            for criteria in _criteria(emails, saved_contact(lead, cfg, db), start.strftime("%d-%b-%Y")):
                ids += [i for i in _search(conn, *criteria) if i not in ids]
            for i in ids:
                status, data = conn.fetch(i, f"(BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)")
                raw = next((part[1] for part in data if isinstance(part, tuple)), b"") if status == "OK" else b""
                if not raw:
                    continue
                msg = message_from_bytes(raw, policy=policy.default)
                if is_automatic(msg):
                    continue
                text = reply_text(msg)
                reply = store.Reply(message_id=msg.get("Message-ID", "") or f"{lead.name}-{i.decode()}",
                                    received_at=_when(msg), sender=parseaddr(msg.get("From", ""))[1],
                                    subject=str(msg.get("Subject", ""))[:200], snippet=text[:SNIPPET_CHARS],
                                    opt_out=is_opt_out(text, cfg))
                if not store.save_reply(lead.name, reply, db):
                    continue
                found.append((lead.name, reply))
                store.set_stage(lead.name, "replied", db)
                if reply.opt_out:
                    store.suppress(lead.name, "asked not to be emailed (reply)", db)
                report(f"Reply from {lead.name} ({reply.sender})" + (": asked not to be emailed" if reply.opt_out else ""))
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
    report(f"Checked {len(leads)} contacted leads: {len(found)} new replies.")
    return found
