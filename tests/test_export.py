"""CSV export of leads and emails."""

import csv
import io

from agent import export
from agent.score_lead import Company, LeadScore
from agent.store import Email, StoredLead

COMPANY = Company(name="Acme, Inc.", industry="B2B SaaS", size="120", location="Pune, India", description="Payroll.")


def lead(verdict="review", flags=()):
    score = LeadScore(fit=75, verdict=verdict, criteria=[], disqualifier_hit=False, disqualifier_reason="",
                      summary='Sells "payroll", fits.', review_flags=list(flags))
    return StoredLead(name="Acme, Inc.", url="https://acme.example", text_source="fetched", company=COMPANY,
                      score=score, scored_at="2026-10-25T00:00:00+00:00", stage="drafted")


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_leads_csv_keeps_commas_and_quotes_intact():
    out = rows(export.leads_csv([lead(flags=["sales motion unknown"])], {"Acme, Inc.": "Priya Rao"}))
    assert out == [out[0]] and list(out[0]) == export.LEAD_COLUMNS
    assert (out[0]["company"], out[0]["contact"], out[0]["held_back_by"], out[0]["summary"]) == (
        "Acme, Inc.", "Priya Rao", "sales motion unknown", 'Sells "payroll", fits.')


def test_emails_csv_one_row_per_email_in_step_order():
    emails = {"Acme, Inc.": [
        Email(step=1, subject="Re: hi", body="Second", drafted_at="b", flags=["too long", "no hook"]),
        Email(step=0, subject="hi", body="Line one\nLine two", drafted_at="a", status="sent", sent_at="2026-10-20"),
    ]}
    out = rows(export.emails_csv([lead()], emails, {}))
    assert [r["step"] for r in out] == ["first", "follow-up 1"]
    assert out[0]["body"] == "Line one\nLine two" and out[0]["sent_at"] == "2026-10-20"
    assert out[1]["flags"] == "too long; no hook" and out[1]["contact"] == ""


def test_leads_without_emails_add_no_email_rows():
    assert rows(export.emails_csv([lead()], {}, {})) == []
