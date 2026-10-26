"""The web app's pages and actions against a temporary store. No model, no network."""

import re

import pytest
from fastapi.testclient import TestClient

from agent import contacts, logos, outreach, store
from agent.store import Citation, Email
from tests.test_outreach import CFG, COMPANY, SCORE
from web import app as web_app
from web import views


class PageClient(TestClient):
    """Posts like a browser on one of the app's pages: with the page's CSRF token."""

    def token(self) -> str:
        return re.search(r'name="csrf" content="([^"]+)"', self.get("/login").text).group(1)

    def post(self, url, *args, data=None, json=None, headers=None, csrf=True, **kwargs):
        if csrf and json is not None:
            headers = {**(headers or {}), "X-CSRF-Token": self.token()}
        elif csrf:
            data = {**(data or {}), "csrf": self.token()}
        return super().post(url, *args, data=data, json=json, headers=headers, **kwargs)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "leads.db"
    monkeypatch.setattr(views, "DB", db)
    monkeypatch.setattr(web_app, "load_outreach", lambda: CFG)
    monkeypatch.setattr(web_app, "PASSWORD", "")
    monkeypatch.setattr(web_app, "forget_lead", lambda lead, db: outreach.forget_lead(
        lead, db, tmp_path / "pages", tmp_path / "logos"))
    web_app.failed_logins.clear()
    return PageClient(web_app.app)


def seed(emails=True):
    store.save(COMPANY, SCORE, url="https://acme.example", text_source="typed", path=views.DB)
    if emails:
        cited = Citation(sentence="Acme builds payroll software.", quote="payroll software",
                         source="https://acme.example", fetched_at="2026-10-24T00:00:00+00:00")
        store.save_email("Acme", Email(step=0, subject="Hello", body="Hi there,\n\nAcme builds payroll software.\n\n"
                                       + CFG.opt_out_line, hook="they have open sales roles: AE",
                                       hook_kind="open sales roles", citations=[cited], rejected=["You won an award."],
                                       drafted_at="2026-10-24T00:00:00+00:00"), path=views.DB)


@pytest.mark.parametrize("path", ["/", "/leads", "/outreach", "/icp", "/leads.csv", "/emails.csv", "/login"])
def test_pages_open_with_an_empty_store(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/", "/leads?group=All&lead=Acme", "/outreach", "/outreach?lead=Acme",
                                  "/outreach?lead=Acme&step=1", "/leads?country=India&min_fit=50&hiring=1"])
def test_pages_open_with_a_lead(client, path):
    seed()
    assert client.get(path).status_code == 200


def test_outreach_splits_the_table_into_pages_of_ten(client):
    for i in range(12):
        store.save(COMPANY.model_copy(update={"name": f"Co {i:02}"}), SCORE, url="https://co.example",
                   text_source="typed", path=views.DB)
    first = client.get("/outreach").text
    assert "Page <b>1</b> of 2 · 1 to 10 of 12" in first
    assert "Page <b>2</b> of 2 · 11 to 12 of 12" in client.get("/outreach?page=2").text
    assert "Page <b>2</b> of 2" in client.get("/outreach?page=99").text


def test_home_counts_kept_and_cut_lines(client):
    seed()
    page = client.get("/").text
    assert "<b>1</b> lines about companies" in page and "<b>1</b> had no matching quote" in page


def test_the_email_marks_the_cited_sentence_with_a_footnote(client):
    seed()
    page = client.get("/outreach?lead=Acme").text
    assert '<span class="hl">Acme builds payroll software.</span><sup>1</sup>' in page
    assert "1 line cut" in page


def test_approve_then_mark_sent(client):
    seed()
    client.post("/outreach/act", data={"lead": "Acme", "do": "approve", "step": 0})
    assert store.load_emails("Acme", views.DB)[0].status == "approved"
    client.post("/outreach/act", data={"lead": "Acme", "do": "sent", "step": 0})
    assert store.load_emails("Acme", views.DB)[0].status == "sent"


def test_approve_is_blocked_when_the_opt_out_line_was_cut(client):
    seed()
    client.post("/outreach/act", data={"lead": "Acme", "do": "edit", "step": 0, "subject": "Hello",
                                       "body": "Hi there,\n\nAcme builds payroll software."})
    r = client.post("/outreach/act", data={"lead": "Acme", "do": "approve", "step": 0}, follow_redirects=True)
    assert "opt-out line is missing" in r.text
    assert store.load_emails("Acme", views.DB)[0].status == "draft"


def test_stopping_a_lead_stops_its_emails(client):
    seed()
    client.post("/outreach/act", data={"lead": "Acme", "do": "stage", "stage": "replied"})
    assert store.load_emails("Acme", views.DB)[0].status == "stopped"


