"""Sending from the rep's mailbox and finding replies, against fake SMTP and IMAP servers. No network."""

from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest

from agent import contacts, mailer, store
from tests.test_outreach import CFG, COMPANY, LEAD, SCORE

BODY = "Hi Priya,\n\nYou are hiring an Account Executive.\n\nSam\nLeadSage\n1 Test Street, Pune\n\n" + CFG.opt_out_line
PRIYA = contacts.Contact(name="Priya Rao", title="VP Sales", matches_buyer="yes", source="https://acme.example/team",
                         fetched_at="2026-10-24T00:00:00+00:00", email="priya@acme.example")


class FakeSMTP:
    def __init__(self):
        self.sent, self.logins = [], []

    def login(self, user, password):
        self.logins.append(user)

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        pass


@pytest.fixture
def mailbox(monkeypatch):
    for key, value in {"SMTP_HOST": "smtp.example", "SMTP_USER": "sam@seller.example", "SMTP_PASSWORD": "pw",
                       "IMAP_HOST": "imap.example"}.items():
        monkeypatch.setenv(key, value)
    server = FakeSMTP()
    monkeypatch.setattr(mailer, "_smtp", lambda s: server)
    return server


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "leads.db"
    store.save(COMPANY, SCORE, url=LEAD.url, text_source="typed", path=path)
    store.save_contacts("Acme", contacts.ContactSearch(contact=PRIYA, chosen_by="buyer", people=[PRIYA]), path)
    return path


def approved(db, step=0, body=BODY, subject="Your AE hire"):
    store.save_email("Acme", store.Email(step=step, subject=subject, body=body, drafted_at="t"), db)
    store.approve_email("Acme", step, db)
    return store.load("Acme", db)


def test_without_settings_nothing_is_sent(monkeypatch, db):
    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(mailer, "_smtp", lambda s: pytest.fail("connected"))
    assert not mailer.can_send()
    with pytest.raises(mailer.SendError, match="not set up"):
        mailer.send(approved(db), 0, CFG, db)


def test_an_approved_email_goes_out_exactly_as_approved_and_is_marked_sent(mailbox, db):
    assert mailer.send(approved(db), 0, CFG, db) == "priya@acme.example"
    msg = mailbox.sent[0]
    assert (msg["To"], msg["From"], msg["Subject"]) == ("Priya Rao <priya@acme.example>", "Sam <sam@seller.example>",
                                                        "Your AE hire")
    assert msg.get_content().strip() == BODY.strip()
    sent = store.load_emails("Acme", db)[0]
    assert sent.status == "sent" and sent.message_id == msg["Message-ID"]
    assert store.load("Acme", db).stage == "contacted"


def test_a_follow_up_goes_in_the_same_thread(mailbox, db):
    mailer.send(approved(db), 0, CFG, db)
    mailer.send(approved(db, step=1, subject="Re: Your AE hire"), 1, CFG, db)
    first, second = mailbox.sent
    assert second["In-Reply-To"] == first["Message-ID"] and second["References"] == first["Message-ID"]


def test_a_draft_is_never_sent(mailbox, db):
    store.save_email("Acme", store.Email(step=0, subject="s", body=BODY, drafted_at="t"), db)
    with pytest.raises(ValueError, match="not approved"):
        mailer.send(store.load("Acme", db), 0, CFG, db)
    assert mailbox.sent == []


def test_no_address_no_send(mailbox, db):
    store.save_contacts("Acme", contacts.ContactSearch(contact=PRIYA.model_copy(update={"email": ""}),
                                                       chosen_by="buyer"), db)
    with pytest.raises(mailer.SendError, match="email address"):
        mailer.send(approved(db), 0, CFG, db)
    assert mailbox.sent == []


def test_the_daily_cap_stops_sending(mailbox, db):
    cfg = CFG.model_copy(update={"max_sends_per_day": 1})
    mailer.send(approved(db), 0, cfg, db)
    with pytest.raises(mailer.SendError, match="limit of 1"):
        mailer.send(approved(db, step=1), 1, cfg, db)
    assert len(mailbox.sent) == 1


def test_an_email_without_the_opt_out_line_is_not_sent(mailbox, db):
    lead = approved(db)
    store.edit_email("Acme", 0, "s", "Hi Priya, no footer.", db)
    store.approve_email("Acme", 0, db)
    with pytest.raises(mailer.SendError, match="opt-out"):
        mailer.send(lead, 0, CFG, db)
    assert mailbox.sent == []


