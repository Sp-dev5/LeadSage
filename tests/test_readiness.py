"""What the dashboard counts. No model, no network."""

import pytest

from agent import readiness as rd
from agent.score_lead import Company, LeadScore
from agent.store import Email, StoredLead
from tests.test_outreach import CFG

COMPANY = Company(name="Acme", industry="B2B SaaS", size="120", location="Pune, India", description="Payroll.")


def lead(verdict="review"):
    score = LeadScore(fit=70, verdict=verdict, criteria=[], disqualifier_hit=False, disqualifier_reason="", summary="s")
    return StoredLead(name="Acme", url="", text_source="typed", company=COMPANY, score=score,
                      scored_at="2026-10-24T00:00:00+00:00", stage="new")


def email(status="draft", hook="", kind="", step=0):
    return Email(step=step, subject="s", body="b", status=status, hook=hook, hook_kind=kind, drafted_at="x")


@pytest.mark.parametrize("hook, kind, label", [
    ("", "", ""),
    ("they have open sales roles: Account Executive", "open sales roles", "Hiring"),
    ("Oct 20, 2026, on their own site: Launch", "recent news", "News"),
    ("they have open sales roles: Account Executive", "", "Hiring"),
    ("Oct 20, 2026, on their own site: Launch", "", "News"),
])
def test_hook_label(hook, kind, label):
    assert rd.hook_label(email(hook=hook, kind=kind)) == label


def test_no_email_means_no_hook_label():
    assert rd.hook_label(None) == ""


def test_a_lead_with_hook_contact_and_a_draft_is_ready():
    r = rd.readiness(lead(), [email(hook="they have open sales roles: x", kind="open sales roles")], "Priya Rao", CFG)
    assert (r.worth_contacting, r.drafted, r.hook, r.contact, r.status, r.ready) == (
        True, True, "Hiring", "Priya Rao", "draft", True)


@pytest.mark.parametrize("emails, contact, ready", [
    ([email(hook="h", kind="recent news")], "", False),
    ([email()], "Priya Rao", False),
    ([], "Priya Rao", False),
    ([email("approved", hook="h", kind="recent news")], "Priya Rao", False),
    ([email("sent", hook="h", kind="recent news")], "Priya Rao", False),
])
def test_ready_needs_a_draft_a_hook_and_a_contact(emails, contact, ready):
    assert rd.readiness(lead(), emails, contact, CFG).ready is ready


def test_disqualified_leads_are_not_worth_contacting():
    assert rd.readiness(lead("disqualify"), [], "", CFG).worth_contacting is False


def test_funnel_counts():
    rows = [
        rd.readiness(lead(), [email(hook="h", kind="recent news")], "A", CFG),
        rd.readiness(lead(), [email(hook="h", kind="recent news")], "", CFG),
        rd.readiness(lead(), [email()], "A", CFG),
        rd.readiness(lead("disqualify"), [], "", CFG),
        rd.readiness(lead(), [email("approved", hook="h", kind="recent news")], "A", CFG),
        rd.readiness(lead(), [email("sent", hook="h", kind="recent news")], "A", CFG),
    ]
    assert dict(rd.funnel(rows)) == {
        "Scored": 6, "Worth contacting": 5, "Email drafted": 5, "Has a hook": 4, "Has a named contact": 4,
        "Ready to review": 1, "Approved or sent": 2, "Sent": 1}


@pytest.mark.parametrize("emails, contact, text", [
    ([], "", "Draft the first email"),
    ([email(hook="h", kind="recent news")], "Priya Rao", "Review and approve"),
    ([email(hook="h", kind="recent news")], "", "Add a contact"),
    ([email()], "Priya Rao", "Add a hook"),
    ([email()], "", "Add a contact and a hook"),
    ([email("approved", hook="h", kind="recent news")], "Priya Rao", "Send it, then mark it sent"),
    ([email("stopped")], "", "Sequence stopped"),
])
def test_todo(emails, contact, text):
    assert rd.todo(rd.readiness(lead(), emails, contact, CFG)) == text


def test_todo_for_a_verdict_that_gets_no_email():
    assert rd.todo(rd.readiness(lead("disqualify"), [], "", CFG)) == "No email for this verdict"


def test_queues():
    needs_contact = rd.readiness(lead(), [email(hook="h", kind="recent news")], "", CFG)
    needs_hook = rd.readiness(lead(), [email()], "A", CFG)
    ready = rd.readiness(lead(), [email(hook="h", kind="recent news")], "A", CFG)
    sent = rd.readiness(lead(), [email("sent", hook="h", kind="recent news")], "A", CFG)
    assert [q for q in rd.QUEUES if rd.in_queue(needs_contact, q)] == ["All", "Needs a contact"]
    assert [q for q in rd.QUEUES if rd.in_queue(needs_hook, q)] == ["All", "Needs a hook"]
    assert [q for q in rd.QUEUES if rd.in_queue(ready, q)] == ["All", "Ready to review"]
    assert [q for q in rd.QUEUES if rd.in_queue(sent, q)] == ["All", "Sent"]
