"""Operational failures: a send clicked twice, a crash between delivery and the store, a refused
send, writes from several threads at once, a model that times out, and a batch where one lead
fails. No network, no model."""

import smtplib
import sqlite3
import threading
import time

import httpx
import pytest

from agent import llm, mailer, pipeline, store
from agent.score_lead import Company
from tests.test_llm import Fact, ollama_calls
from tests.test_mailer import FakeSMTP, approved, db, mailbox
from tests.test_outreach import CFG
from tests.test_pipeline import lead_score
from tests.test_scoring import make_icp


class SlowSMTP(FakeSMTP):
    def send_message(self, msg):
        time.sleep(0.2)
        super().send_message(msg)


def test_two_sends_at_once_deliver_one_email(mailbox, db, monkeypatch):
    server = SlowSMTP()
    monkeypatch.setattr(mailer, "_smtp", lambda s: server)
    lead = approved(db)
    results = []

    def click():
        try:
            results.append(mailer.send(lead, 0, CFG, db))
        except ValueError as e:
            results.append(e)

    clicks = [threading.Thread(target=click) for _ in range(2)]
    for t in clicks:
        t.start()
    for t in clicks:
        t.join()
    assert len(server.sent) == 1
    assert sum(isinstance(r, str) for r in results) == 1


def test_a_sent_email_is_never_sent_again(mailbox, db):
    mailer.send(approved(db), 0, CFG, db)
    with pytest.raises(ValueError, match="not approved"):
        mailer.send(store.load("Acme", db), 0, CFG, db)
    assert len(mailbox.sent) == 1


def test_a_crash_after_delivery_blocks_a_second_send_until_the_rep_checks(mailbox, db, monkeypatch):
    lead = approved(db)
    real_mark_sent = store.mark_sent

    def disk_full(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "mark_sent", disk_full)
    with pytest.raises(sqlite3.OperationalError):
        mailer.send(lead, 0, CFG, db)
    monkeypatch.setattr(store, "mark_sent", real_mark_sent)
    assert len(mailbox.sent) == 1

    with pytest.raises(mailer.SendError, match="Sent folder"):
        mailer.send(store.load("Acme", db), 0, CFG, db)
    assert len(mailbox.sent) == 1

    store.mark_sent("Acme", 0, db)
    email = store.load_emails("Acme", db)[0]
    assert email.status == "sent" and email.message_id == mailbox.sent[0]["Message-ID"]


@pytest.mark.parametrize("failure", [
    smtplib.SMTPAuthenticationError(535, b"bad password"),
    smtplib.SMTPRecipientsRefused({"priya@acme.example": (550, b"no such user")}),
    smtplib.SMTPDataError(554, b"rejected as spam"),
    ConnectionRefusedError("no route"),
])
def test_a_send_the_server_refused_can_be_tried_again(mailbox, db, monkeypatch, failure):
    class Refusing(FakeSMTP):
        def login(self, user, password):
            if isinstance(failure, (smtplib.SMTPAuthenticationError, ConnectionRefusedError)):
                raise failure

        def send_message(self, msg):
            raise failure

    monkeypatch.setattr(mailer, "_smtp", lambda s: Refusing())
    with pytest.raises(type(failure)):
        mailer.send(approved(db), 0, CFG, db)
    email = store.load_emails("Acme", db)[0]
    assert email.status == "approved" and email.message_id == ""

    monkeypatch.setattr(mailer, "_smtp", lambda s: mailbox)
    mailer.send(store.load("Acme", db), 0, CFG, db)
    assert len(mailbox.sent) == 1 and store.load_emails("Acme", db)[0].status == "sent"


