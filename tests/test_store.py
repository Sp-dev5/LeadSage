"""Lead store round trip. No model, no network."""

import sqlite3

from agent import store
from agent.score_lead import Company, LeadScore, Location

COMPANY = Company(name="Acme", industry="B2B SaaS", size="120", location="Pune, India", description="CRM.")


def score(fit: int, verdict: str = "review") -> LeadScore:
    return LeadScore(fit=fit, verdict=verdict, criteria=[], disqualifier_hit=False, disqualifier_reason="",
                     summary="s", location=Location(country="India", in_region="yes", evidence="e"))


def test_saved_lead_comes_back(tmp_path):
    db = tmp_path / "leads.db"
    store.save(COMPANY, score(55), url="https://acme.test", text_source="fetched", path=db)
    [lead] = store.load_all(db)
    assert (lead.name, lead.score.fit, lead.score.location.country, lead.stage) == ("Acme", 55, "India", "new")


def test_only_web_addresses_are_kept_as_links(tmp_path):
    db = tmp_path / "leads.db"
    store.save(COMPANY, score(55), url="javascript:alert(1)", text_source="fetched", path=db)
    assert store.load_all(db)[0].url == ""
    assert store.StoredLead.model_validate({**store.load_all(db)[0].model_dump(), "url": " JavaScript:x"}).url == ""
    assert store.web_url("HTTPS://acme.test") == "HTTPS://acme.test"


def test_rescoring_replaces_the_score_but_keeps_the_stage(tmp_path):
    db = tmp_path / "leads.db"
    store.save(COMPANY, score(55), url="", text_source="typed", path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE leads SET stage = 'contacted'")
    store.save(COMPANY, score(80, "qualify"), url="", text_source="fetched", path=db)
    [lead] = store.load_all(db)
    assert (lead.score.fit, lead.score.verdict, lead.text_source, lead.stage) == (80, "qualify", "fetched", "contacted")


def test_a_deleted_company_cannot_be_read_back_from_the_file(tmp_path):
    db = tmp_path / "leads.db"
    secret = COMPANY.model_copy(update={"name": "Zorblax Industries", "description": "Zorblax makes widgets."})
    store.save(secret, score(55), url="https://zorblax.test", text_source="fetched", path=db)
    store.save_email("Zorblax Industries", store.Email(step=0, subject="Hello Zorblax", body="Hi Zorblax",
                                                       drafted_at="t"), db)
    assert b"Zorblax" in db.read_bytes()
    store.delete_lead("Zorblax Industries", db)
    assert store.load_all(db) == []
    assert b"Zorblax" not in db.read_bytes()


def test_a_replaced_score_leaves_no_old_text_in_the_file(tmp_path):
    db = tmp_path / "leads.db"
    store.save(COMPANY, score(55).model_copy(update={"summary": "OLDSUMMARYTEXT"}), url="", text_source="typed", path=db)
    store.save(COMPANY, score(80), url="", text_source="typed", path=db)
    assert b"OLDSUMMARYTEXT" not in db.read_bytes()


def test_all_emails_and_contacts_load_in_one_go_grouped_by_company(tmp_path):
    db = tmp_path / "leads.db"
    other = COMPANY.model_copy(update={"name": "Beta"})
    for company in (COMPANY, other):
        store.save(company, score(55), url="", text_source="typed", path=db)
    store.save_email("Acme", store.Email(step=0, subject="a0", body="b", drafted_at="t"), db)
    store.save_email("Beta", store.Email(step=0, subject="b0", body="b", drafted_at="t"), db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE leads SET stage = 'drafted'")
    store.approve_email("Acme", 0, db)
    store.mark_sent("Acme", 0, db)
    store.save_email("Acme", store.Email(step=1, subject="a1", body="b", drafted_at="t"), db)
    store.save_contacts("Beta", store.ContactSearch(searched_at="t"), db)
    emails = store.load_all_emails(db)
    assert [e.subject for e in emails["Acme"]] == ["a0", "a1"]
    assert [e.subject for e in emails["Beta"]] == ["b0"]
    assert list(store.load_all_contacts(db)) == ["Beta"]
    assert store.load_all_emails(tmp_path / "empty.db") == {} and store.load_all_contacts(tmp_path / "empty.db") == {}
