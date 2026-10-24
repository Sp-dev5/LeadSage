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


def test_rescoring_replaces_the_score_but_keeps_the_stage(tmp_path):
    db = tmp_path / "leads.db"
    store.save(COMPANY, score(55), url="", text_source="typed", path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE leads SET stage = 'contacted'")
    store.save(COMPANY, score(80, "qualify"), url="", text_source="fetched", path=db)
    [lead] = store.load_all(db)
    assert (lead.score.fit, lead.score.verdict, lead.text_source, lead.stage) == (80, "qualify", "fetched", "contacted")