def test_delete_needs_the_tick_box(client):
    seed()
    client.post("/outreach/act", data={"lead": "Acme", "do": "delete"})
    assert len(store.load_all(views.DB)) == 1
    client.post("/outreach/act", data={"lead": "Acme", "do": "delete", "sure": "1"})
    assert store.load_all(views.DB) == []


def test_do_not_contact_list_round_trip(client):
    seed()
    client.post("/outreach/act", data={"lead": "Acme", "do": "suppress", "reason": "asked"})
    assert "Acme" in store.suppressed(views.DB)
    client.post("/outreach/unsuppress", data={"company": "Acme"})
    assert store.suppressed(views.DB) == {}


def test_leads_csv_follows_the_filters(client):
    seed(emails=False)
    assert "Acme" in client.get("/leads.csv?group=All").text
    assert "Acme" not in client.get("/leads.csv?min_fit=95").text


def test_a_missing_logo_gets_a_letter_tile(client):
    r = client.get("/logo/Zzz-not-saved")
    assert r.headers["content-type"].startswith("image/svg+xml") and ">Z<" in r.text


def test_password_gate(client, monkeypatch):
    monkeypatch.setattr(web_app, "PASSWORD", "open sesame")
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/static/app.css").status_code == 200
    assert "Wrong password" in client.post("/login", data={"password": "nope"}).text
    assert client.post("/login", data={"password": "open sesame"}, follow_redirects=False).status_code == 303
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_icp_save_rejects_a_backwards_size_range(client):
    r = client.post("/icp", data={"company_size_min": 500, "company_size_max": 50}, follow_redirects=True)
    assert "bigger than the largest" in r.text


def test_a_background_job_reports_its_lines_and_blocks_a_second(client):
    import threading
    from web import jobs
    gate = threading.Event()

    def work(report):
        report("step one")
        gate.wait(2)

    first = jobs.start("Test job", work)
    assert jobs.start("Another", work) is first
    gate.set()
    for _ in range(50):
        if first.done:
            break
        threading.Event().wait(0.05)
    status = client.get(f"/jobs/{first.id}").json()
    assert status["done"] and status["lines"] == ["step one"] and status["failed"] == ""


def test_suggest_returns_titles_for_the_buyer_persona_only(client, monkeypatch):
    monkeypatch.setattr(web_app, "suggest_buyer", lambda draft, texts: ("Head of Sales.", ["VP Sales", "CRO"]))
    monkeypatch.setattr(web_app, "suggest", lambda field, draft, texts: "A plain sentence.")
    body = {"context": {"buyer_persona": "Head of Sales", "pain_solved": "", "notes": ""}}
    buyer = client.post("/icp/suggest", json={"field": "buyer_persona", **body})
    assert buyer.json() == {"text": "Head of Sales.", "titles": ["VP Sales", "CRO"]}
    other = client.post("/icp/suggest", json={"field": "pain_solved", **body})
    assert other.json() == {"text": "A plain sentence."}


def test_suggest_says_so_when_the_model_is_down(client, monkeypatch):
    def down(draft, texts):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(web_app, "suggest_buyer", down)
    r = client.post("/icp/suggest", json={"field": "buyer_persona", "context": {}})
    assert r.status_code == 502 and "ollama serve" in r.json()["error"]


def icp_file(tmp_path, monkeypatch):
    import shutil
    path = tmp_path / "icp.json"
    shutil.copy(web_app.ICP_PATH, path)
    monkeypatch.setattr(web_app, "ICP_PATH", path)
    return path


def scoring_form(path, **change):
    import json
    s = json.loads(path.read_text(encoding="utf-8"))["settings"]
    data = {k: s[k] for k, *_ in web_app.SETTING_FIELDS} | {f"weight:{n}": w for n, w in s["weights"].items()}
    return data | change


def test_icp_page_names_facts_in_words(client):
    html = client.get("/icp").text
    assert ">How it sells<" in html and ">has salespeople, method unclear<" in html
    assert ">sales_team_unclear<" not in html and ">is_services<" not in html


def test_icp_page_shows_the_numbers_and_rules_as_inputs(client):
    html = client.get("/icp").text
    assert 'name="qualify_at"' in html and 'name="weight:industry match"' in html
    assert html.count('name="rule_fact"') >= 1 and 'id="add-rule"' in html


def test_scoring_numbers_save(client, tmp_path, monkeypatch):
    import json
    path = icp_file(tmp_path, monkeypatch)
    r = client.post("/icp/scoring", data=scoring_form(path, qualify_at=75, **{"weight:pain match": 2}),
                    follow_redirects=True)
    assert "Saved" in r.text
    saved = json.loads(path.read_text(encoding="utf-8"))["settings"]
    assert saved["qualify_at"] == 75 and saved["weights"]["pain match"] == 2