def test_a_connection_lost_mid_send_is_treated_as_maybe_delivered(mailbox, db, monkeypatch):
    class DropsDuringData(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPServerDisconnected("connection closed")

    monkeypatch.setattr(mailer, "_smtp", lambda s: DropsDuringData())
    with pytest.raises(smtplib.SMTPServerDisconnected):
        mailer.send(approved(db), 0, CFG, db)
    monkeypatch.setattr(mailer, "_smtp", lambda s: mailbox)
    with pytest.raises(mailer.SendError, match="Sent folder"):
        mailer.send(store.load("Acme", db), 0, CFG, db)
    assert mailbox.sent == []


def company(name):
    return Company(name=name, industry="SaaS", size="120", location="Pune, India", description="CRM software.")


def test_writes_from_several_threads_all_land(tmp_path):
    path = tmp_path / "leads.db"
    errors = []

    def worker(i):
        try:
            for j in range(20):
                name = f"Co {i}-{j}"
                store.save(company(name), lead_score(80, "qualify", "main"), url="", text_source="typed", path=path)
                store.save_email(name, store.Email(step=0, subject="s", body="b", drafted_at="t"), path)
                store.approve_email(name, 0, path)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(store.load_all(path)) == 160
    assert all(e[0].status == "approved" for e in store.load_all_emails(path).values())


def test_a_model_timeout_goes_once_to_the_other_model(ollama_calls):
    calls, plan = ollama_calls
    plan["qwen3.5:9b"] = httpx.ReadTimeout("timed out")
    plan["qwen3.5:4b"] = '{"value": "yes"}'
    assert llm.generate_structured("s", "u", Fact).value == "yes"
    assert calls == ["qwen3.5:9b", "qwen3.5:4b"]


def batch(monkeypatch, tmp_path, score=None, save_fails_for=()):
    def research(row, icp, signals):
        return company(row["name"]), "typed"

    def scorer(icp, c, model=None):
        if c.name == "Slow":
            raise httpx.ReadTimeout("timed out")
        return (score or {}).get(c.name, lead_score(85, "qualify", model or "main"))

    real_save = store.save

    def save(c, s, **kwargs):
        if c.name in save_fails_for:
            raise sqlite3.OperationalError("database is locked")
        return real_save(c, s, **kwargs)

    monkeypatch.setattr(pipeline, "research", research)
    monkeypatch.setattr(pipeline, "score_lead", scorer)
    monkeypatch.setattr(pipeline.store, "save", save)
    lines = []
    path = tmp_path / "leads.db"
    return path, lines, lambda names: pipeline.score_all([{"name": n, "url": ""} for n in names], make_icp(), {},
                                                          report=lines.append, db=path)


def test_a_lead_whose_model_times_out_does_not_stop_the_batch(monkeypatch, tmp_path):
    path, lines, run = batch(monkeypatch, tmp_path)
    run(["Acme", "Slow", "Zeta"])
    assert {lead.name for lead in store.load_all(path)} == {"Acme", "Zeta"}
    assert any(line.startswith("[2/3] Slow: failed") for line in lines)


def test_a_store_failure_for_one_lead_does_not_stop_the_batch(monkeypatch, tmp_path):
    path, lines, run = batch(monkeypatch, tmp_path, save_fails_for={"Acme"})
    run(["Acme", "Zeta"])
    assert {lead.name for lead in store.load_all(path)} == {"Zeta"}
    assert any("Acme" in line and "not saved" in line for line in lines)


def test_a_store_failure_after_a_second_opinion_does_not_stop_the_others(monkeypatch, tmp_path):
    edge = {"Edge": lead_score(55, "review", "main"), "Edge2": lead_score(55, "review", "main")}
    path, lines, run = batch(monkeypatch, tmp_path, score=edge)
    real_save = store.save
    saved = []

    def save_once_then_fail(c, s, **kwargs):
        saved.append(c.name)
        if c.name == "Edge" and saved.count("Edge") == 2:
            raise sqlite3.OperationalError("database is locked")
        return real_save(c, s, **kwargs)

    monkeypatch.setattr(pipeline.store, "save", save_once_then_fail)
    run(["Edge", "Edge2"])
    assert saved.count("Edge2") == 2
    assert any("Edge" in line and "not saved" in line for line in lines)