@pytest.mark.parametrize("bad", ["priya", "a@b.com, c@d.com", "Priya <p@acme.example>", "p@acme.example\nBcc: x@y.z",
                                 "p @acme.example", "x" * 250 + "@acme.example"])
def test_only_a_plain_single_address_is_accepted(bad):
    with pytest.raises(ValueError):
        mailer.valid_address(bad)


def test_a_line_break_in_the_subject_cannot_add_a_header(mailbox, db):
    with pytest.raises(ValueError):
        mailer.send(approved(db, subject="Hi\nBcc: everyone@example.com"), 0, CFG, db)
    assert mailbox.sent == [] and store.load_emails("Acme", db)[0].status == "approved"


def reply(body, sender="priya@acme.example", in_reply_to="", **headers):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, "sam@seller.example", "Re: Your AE hire"
    msg["Message-ID"], msg["Date"] = f"<r{abs(hash(body))}@acme.example>", "Tue, 27 Oct 2026 10:00:00 +0000"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    for k, v in headers.items():
        msg[k.replace("_", "-")] = v
    msg.set_content(body)
    return msg.as_bytes()


class FakeIMAP:
    """Answers SEARCH by matching the From address or a header against the stored messages."""

    def __init__(self, messages):
        self.messages, self.readonly, self.searches = messages, None, []

    def login(self, user, password):
        pass

    def select(self, box, readonly=False):
        self.readonly = readonly

    def search(self, charset, *criteria):
        self.searches.append(criteria)
        hits = []
        for i, raw in enumerate(self.messages, 1):
            msg = message_from_bytes(raw, policy=policy.default)
            if "FROM" in criteria and criteria[-1].strip('"') in msg["From"]:
                hits.append(str(i).encode())
            if "HEADER" in criteria and criteria[-1] in (msg.get(criteria[-2]) or ""):
                hits.append(str(i).encode())
        return "OK", [b" ".join(hits)]

    def fetch(self, i, spec):
        assert "PEEK" in spec
        return "OK", [(b"1 (BODY[] {1}", self.messages[int(i) - 1]), b")"]

    def logout(self):
        pass


def check(db, messages):
    imap = FakeIMAP(messages)
    return mailer.check_replies(CFG, db, report=lambda line: None, connect=lambda s: imap), imap


def test_a_reply_stops_the_follow_ups_and_reading_is_read_only(mailbox, db):
    mailer.send(approved(db), 0, CFG, db)
    sent_id = store.load_emails("Acme", db)[0].message_id
    found, imap = check(db, [reply("Sounds good, call me Thursday.\n\nOn Mon, Sam wrote:\n> " + CFG.opt_out_line,
                                   in_reply_to=sent_id)])
    assert imap.readonly is True
    assert [(lead, r.snippet, r.opt_out) for lead, r in found] == [("Acme", "Sounds good, call me Thursday.", False)]
    assert store.load("Acme", db).stage == "replied" and "Acme" not in store.suppressed(db)


def test_the_same_reply_is_kept_once(mailbox, db):
    mailer.send(approved(db), 0, CFG, db)
    messages = [reply("Thanks, not now.")]
    check(db, messages)
    store.set_stage("Acme", "contacted", db)
    found, _ = check(db, messages)
    assert found == [] and len(store.load_replies("Acme", db)) == 1


@pytest.mark.parametrize("text", ["Please unsubscribe me.", "STOP", "Not interested, thanks.", "remove me from your list"])
def test_an_opt_out_reply_adds_the_company_to_the_do_not_contact_list(mailbox, db, text):
    mailer.send(approved(db), 0, CFG, db)
    found, _ = check(db, [reply(text)])
    assert found[0][1].opt_out and "Acme" in store.suppressed(db) and store.load("Acme", db).stage == "lost"


def test_an_out_of_office_reply_is_not_a_reply(mailbox, db):
    mailer.send(approved(db), 0, CFG, db)
    found, _ = check(db, [reply("I am away until Monday.", Auto_Submitted="auto-replied")])
    assert found == [] and store.load("Acme", db).stage == "contacted"


def test_mail_from_someone_else_is_never_read(mailbox, db):
    mailer.send(approved(db), 0, CFG, db)
    found, imap = check(db, [reply("Unrelated newsletter", sender="news@other.example")])
    assert found == [] and all("news@other.example" not in " ".join(c) for c in imap.searches)