def test_scoring_refuses_a_cutoff_order_that_cannot_work(client, tmp_path, monkeypatch):
    import json
    path = icp_file(tmp_path, monkeypatch)
    before = path.read_text(encoding="utf-8")
    r = client.post("/icp/scoring", data=scoring_form(path, qualify_at=30, disqualify_below=60),
                    follow_redirects=True)
    assert "Not saved" in r.text and "higher than disqualify_below" in r.text
    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before)["settings"]["qualify_at"] != 30


def test_scoring_refuses_a_missing_or_wrong_number(client, tmp_path, monkeypatch):
    path = icp_file(tmp_path, monkeypatch)
    before = path.read_text(encoding="utf-8")
    data = scoring_form(path, qualify_at="70.5")
    assert "Not saved" in client.post("/icp/scoring", data=data, follow_redirects=True).text
    data = scoring_form(path)
    del data["fail_cap"]
    assert "Not saved" in client.post("/icp/scoring", data=data, follow_redirects=True).text
    assert path.read_text(encoding="utf-8") == before


def test_a_post_without_the_page_token_is_refused(client):
    seed()
    assert client.post("/outreach/act", data={"lead": "Acme", "do": "approve", "step": 0}, csrf=False).status_code == 403
    assert client.post("/outreach/act", data={"lead": "Acme", "do": "approve", "step": 0, "csrf": "forged"},
                       csrf=False).status_code == 403
    assert client.post("/icp/suggest", json={"field": "notes", "context": {}}, csrf=False).status_code == 403


def test_login_locks_after_five_wrong_tries(client, monkeypatch):
    monkeypatch.setattr(web_app, "PASSWORD", "open sesame")
    for _ in range(web_app.MAX_TRIES):
        assert "Wrong password" in client.post("/login", data={"password": "nope"}).text
    locked = client.post("/login", data={"password": "open sesame"})
    assert locked.status_code == 429 and "Too many wrong tries" in locked.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_a_failed_action_shows_no_internal_detail(client, monkeypatch):
    seed()

    def broken(*args, **kwargs):
        raise RuntimeError("secret path C:/internal/db")

    monkeypatch.setattr(web_app, "draft_step", broken)
    r = client.post("/outreach/act", data={"lead": "Acme", "do": "draft", "step": 0}, follow_redirects=True)
    assert "details are in the app" in r.text and "C:/internal" not in r.text


def test_rules_save_add_and_remove(client, tmp_path, monkeypatch):
    import json
    path = icp_file(tmp_path, monkeypatch)
    rules = {"rule_fact": ["customers", "sales_motion"], "rule_value": ["consumers", "no_sales"],
             "rule_label": ["consumer product", "self-serve"], "rule_action": ["disqualify", "review"]}
    assert "Saved" in client.post("/icp/rules", data=rules, follow_redirects=True).text
    saved = json.loads(path.read_text(encoding="utf-8"))["fact_rules"]
    assert [(r["fact"], r["value"], r["action"]) for r in saved] == [
        ("customers", "consumers", "disqualify"), ("sales_motion", "no_sales", "review")]


def test_rules_refuse_a_value_the_fact_cannot_have(client, tmp_path, monkeypatch):
    path = icp_file(tmp_path, monkeypatch)
    before = path.read_text(encoding="utf-8")
    bad = {"rule_fact": ["customers"], "rule_value": ["nonsense"], "rule_label": ["x"], "rule_action": ["review"]}
    assert "Not saved" in client.post("/icp/rules", data=bad, follow_redirects=True).text
    blank = {"rule_fact": ["customers"], "rule_value": ["consumers"], "rule_label": ["  "], "rule_action": ["review"]}
    assert "needs a reason" in client.post("/icp/rules", data=blank, follow_redirects=True).text
    assert path.read_text(encoding="utf-8") == before


def test_industry_gates_and_sales_titles_save(client, tmp_path, monkeypatch):
    import json
    path = icp_file(tmp_path, monkeypatch)
    data = {"ind_offering": ["own_software", "services"], "ind_customers": ["businesses", ""],
            "ind_score": ["5", "0"], "industry_default": "1", "gate": ["industry match", "company size match"],
            "sales_definition": "sells to new customers", "sales_titles": "account executive\n\n  sdr  \n"}
    assert "Saved" in client.post("/icp/match", data=data, follow_redirects=True).text
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["industry_map"] == [{"offering": "own_software", "customers": "businesses", "score": 5},
                                     {"offering": "services", "score": 0}]
    assert saved["industry_default"] == 1 and saved["gates"] == ["industry match", "company size match"]
    assert saved["sales_roles"] == {"definition": "sells to new customers", "titles": ["account executive", "sdr"]}


