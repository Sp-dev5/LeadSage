"""Contacts, cited email drafts, follow-up timing and stages. No model, no network."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent import contacts, hooks, outreach, store
from agent.fetch import Page
from agent.llm import Written
from agent.score_lead import Company, LeadScore, SalesRole
from agent.store import Email
from tests.test_scoring import make_icp

CFG = outreach.OutreachConfig(sender_name="Sam", sender_company="LeadSage", draft_verdicts=["qualify", "review"],
                              follow_up_days=[3, 7, 14], max_words=120, contact_pages=["/about", "/team"],
                              news_pages=["/news", "/blog"], news_max_age_days=90, startup_max_headcount=200,
                              startup_contact_titles=["ceo", "chief executive officer", "founder", "co-founder"],
                              closest_contact_titles=["chief operating officer", "ceo"],
                              sender_address="1 Test Street, Pune", opt_out_line='Reply "no thanks" to stop.',
                              avoid_phrases=["brief conversation", "quick chat"], examples=[])
ROLE = SalesRole(title="Account Executive, APAC", match="exact", source="https://jobs.example/acme",
                 fetched_at="2026-10-23T00:00:00+00:00")
COMPANY = Company(name="Acme", industry="B2B SaaS", size="120", location="Pune, India",
                  description="Acme builds payroll software for mid-size firms in India.", sales_roles=[ROLE])
SCORE = LeadScore(fit=80, verdict="qualify", criteria=[], disqualifier_hit=False, disqualifier_reason="", summary="s")
LEAD = store.StoredLead(name="Acme", url="https://acme.example", text_source="typed", company=COMPANY,
                        score=SCORE, scored_at="2026-10-24T00:00:00+00:00", stage="new")
PRIYA = contacts.Contact(name="Priya Rao", title="VP Sales", matches_buyer="yes",
                         source="https://acme.example/team", fetched_at="2026-10-24T00:00:00+00:00")


def test_shipped_outreach_config_loads():
    outreach.load_outreach()


@pytest.mark.parametrize("change", [{"follow_up_days": [7, 3]}, {"follow_up_days": [0, 3]},
                                    {"draft_verdicts": []}, {"sender_name": ""}])
def test_bad_outreach_config_is_rejected(tmp_path, change):
    path = tmp_path / "outreach.json"
    path.write_text(json.dumps(CFG.model_dump() | change), encoding="utf-8")
    with pytest.raises(ValueError):
        outreach.load_outreach(path)


def test_missing_outreach_setting_is_rejected(tmp_path):
    raw = CFG.model_dump()
    del raw["max_words"]
    path = tmp_path / "outreach.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="max_words"):
        outreach.load_outreach(path)


def claim(quote, sentence="A sentence."):
    return outreach.ClaimOut(quote=quote, sentence=sentence)


def test_claim_quoting_the_company_is_kept_with_its_source(tmp_path):
    kept, rejected = outreach.check_claims([claim("payroll software for mid-size firms")], LEAD, tmp_path)
    assert rejected == []
    assert kept[0].source == "https://acme.example (typed description)"


def test_claim_quoting_a_job_title_cites_the_job_board(tmp_path):
    kept, _ = outreach.check_claims([claim("Account Executive, APAC")], LEAD, tmp_path)
    assert (kept[0].source, kept[0].fetched_at) == (ROLE.source, ROLE.fetched_at)


@pytest.mark.parametrize("quote", ["Acme is growing fast", "Sales reps spend hours researching", ""])
def test_claim_without_a_real_quote_is_rejected(tmp_path, quote):
    kept, rejected = outreach.check_claims([claim(quote)], LEAD, tmp_path)
    assert kept == [] and len(rejected) == 1


SOURCE = "Acme builds payroll software for mid-size firms in India. Account Executive, APAC. Series A."


@pytest.mark.parametrize("sentence, missing", [
    ("You're hiring AEs in Bangalore.", ["AEs", "Bangalore"]),
    ("You're hiring Account Executives for APAC.", []),
    ("Since you build payroll software, reps matter.", []),
    ("Congrats on raising a Series B of 20 million.", ["B", "20"]),
    ("SDRs at Acme in India.", ["SDRs"]),
    ("Your team in India keeps growing.", []),
])
def test_unsupported_specifics(sentence, missing):
    assert outreach.unsupported_specifics(sentence, SOURCE, outreach.PLAIN_CAPS) == missing


def test_claim_with_a_real_quote_but_an_added_place_is_cut(tmp_path):
    kept, rejected = outreach.check_claims(
        [claim("Account Executive, APAC", "You're hiring Account Executives in Bangalore.")], LEAD, tmp_path)
    assert kept == [] and "Bangalore" in rejected[0]


def test_short_form_job_title_is_cut(tmp_path):
    kept, _ = outreach.check_claims([claim("Account Executive, APAC", "You're hiring AEs for APAC.")], LEAD, tmp_path)
    assert kept == []


def test_fetched_lead_cites_the_cached_page(tmp_path):
    page = Page(url="https://acme.example/", text="x", fetched_at="2026-10-20T00:00:00+00:00", ok=True)
    (tmp_path / "acme.json").write_text(page.model_dump_json(), encoding="utf-8")
    fetched = LEAD.model_copy(update={"text_source": "fetched"})
    kept, _ = outreach.check_claims([claim("payroll software")], fetched, tmp_path)
    assert (kept[0].source, kept[0].fetched_at) == (page.url, page.fetched_at)


@pytest.fixture
def model_writes(monkeypatch):
    """Fake drafting call: returns whatever EmailOut the test sets."""
    out = {}
    monkeypatch.setattr(outreach, "generate_writing",
                        lambda system, user_prompt, schema: Written(out["email"], "fake-model", out.get("failed", "")))
    return out


def email_out(*claims):
    return outreach.EmailOut(subject="Payroll outreach", claims=list(claims), pitch="We research leads for you.",
                             ask="Worth a short call?")


def test_only_checked_claims_reach_the_email(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("Account Executive, APAC", "You are hiring an Account Executive for APAC."),
                                      claim("payroll software", "You build payroll software."),
                                      claim("raised a Series B", "Congrats on the Series B."))
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert email.body.startswith("Hi Priya,")
    assert "You build payroll software." in email.body
    assert "Series B" not in email.body
    assert email.body.endswith("Sam\nLeadSage\n1 Test Street, Pune\n\nReply \"no thanks\" to stop.")
    assert (len(email.citations), len(email.rejected), email.flags) == (2, 1, [])


def test_email_with_nothing_checked_or_no_contact_is_flagged(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("invented", "You are invented."))
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, None, [], tmp_path)
    assert email.body.startswith("Hi there,")
    assert any("not personal yet" in f for f in email.flags)
    assert any("startup with no one named" in f for f in email.flags)
    big = LEAD.model_copy(update={"company": COMPANY.model_copy(update={"size": "450"})})
    assert any("no named contact" in f for f in outreach.draft_email(big, 0, make_icp(), CFG, None, [], tmp_path).flags)


def test_avoided_phrase_is_flagged_after_one_retry(model_writes, tmp_path):
    model_writes["email"] = email_out().model_copy(update={"ask": "Open to a brief conversation?"})
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert "uses phrases you avoid: brief conversation" in email.flags


def test_avoided_phrase_gets_one_rewrite(monkeypatch, tmp_path):
    drafts = [email_out().model_copy(update={"ask": "Open to a quick chat?"}),
              email_out().model_copy(update={"ask": "Is outbound research on your list this quarter?"})]
    prompts = []

    def fake(system, user_prompt, schema):
        prompts.append(user_prompt)
        return Written(drafts[len(prompts) - 1], "fake-model", "")

    monkeypatch.setattr(outreach, "generate_writing", fake)
    email = outreach.draft_email(NO_ROLES, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert len(prompts) == 2 and "It used quick chat" in prompts[1]
    assert "this quarter" in email.body and not any("avoid" in f for f in email.flags)


def test_pitch_naming_something_from_nowhere_is_flagged(model_writes, tmp_path):
    out = email_out(claim("payroll software", "You build payroll software."))
    model_writes["email"] = out.model_copy(update={"pitch": "Teams like Razorpay cut research time by 40%."})
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert any("Razorpay" in f and "40%" in f for f in email.flags)


def test_follow_up_replies_in_the_same_thread(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "Still thinking of your payroll software."))
    first = Email(step=0, subject="Payroll outreach", body="b", status="sent", drafted_at="x",
                  sent_at="2026-10-24T00:00:00+00:00")
    email = outreach.draft_email(LEAD, 1, make_icp(), CFG, PRIYA, [first], tmp_path)
    assert email.subject == "Re: Payroll outreach"


def test_follow_up_repeating_an_earlier_line_is_flagged(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "Payroll again."))
    first = Email(step=0, subject="s", body="Hi.\n\nWe research leads for you.\n\nAsk.", status="sent",
                  drafted_at="x", sent_at="2026-10-24T00:00:00+00:00")
    email = outreach.draft_email(LEAD, 1, make_icp(), CFG, PRIYA, [first], tmp_path)
    assert "repeats a line from an earlier email" in email.flags


def test_fancy_characters_are_made_plain_in_the_email(model_writes, tmp_path):
    model_writes["email"] = outreach.EmailOut(
        subject="Acme’s payroll", pitch="We handle research — and timing.", ask="Is AI‑driven outreach useful?",
        claims=[claim("payroll software for mid‑size firms", "You build payroll software for mid‑size firms.")])
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert "Acme's payroll" == email.subject
    assert "research, and timing" in email.body and "AI-driven" in email.body and "mid-size" in email.body
    assert not any(ord(ch) > 127 for ch in email.body)
    assert email.citations and email.rejected == []


def test_when_every_claim_is_cut_the_model_gets_one_more_try(monkeypatch, tmp_path):
    drafts = [email_out(claim("Acme builds payroll and HR software", "You build payroll software.")),
              email_out(claim("payroll software for mid-size firms", "You build payroll software for mid-size firms."))]
    prompts = []

    def fake(system, user_prompt, schema):
        prompts.append(user_prompt)
        return Written(drafts[len(prompts) - 1], "fake-model", "")

    monkeypatch.setattr(outreach, "generate_writing", fake)
    email = outreach.draft_email(NO_ROLES, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert len(prompts) == 2 and "Every claim was cut" in prompts[1]
    assert len(email.citations) == 1 and "not personal yet" not in " ".join(email.flags)


def test_second_try_that_also_fails_keeps_the_first_rejections(monkeypatch, tmp_path):
    bad = email_out(claim("a quote that is not there", "You do a thing."))
    monkeypatch.setattr(outreach, "generate_writing", lambda system, user_prompt, schema: Written(bad, "fake-model", ""))
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert email.citations == [] and len(email.rejected) == 1
    assert any("not personal yet" in f for f in email.flags)


def test_examples_go_into_the_prompt_for_first_emails_and_follow_ups(monkeypatch, tmp_path):
    cfg = CFG.model_copy(update={"examples": [outreach.Example(situation="first email", subject="a subject",
                                                               body="Example body {with braces}.")]})
    systems = []

    def fake(system, user_prompt, schema):
        systems.append(system)
        return Written(email_out(), "fake-model", "")

    monkeypatch.setattr(outreach, "generate_writing", fake)
    for step in (0, 1):
        outreach.draft_email(LEAD, step, make_icp(), cfg, PRIYA, [], tmp_path)
    assert all("Example 1 (first email)" in s and "Example body {with braces}." in s for s in systems)
    outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert "EXAMPLES" not in systems[-1]


def test_prompt_never_carries_the_verdict_or_the_scorers_summary(monkeypatch, tmp_path):
    lead = LEAD.model_copy(update={"score": SCORE.model_copy(update={"summary": "Excellent fit, no direct evidence"})})
    prompt = outreach._prompt(lead, make_icp(), PRIYA, [])
    assert "Excellent fit" not in prompt and "qualify" not in prompt.lower() and "80" not in prompt


def test_shipped_examples_stay_clear_of_the_banned_phrases():
    cfg = outreach.load_outreach()
    text = " ".join(f"{e.subject} {e.body}" for e in cfg.examples).lower()
    assert cfg.examples and not [p for p in cfg.avoid_phrases if p.lower() in text]
    assert all(len(e.body.split()) <= cfg.max_words for e in cfg.examples)


NO_ROLES = LEAD.model_copy(update={"company": COMPANY.model_copy(update={"sales_roles": []})})


NEWS = hooks.NewsItem(headline="Acme launches payroll for Singapore", date_text="Oct 20, 2026", date="2026-10-20",
                      kind="launch", source="https://acme.example/news", fetched_at="2026-10-24T00:00:00+00:00")


def test_open_sales_roles_are_the_hook():
    hook = outreach.hook_for(LEAD)
    assert (hook.kind, hook.text) == ("open sales roles", "they have open sales roles: Account Executive, APAC")
    assert outreach.hook_for(NO_ROLES) == ("", "")


def test_recent_news_is_the_hook_when_there_are_no_sales_roles():
    hook = outreach.hook_for(NO_ROLES, NEWS)
    assert (hook.kind, hook.text) == ("recent news", "Oct 20, 2026, on their own site: Acme launches payroll for Singapore")


def test_sales_roles_beat_news():
    assert outreach.hook_for(LEAD, NEWS).kind == "open sales roles"


def test_prompt_shows_the_news_and_hooks_on_it():
    prompt = outreach._prompt(NO_ROLES, make_icp(), PRIYA, [], NEWS)
    assert "RECENT NEWS (from their own site)\n- Oct 20, 2026: Acme launches payroll for Singapore" in prompt
    assert "HOOK\n- Oct 20, 2026, on their own site: Acme launches payroll for Singapore" in prompt
    assert "RECENT NEWS" not in outreach._prompt(NO_ROLES, make_icp(), PRIYA, [])


def test_claim_quoting_the_news_is_cited_to_the_news_page(tmp_path):
    kept, rejected = outreach.check_claims(
        [claim("Acme launches payroll for Singapore", "You launched payroll for Singapore on 20 October.")],
        NO_ROLES, tmp_path, extra=outreach._news_extra(NEWS))
    assert rejected == []
    assert (kept[0].source, kept[0].fetched_at) == (NEWS.source, NEWS.fetched_at)


def test_claim_adding_a_detail_the_news_lacks_is_cut(tmp_path):
    kept, rejected = outreach.check_claims(
        [claim("Acme launches payroll for Singapore", "You launched payroll for Singapore and Vietnam.")],
        NO_ROLES, tmp_path, extra=outreach._news_extra(NEWS))
    assert kept == [] and "Vietnam" in rejected[0]


def test_email_opening_with_the_news_records_the_hook(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("Acme launches payroll for Singapore", "You launched payroll in Singapore."))
    email = outreach.draft_email(NO_ROLES, 0, make_icp(), CFG, PRIYA, [], tmp_path, news=NEWS)
    assert email.hook.endswith("Acme launches payroll for Singapore")
    assert not any("hook" in f for f in email.flags)


def test_a_draft_that_ignores_the_hook_gets_one_more_try(monkeypatch, tmp_path):
    drafts = [email_out(claim("payroll software", "You build payroll software.")),
              email_out(claim("Acme launches payroll for Singapore", "You launched payroll in Singapore."))]
    prompts = []

    def fake(system, user_prompt, schema):
        prompts.append(user_prompt)
        return Written(drafts[len(prompts) - 1], "fake-model", "")

    monkeypatch.setattr(outreach, "generate_writing", fake)
    email = outreach.draft_email(NO_ROLES, 0, make_icp(), CFG, PRIYA, [], tmp_path, news=NEWS)
    assert len(prompts) == 2 and "The first claim must be about the HOOK" in prompts[1] and "RECENT NEWS" in prompts[1]
    assert email.hook.endswith("Acme launches payroll for Singapore") and not any("hook" in f for f in email.flags)


def test_a_second_draft_that_still_ignores_the_hook_keeps_the_first_and_flags_it(monkeypatch, tmp_path):
    ignoring = email_out(claim("payroll software", "You build payroll software."))
    monkeypatch.setattr(outreach, "generate_writing", lambda system, user_prompt, schema: Written(ignoring, "m", ""))
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert email.hook == "" and "a hook was available (open sales roles) but the email does not use it" in email.flags
    assert len(email.citations) == 1


def test_follow_ups_do_not_chase_the_hook(monkeypatch, tmp_path):
    prompts = []

    def fake(system, user_prompt, schema):
        prompts.append(user_prompt)
        return Written(email_out(claim("payroll software", "You build payroll software.")), "m", "")

    monkeypatch.setattr(outreach, "generate_writing", fake)
    outreach.draft_email(NO_ROLES, 1, make_icp(), CFG, PRIYA, [], tmp_path, news=NEWS)
    assert len(prompts) == 1


def test_email_that_ignores_the_news_is_flagged(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    email = outreach.draft_email(NO_ROLES, 0, make_icp(), CFG, PRIYA, [], tmp_path, news=NEWS)
    assert email.hook == "" and "a hook was available (recent news) but the email does not use it" in email.flags


def test_prompt_tells_the_model_to_open_with_the_hook():
    assert "HOOK\n- they have open sales roles" in outreach._prompt(LEAD, make_icp(), PRIYA, [])
    assert "HOOK" not in outreach._prompt(NO_ROLES, make_icp(), PRIYA, [])


def test_email_that_opens_with_the_hook_records_it(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("Account Executive, APAC", "You are hiring an Account Executive for APAC."))
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert email.hook == "they have open sales roles: Account Executive, APAC"
    assert not any("hook" in f for f in email.flags)


def test_email_that_ignores_an_available_hook_is_flagged(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert email.hook == "" and "a hook was available (open sales roles) but the email does not use it" in email.flags


def test_company_with_no_timely_signal_is_flagged_as_having_no_hook(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    email = outreach.draft_email(NO_ROLES, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert email.hook == "" and any(f.startswith("no hook:") for f in email.flags)


def test_follow_ups_are_not_flagged_for_a_hook(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    email = outreach.draft_email(NO_ROLES, 1, make_icp(), CFG, PRIYA, [], tmp_path)
    assert not any("hook" in f for f in email.flags)


def test_signature_and_opt_out_line_do_not_count_towards_the_word_limit(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    tight = CFG.model_copy(update={"max_words": 25})
    email = outreach.draft_email(LEAD, 0, make_icp(), tight, PRIYA, [], tmp_path)
    assert len(email.body.split()) > 25 and not any("word limit" in f for f in email.flags)


def test_every_email_ends_with_the_signature_address_and_opt_out_line(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    for step in (0, 2):
        body = outreach.draft_email(LEAD, step, make_icp(), CFG, PRIYA, [], tmp_path).body
        assert "Sam\nLeadSage\n1 Test Street, Pune" in body and body.endswith('Reply "no thanks" to stop.')


def test_sending_problems_are_named():
    ok = "Hi.\n\nSam\n\nReply \"no thanks\" to stop."
    assert outreach.sending_problems(ok, CFG) == []
    assert outreach.sending_problems("Hi.", CFG) == ["the opt-out line is missing: put it back at the end"]
    unset = CFG.model_copy(update={"sender_name": "Your name", "sender_address": " "})
    problems = outreach.sending_problems(ok, unset)
    assert len(problems) == 2 and "your name" in problems[0] and "business address" in problems[1]


def test_closest_match_is_flagged_on_the_draft(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path, contact_how="closest")
    assert any("closest match on their site, not a sales leader" in f for f in email.flags)
    assert not any("closest" in f for f in outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path).flags)


def test_pitch_using_words_from_the_news_or_the_contacts_title_is_not_flagged(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("Acme launches payroll for Singapore", "You launched payroll in Singapore.")
                                      ).model_copy(update={"pitch": "Teams like Sophos in Asia time outreach better.",
                                                           "ask": "Is that useful?"})
    contact = contacts.Contact(name="Priya Rao", title="GM, Sales - Greater Asia", matches_buyer="yes",
                               source="s", fetched_at="t")
    news = NEWS.model_copy(update={"headline": "Acme launches payroll with Sophos for Singapore"})
    flags = outreach.draft_email(NO_ROLES, 0, make_icp(), CFG, contact, [], tmp_path, news=news).flags
    assert not any("found in neither" in f for f in flags)


def test_email_records_which_model_wrote_it(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    assert outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path).model == "fake-model"


def test_cloud_failure_is_flagged_on_the_draft(model_writes, tmp_path):
    model_writes["email"] = email_out(claim("payroll software", "You build payroll software."))
    model_writes["failed"] = "RuntimeError: HTTP 500"
    email = outreach.draft_email(LEAD, 0, make_icp(), CFG, PRIYA, [], tmp_path)
    assert any("cloud model failed (RuntimeError: HTTP 500)" in f for f in email.flags)


def test_last_follow_up_is_told_it_is_the_last(monkeypatch, tmp_path):
    systems = []

    def fake(system, user_prompt, schema):
        systems.append(system)
        return Written(email_out(), "fake-model", "")

    monkeypatch.setattr(outreach, "generate_writing", fake)
    for step in (2, 3):
        outreach.draft_email(LEAD, step, make_icp(), CFG, PRIYA, [], tmp_path)
    assert [outreach.LAST_NOTE in s for s in systems] == [False, True]


def test_no_step_past_the_last_follow_up(model_writes, tmp_path):
    with pytest.raises(ValueError, match="only 3 follow-ups"):
        outreach.draft_email(LEAD, 4, make_icp(), CFG, PRIYA, [], tmp_path)


T0 = datetime(2026, 10, 24, tzinfo=timezone.utc)


def mail(step, status, sent_at=""):
    return Email(step=step, subject="s", body="b", status=status, drafted_at="x", sent_at=sent_at)


@pytest.mark.parametrize("emails, now, action, step", [
    ([], T0, "draft", 0),
    ([mail(0, "draft")], T0, "approve", 0),
    ([mail(0, "approved")], T0, "send", 0),
    ([mail(0, "sent", T0.isoformat())], T0 + timedelta(days=2), "wait", 1),
    ([mail(0, "sent", T0.isoformat())], T0 + timedelta(days=3), "draft", 1),
    ([mail(0, "sent", T0.isoformat()), mail(1, "sent", T0.isoformat())], T0 + timedelta(days=5), "wait", 2),
    ([mail(0, "sent", T0.isoformat()), mail(1, "draft")], T0 + timedelta(days=1), "approve", 1),
    ([mail(s, "sent", T0.isoformat()) for s in range(4)], T0 + timedelta(days=30), "done", None),
])
def test_next_step(emails, now, action, step):
    nxt = outreach.next_step("contacted", emails, CFG, now)
    assert (nxt.action, nxt.step) == (action, step)


def test_follow_ups_fall_due_after_the_first_send():
    due = outreach.due_dates([mail(0, "sent", T0.isoformat())], CFG)
    assert due == {1: T0 + timedelta(days=3), 2: T0 + timedelta(days=7), 3: T0 + timedelta(days=14)}
    assert outreach.due_dates([mail(0, "approved")], CFG) == {}


def test_a_stopped_lead_has_no_next_step():
    assert outreach.next_step("replied", [], CFG, T0).action == "stopped"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "leads.db"
    store.save(COMPANY, SCORE, url=LEAD.url, text_source="typed", path=path)
    return path


def stage(db):
    return store.load("Acme", db).stage


def test_stage_follows_the_first_email(db):
    store.save_email("Acme", mail(0, "draft"), db)
    assert stage(db) == "drafted"
    store.approve_email("Acme", 0, db)
    assert stage(db) == "approved"
    store.mark_sent("Acme", 0, db)
    assert stage(db) == "contacted"
    store.save_email("Acme", mail(1, "draft"), db)
    assert stage(db) == "contacted"


def test_only_an_approved_email_can_be_sent(db):
    store.save_email("Acme", mail(0, "draft"), db)
    with pytest.raises(ValueError, match="not approved"):
        store.mark_sent("Acme", 0, db)


def test_follow_up_cannot_go_before_the_email_before_it(db):
    store.save_email("Acme", mail(0, "draft"), db)
    store.save_email("Acme", mail(1, "draft"), db)
    store.approve_email("Acme", 1, db)
    with pytest.raises(ValueError, match="email 0 has not been sent"):
        store.mark_sent("Acme", 1, db)


def test_a_sent_email_is_never_replaced(db):
    store.save_email("Acme", mail(0, "draft"), db)
    store.approve_email("Acme", 0, db)
    store.mark_sent("Acme", 0, db)
    with pytest.raises(ValueError, match="already sent"):
        store.save_email("Acme", mail(0, "draft"), db)


def test_editing_needs_approval_again(db):
    store.save_email("Acme", mail(0, "draft"), db)
    store.approve_email("Acme", 0, db)
    store.edit_email("Acme", 0, "New subject", "New body", db)
    [email] = store.load_emails("Acme", db)
    assert (email.status, email.subject, email.edited) == ("draft", "New subject", True)


def test_reply_stops_the_sequence(db):
    store.save_email("Acme", mail(0, "draft"), db)
    store.approve_email("Acme", 0, db)
    store.mark_sent("Acme", 0, db)
    store.save_email("Acme", mail(1, "draft"), db)
    store.set_stage("Acme", "replied", db)
    assert [e.status for e in store.load_emails("Acme", db)] == ["sent", "stopped"]
    with pytest.raises(ValueError, match="sequence stopped"):
        store.save_email("Acme", mail(2, "draft"), db)


def test_stage_never_moves_back_on_its_own(db):
    store.set_stage("Acme", "contacted", db)
    store.save_email("Acme", mail(0, "draft"), db)
    assert stage(db) == "contacted"


def test_do_not_contact_stops_the_lead_and_cannot_be_undone_by_a_stage_change(db):
    store.save_email("Acme", mail(0, "draft"), db)
    store.suppress("Acme", "asked not to be contacted", db)
    assert stage(db) == "lost" and [e.status for e in store.load_emails("Acme", db)] == ["stopped"]
    with pytest.raises(ValueError, match="do-not-contact list"):
        store.set_stage("Acme", "new", db)
    assert store.suppressed(db)["Acme"][0] == "asked not to be contacted"
    store.unsuppress("Acme", db)
    store.set_stage("Acme", "new", db)
    assert stage(db) == "new"


def test_scoring_a_suppressed_company_again_keeps_it_lost(db):
    store.suppress("Acme", "", db)
    store.save(COMPANY, SCORE, url="", text_source="fetched", path=db)
    assert stage(db) == "lost"


def test_a_company_can_be_suppressed_before_it_is_scored(tmp_path):
    path = tmp_path / "leads.db"
    store.suppress("Newco", "unsubscribed", path)
    store.save(COMPANY.model_copy(update={"name": "Newco"}), SCORE, url="", text_source="typed", path=path)
    assert store.load("Newco", path).stage == "lost"


def test_deleting_a_lead_removes_its_rows_and_cached_pages_but_keeps_the_do_not_contact_list(db, tmp_path):
    store.save_email("Acme", mail(0, "draft"), db)
    store.save_contacts("Acme", staff(("Zed Khan", "CEO")), db)
    store.save_news("Acme", hooks.NewsSearch(searched_at="t"), db)
    store.suppress("Acme", "asked", db)
    store.save(COMPANY.model_copy(update={"name": "Acme Labs"}), SCORE, url="", text_source="typed", path=db)
    pages, logo_dir = tmp_path / "pages", tmp_path / "logos"
    pages.mkdir()
    logo_dir.mkdir()
    for name in ("acme.json", "acme-jobs.json", "acme-about-full.json", "acme-news-blog-full.json",
                 "acme-company-about-us-full.json", "acme-news-newsroom-full.json", "acme-careers-html.json",
                 "acme-labs.json", "acme-labs-about-full.json", "acmecorp.json", "other.json"):
        (pages / name).write_text("{}", encoding="utf-8")
    for name in ("acme.png", "acme-labs.png"):
        (logo_dir / name).write_bytes(b"x")
    removed = outreach.forget_lead(store.load("Acme", db), db, pages, logo_dir)
    assert removed == 7
    assert sorted(p.name for p in pages.iterdir()) == [
        "acme-labs-about-full.json", "acme-labs.json", "acmecorp.json", "other.json"]
    assert [p.name for p in logo_dir.iterdir()] == ["acme-labs.png"]
    with pytest.raises(KeyError):
        store.load("Acme", db)
    assert (store.load_emails("Acme", db), store.load_contacts("Acme", db), store.load_news("Acme", db)) == ([], None, None)
    assert "Acme" in store.suppressed(db)


def test_a_stopped_lead_is_not_drafted_for(db, monkeypatch):
    store.suppress("Acme", "", db)
    monkeypatch.setattr(outreach, "contact_for", lambda *a, **k: pytest.fail("searched"))
    with pytest.raises(ValueError, match="sequence stopped"):
        outreach.draft_step(store.load("Acme", db), 0, make_icp(), CFG, db)


def test_unknown_stage_is_rejected(db):
    with pytest.raises(ValueError, match="unknown stage"):
        store.set_stage("Acme", "won", db)


def test_contacts_round_trip(db):
    search = contacts.ContactSearch(contact=PRIYA, people=[PRIYA], pages_tried=["https://acme.example/team"])
    store.save_contacts("Acme", search, db)
    assert store.load_contacts("Acme", db) == search
    assert store.load_contacts("Nobody", db) is None


TEAM_PAGE = Page(url="https://acme.example/team", fetched_at="2026-10-24T00:00:00+00:00", ok=True,
                 text="Our team. Priya Rao, VP Sales. Arjun Mehta, CTO.")


@pytest.fixture
def model_sees(monkeypatch):
    """Fake people call: returns the people the test sets."""
    seen = {}
    monkeypatch.setattr(contacts, "generate_structured",
                        lambda system, user_prompt, schema, **kw: contacts.PeopleSeen(people=seen["people"]))
    return seen


def person(name, title, matches="no"):
    return contacts.PersonSeen(name=name, title=title, matches_buyer=matches)


def test_person_not_on_the_page_is_rejected(model_sees):
    model_sees["people"] = [person("Priya Rao", "VP Sales", "yes"), person("John Doe", "CEO"),
                            person("Arjun Mehta", "Chief Revenue Officer")]
    kept, rejected = contacts.people_on_page(TEAM_PAGE, make_icp())
    assert [p.name for p in kept] == ["Priya Rao"]
    assert len(rejected) == 2


def test_find_contact_skips_dead_pages_and_stops_at_the_buyer(model_sees, tmp_path):
    model_sees["people"] = [person("Priya Rao", "VP Sales", "yes"), person("Arjun Mehta", "CTO")]
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("/about"):
            return Page(url=url, fetched_at="t", ok=False, error="HTTP 404")
        return TEAM_PAGE

    search = contacts.find_contact("Acme", "https://acme.example", make_icp(), ["/about", "/team", "/leadership"],
                                   tmp_path, fetch)
    assert sorted(fetched) == ["https://acme.example/about", "https://acme.example/leadership",
                               "https://acme.example/team"]
    assert search.contact.name == "Priya Rao"
    assert len(search.pages_tried) == 2
    assert [p.name for p in search.people] == ["Priya Rao", "Arjun Mehta"]
    assert "HTTP 404" in search.pages_tried[0]


TITLES = CFG.startup_contact_titles


def staff(*people):
    return contacts.ContactSearch(people=[contacts.Contact(name=n, title=t, matches_buyer="no", source="s",
                                                           fetched_at="t") for n, t in people])


def test_startup_with_no_buyer_gets_the_chief_executive():
    search = staff(("Madhav Bhagat", "CTO and Co-Founder"), ("Shashank Bijapur", "CEO and Co-Founder"))
    chosen = contacts.startup_fallback(search, 120, 200, TITLES)
    assert (chosen.contact.name, chosen.chosen_by) == ("Shashank Bijapur", "startup_ceo")


def test_startup_without_a_chief_executive_gets_a_founder():
    chosen = contacts.startup_fallback(staff(("Ana Ruiz", "Head of Design"), ("Madhav Bhagat", "CTO and Co-Founder")),
                                       120, 200, TITLES)
    assert chosen.contact.name == "Madhav Bhagat"


@pytest.mark.parametrize("headcount", [None, 201, 5000])
def test_no_fallback_above_the_startup_size_or_with_unknown_size(headcount):
    assert contacts.startup_fallback(staff(("Shashank Bijapur", "CEO")), headcount, 200, TITLES).contact is None


def test_fallback_never_replaces_a_chosen_contact():
    search = staff(("Shashank Bijapur", "CEO")).model_copy(update={"contact": PRIYA, "chosen_by": "rep"})
    assert contacts.startup_fallback(search, 50, 200, TITLES).contact == PRIYA


def test_closest_match_takes_the_first_title_on_the_list():
    search = staff(("Ana Ruiz", "Head of Design"), ("Zed Khan", "CEO"), ("Lee Wong", "Chief Operating Officer"))
    chosen = contacts.closest_match(search, ["chief operating officer", "ceo"])
    assert (chosen.contact.name, chosen.chosen_by) == ("Lee Wong", "closest")


def test_closest_match_finds_nobody_when_no_title_fits_and_never_replaces_a_contact():
    assert contacts.closest_match(staff(("Ana Ruiz", "Head of Design")), ["ceo"]).contact is None
    kept = staff(("Zed Khan", "CEO")).model_copy(update={"contact": PRIYA, "chosen_by": "rep"})
    assert contacts.closest_match(kept, ["ceo"]).contact == PRIYA


def test_a_bigger_company_gets_the_closest_match_not_the_startup_fallback(db):
    big = store.load("Acme", db).model_copy(update={"company": COMPANY.model_copy(update={"size": "450"})})
    store.save_contacts("Acme", staff(("Zed Khan", "CEO"), ("Lee Wong", "Chief Operating Officer")), db)
    assert outreach.saved_contact(big, CFG, db).name == "Lee Wong"
    assert outreach.contact_for(big, make_icp(), CFG, db).chosen_by == "closest"


def test_saved_search_gets_the_fallback_and_keeps_it(db):
    store.save_contacts("Acme", staff(("Shashank Bijapur", "CEO")), db)
    lead = store.load("Acme", db)
    assert outreach.saved_contact(lead, CFG, db).name == "Shashank Bijapur"
    outreach.contact_for(lead, make_icp(), CFG, db)
    assert store.load_contacts("Acme", db).chosen_by == "startup_ceo"


def test_no_buyer_on_the_site_leaves_the_choice_to_the_rep(model_sees, tmp_path):
    model_sees["people"] = [person("Arjun Mehta", "CTO")]
    search = contacts.find_contact("Acme", "https://acme.example", make_icp(), ["/team"], tmp_path,
                                   lambda url: TEAM_PAGE)
    assert search.contact is None and [p.name for p in search.people] == ["Arjun Mehta"]


def test_homepage_links_come_before_the_guessed_paths_without_repeats(monkeypatch):
    monkeypatch.setattr(outreach, "site_links", lambda url, words: ["/company/team", "/about"])
    assert outreach.with_site_links("https://acme.example", ("team",), ["/about/", "/team"]) == [
        "/company/team", "/about", "/team"]
    assert outreach.with_site_links("", ("team",), ["/team"]) == ["/team"]


def test_a_customer_quoted_on_the_site_is_not_a_contact():
    assert contacts.works_elsewhere("VP of Enterprise Data and Analytics @ Workday", "Atlan")
    assert contacts.works_elsewhere("CDO at Mastercard", "Atlan")
    assert not contacts.works_elsewhere("Head of Sales at Atlan", "Atlan")
    assert not contacts.works_elsewhere("Chief Revenue Officer", "Atlan")


def test_the_ceos_assistant_is_not_the_ceo():
    def person(title):
        return contacts.Contact(name="X", title=title, matches_buyer="no", source="s", fetched_at="t")
    assert not contacts.holds_title(person("Executive Assistant to the CEO"), "ceo")
    assert not contacts.holds_title(person("Office of the CEO"), "ceo")
    assert contacts.holds_title(person("CEO and Co-founder"), "ceo")


def test_saved_search_applies_the_fallbacks_without_saving_them(db):
    ceo = contacts.Contact(name="Zed Khan", title="Chief Executive Officer", matches_buyer="no", source="https://acme.example/team",
                  fetched_at="2026-10-24T00:00:00+00:00")
    store.save_contacts("Acme", contacts.ContactSearch(people=[ceo], searched_at="t"), db)
    lead = store.load("Acme", db).model_copy(update={"company": COMPANY.model_copy(update={"size": "10 employees"})})
    shown = outreach.saved_search(lead, CFG, db)
    assert shown.contact == ceo and shown.chosen_by == "startup_ceo"
    assert store.load_contacts("Acme", db).contact is None


def test_saved_search_is_none_when_nothing_was_searched(db):
    assert outreach.saved_search(store.load("Acme", db), CFG, db) is None


MENU = "Products Pricing Resources Blog Customers Careers Contact Login " * 400


def test_a_short_page_goes_to_the_model_whole():
    assert contacts.leader_view("Priya Rao VP Sales") == "Priya Rao VP Sales"


def test_a_long_page_is_cut_to_the_stretches_around_job_titles():
    text = MENU + "Meet the team. Kedar Kulkarni Co-founder & CEO. Asha Rao Chief Revenue Officer. " + MENU
    view = contacts.leader_view(text)
    assert len(text) > contacts.VIEW_CHARS and len(view) <= contacts.VIEW_CHARS
    assert "Kedar Kulkarni Co-founder & CEO" in view and "Asha Rao Chief Revenue Officer" in view
    assert view.count("Pricing") < 40


def test_the_model_sees_the_stretches_but_names_are_checked_against_the_whole_page(monkeypatch):
    page_text = MENU + "Kedar Kulkarni Co-founder & CEO " + MENU
    seen = {}

    def fake(system, user_prompt, schema, **kw):
        seen["prompt"] = user_prompt
        return contacts.PeopleSeen(people=[person("Kedar Kulkarni", "Co-founder & CEO"), person("Ghost", "CEO")])

    monkeypatch.setattr(contacts, "generate_structured", fake)
    found, rejected = contacts.people_on_page(
        Page(url="https://acme.example/about", text=page_text, fetched_at="t", ok=True), make_icp(), "Acme")
    assert [p.name for p in found] == ["Kedar Kulkarni"] and len(rejected) == 1
    assert len(seen["prompt"]) < len(page_text) / 2


def test_a_page_saved_at_the_old_cut_is_read_again(tmp_path):
    from agent.fetch import FULL_MAX_CHARS
    path = tmp_path / "acme-about-full.json"
    path.write_text(Page(url="u", text="x" * FULL_MAX_CHARS, fetched_at="t", ok=True).model_dump_json(),
                    encoding="utf-8")
    calls = []

    def fetch(url):
        calls.append(url)
        return Page(url=url, text="Priya Rao VP Sales " * 600, fetched_at="t", ok=True)

    again = contacts._people_page("https://acme.example/about", path, fetch)
    assert calls and len(again.text) > FULL_MAX_CHARS
    calls.clear()
    contacts._people_page("https://acme.example/about", path, fetch)
    assert calls == []