def test_industry_save_refuses_bad_values(client, tmp_path, monkeypatch):
    path = icp_file(tmp_path, monkeypatch)
    before = path.read_text(encoding="utf-8")
    base = {"ind_offering": ["own_software"], "ind_customers": ["businesses"], "ind_score": ["5"],
            "industry_default": "2", "gate": ["industry match"], "sales_definition": "x", "sales_titles": "sdr"}
    for change in ({"ind_score": ["9"]}, {"ind_score": ["high"]}, {"sales_titles": "  "}, {"gate": ["vibes"]}):
        r = client.post("/icp/match", data=base | change, follow_redirects=True)
        assert "Not saved" in r.text, change
    assert path.read_text(encoding="utf-8") == before


def test_opening_a_lead_never_writes_to_the_store(client):
    seed()
    ceo = contacts.Contact(name="Zed Khan", title="CEO", matches_buyer="no", source="https://acme.example/team",
                           fetched_at="2026-10-24T00:00:00+00:00")
    store.save_contacts("Acme", contacts.ContactSearch(people=[ceo], searched_at="t"), views.DB)
    before = store.load_contacts("Acme", views.DB)
    page = client.get("/outreach?lead=Acme").text
    assert "Zed Khan" in page
    assert store.load_contacts("Acme", views.DB) == before
    assert before.contact is None


def test_saving_the_ideal_customer_leaves_no_temporary_file(client, tmp_path, monkeypatch):
    target = tmp_path / "icp.json"
    target.write_text(web_app.ICP_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(web_app, "ICP_PATH", target)
    r = client.post("/icp", data={"buyer_persona": "Head of Sales", "pain_solved": "p", "notes": "n",
                                  "company_size_min": 10, "company_size_max": 500, "countries": ["India"]},
                    follow_redirects=True)
    assert "Saved." in r.text
    assert '"buyer_persona": "Head of Sales"' in target.read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".tmp") == []


def test_sender_settings_keep_the_saved_limits_when_the_form_leaves_them_out(client, tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(web_app, "save_outreach", saved.append)
    r = client.post("/outreach/settings", data={"sender_name": "Sam", "sender_company": "LeadSage",
                                                "sender_address": "1 Test Street", "opt_out_line": "Reply stop.",
                                                "draft_verdicts": ["qualify"], "follow_up_days": "3, 7"},
                    follow_redirects=True)
    assert "saved" in r.text
    assert (saved[0].max_words, saved[0].startup_max_headcount) == (CFG.max_words, CFG.startup_max_headcount)
    assert saved[0].follow_up_days == [3, 7]


@pytest.mark.parametrize("query, shown", [("min_fit=40", 1), ("min_fit=abc", 1), ("min_fit=95", 0)])
def test_the_minimum_fit_filter_ignores_a_bad_value(client, query, shown):
    seed(emails=False)
    assert client.get(f"/leads.csv?group=All&{query}").text.count("Acme") == shown


ALL_PAGES = ["/", "/leads?group=All&lead=Acme", "/outreach?lead=Acme", "/icp", "/login"]


@pytest.mark.parametrize("path", ALL_PAGES + ["/static/app.js", "/leads.csv", "/no-such-page"])
def test_every_response_carries_the_browser_rules(client, path):
    seed()
    headers = client.get(path).headers
    policy = headers["Content-Security-Policy"]
    assert "script-src 'self';" in policy and "frame-ancestors 'none'" in policy and "object-src 'none'" in policy
    assert (headers["X-Frame-Options"], headers["X-Content-Type-Options"], headers["Referrer-Policy"]) == (
        "DENY", "nosniff", "no-referrer")


def test_the_login_redirect_carries_the_browser_rules_too(client, monkeypatch):
    monkeypatch.setattr(web_app, "PASSWORD", "secret")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("path", ALL_PAGES)
def test_no_page_has_inline_script_or_event_handlers(client, path):
    seed()
    html = client.get(path).text
    assert re.findall(r"<script(?![^>]*\ssrc=)[^>]*>", html) == []
    assert re.findall(r"\son[a-z]+\s*=", html) == []


def test_a_clicked_table_row_opens_its_lead(client):
    seed()
    assert 'data-href="?group=All&amp;lead=Acme#detail"' in client.get("/leads?group=All").text


def test_logos_are_sandboxed_and_a_stored_svg_is_never_served(client, tmp_path, monkeypatch):
    real = logos.logo_path
    (tmp_path / "acme.svg").write_bytes(b"<svg onload='steal()'/>")
    monkeypatch.setattr(logos, "logo_path", lambda name: real(name, tmp_path))
    r = client.get("/logo/Acme")
    assert "steal" not in r.text and ">A</text>" in r.text
    assert r.headers["Content-Security-Policy"].endswith("sandbox")
    (tmp_path / "acme.png").write_bytes(b"png")
    r = client.get("/logo/Acme")
    assert r.content == b"png" and r.headers["content-type"] == "image/png"
    assert "sandbox" in r.headers["Content-Security-Policy"]
