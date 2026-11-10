"""The per-lead trace: what is written, in what order, how long it is kept and when it is deleted.
No model, no network: Ollama and Groq are faked below the trace."""

from types import SimpleNamespace

import pytest

from agent import llm, pipeline, store, trace
from agent import score_lead as sl
from agent.contacts import Contact, ContactSearch
from agent.store import Email
from tests.test_scoring import COMPANY, facts, make_icp, ratings
from tests.test_store import score


class FakeClient:
    """Ollama stand-in: answers by schema; a model named in `broken` answers with bad JSON."""

    def __init__(self, answers, broken=()):
        self.answers, self.broken, self.calls = answers, broken, []

    def chat(self, model, messages, format, options, think):
        self.calls.append(model)
        title = format.get("title", "")
        content = "{not json" if model in self.broken else self.answers[title].model_dump_json()
        return SimpleNamespace(message=SimpleNamespace(content=content))


@pytest.fixture
def ollama(monkeypatch):
    def install(answers, broken=()):
        client = FakeClient(answers, broken)
        monkeypatch.setattr(llm, "_ollama_client", lambda host: client)
        return client
    return install


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "leads.db"
    store.save(COMPANY, score(50), url="https://acme.example", text_source="typed", path=path)
    return path


def steps(db, lead=COMPANY.name):
    return [s.step for s in store.load_trace(lead, db)]


def test_a_model_call_outside_a_run_leaves_no_trace(ollama, db):
    ollama({"CompanyFacts": facts()})
    llm.generate_structured("sys", "user", sl.CompanyFacts)
    assert store.load_trace(COMPANY.name, db) == []


def test_a_model_call_inside_a_run_keeps_the_whole_prompt_and_answer(ollama, db):
    ollama({"CompanyFacts": facts()})
    long_prompt = "page text " * 2000
    with trace.run(COMPANY.name, "score", db):
        llm.generate_structured("SYSTEM RULES", long_prompt, sl.CompanyFacts)
    [s] = store.load_trace(COMPANY.name, db)
    assert s.step == "Read the facts" and s.ok and s.model == llm.model_name()
    assert s.input == f"SYSTEM\nSYSTEM RULES\n\nUSER\n{long_prompt}"
    assert sl.CompanyFacts.model_validate_json(s.output) == facts()


def test_a_failed_model_and_its_stand_in_are_both_written(ollama, db):
    ollama({"CompanyFacts": facts()}, broken=(llm.model_name(),))
    with trace.run(COMPANY.name, "score", db):
        llm.generate_structured("sys", "user", sl.CompanyFacts)
    first, second = store.load_trace(COMPANY.name, db)
    assert (first.ok, first.model, first.output) == (False, llm.model_name(), "{not json")
    assert "ValidationError" in first.error
    assert (second.ok, second.model) == (True, llm.fallback_model_name(llm.model_name()))


def test_a_scoring_run_writes_every_step_in_order(ollama, db):
    ollama({"CompanyFacts": facts(builds_own_software=("yes", "Sells a CRM"),
                                  customers=("businesses", "to small businesses")),
            "FitScores": ratings((4, "Founders do all the selling"), (4, "Sells a CRM"))})
    pipeline.score_one(make_icp(), COMPANY, "https://acme.example", "typed", lambda m: None, db)
    assert steps(db) == ["Company text", "Read the facts", "Check the fact quotes", "Apply the rules",
                         "Rate buyer and pain", "Check the rating quotes", "Fit and verdict"]
    assert len(trace.runs(COMPANY.name, db)) == 1


def test_a_batch_second_opinion_joins_the_leads_own_run(monkeypatch, tmp_path):
    db = tmp_path / "leads.db"
    monkeypatch.setattr(pipeline, "research", lambda row, icp, signals: (
        COMPANY.model_copy(update={"name": row["name"]}), "typed"))
    monkeypatch.setattr(pipeline, "score_lead", lambda icp, company, model=None: score(55))
    pipeline.score_all([{"name": "Edge", "url": ""}], make_icp(), {}, report=lambda m: None, db=db)
    [run] = trace.runs("Edge", db)
    assert [s.step for s in run.steps] == ["Company text", "Second opinion"]


def test_only_the_last_runs_of_each_kind_are_kept(db):
    for n in range(5):
        with trace.run(COMPANY.name, "score", db):
            trace.step(f"score {n}")
    with trace.run(COMPANY.name, "draft", db):
        trace.step("draft 0")
    kept = trace.runs(COMPANY.name, db)
    assert [r.steps[0].step for r in kept if r.kind == "score"] == ["score 4", "score 3", "score 2"]
    assert [r.kind for r in kept].count("draft") == 1


def test_a_rescore_inside_a_watch_lands_in_the_watch_run(db):
    with trace.run(COMPANY.name, "watch", db):
        trace.step("Read the job board")
        with trace.run(COMPANY.name, "score", db):
            trace.step("Fit and verdict")
    [run] = trace.runs(COMPANY.name, db)
    assert run.kind == "watch" and [s.step for s in run.steps] == ["Read the job board", "Fit and verdict"]


def test_approve_and_send_join_the_latest_email_run(db):
    with trace.run(COMPANY.name, "draft", db):
        trace.step("Write the email")
    store.save_email(COMPANY.name, Email(step=0, subject="Hi", body="b", drafted_at="t"), db)
    store.approve_email(COMPANY.name, 0, db)
    store.mark_sent(COMPANY.name, 0, db)
    [run] = trace.runs(COMPANY.name, db)
    assert [s.step for s in run.steps] == ["Write the email", "Approved email 1", "Sent email 1"]


def test_approve_with_no_email_run_starts_one(db):
    store.save_email(COMPANY.name, Email(step=0, subject="Hi", body="b", drafted_at="t"), db)
    store.approve_email(COMPANY.name, 0, db)
    [run] = trace.runs(COMPANY.name, db)
    assert run.kind == "draft" and run.steps[0].step == "Approved email 1"


def fill(db):
    with trace.run(COMPANY.name, "draft", db):
        trace.step("Write the email", output="Hi Priya Rao", input="WRITING TO Priya Rao")
    assert store.trace_size(COMPANY.name, db) == (1, 1)


def test_forgetting_the_person_deletes_the_companys_trace(db):
    fill(db)
    store.forget_person(COMPANY.name, "Priya Rao", db)
    assert store.load_trace(COMPANY.name, db) == []
    assert b"Priya Rao" not in db.read_bytes()


def test_deleting_the_lead_deletes_its_trace(db):
    fill(db)
    store.delete_lead(COMPANY.name, db)
    assert store.load_trace(COMPANY.name, db) == []


def test_stale_forgetting_deletes_the_trace(db):
    fill(db)
    person = Contact(name="Priya Rao", title="Head of Sales", matches_buyer="yes", source="s",
                     fetched_at="2026-01-01T00:00:00+00:00")
    store.save_contacts(COMPANY.name, ContactSearch(contact=person, searched_at="2026-01-01T00:00:00+00:00"), db)
    from datetime import date
    assert store.forget_stale_contacts(30, date(2026, 11, 8), db) == [COMPANY.name]
    assert store.load_trace(COMPANY.name, db) == []


def test_a_groq_failure_is_written_with_the_key_masked(monkeypatch, db):
    key = "gsk_trace_test_key"
    monkeypatch.setenv("GROQ_API_KEY", key)

    def boom(*a, **kw):
        raise RuntimeError(f"bad key {key}")

    monkeypatch.setattr(llm, "_groq_raw", boom)
    monkeypatch.setattr(llm, "generate_structured", lambda *a, **kw: facts())
    with trace.run(COMPANY.name, "draft", db):
        llm.generate_writing("sys", "user", sl.CompanyFacts)
    [s] = store.load_trace(COMPANY.name, db)
    assert not s.ok and s.model.startswith("groq:") and key not in s.error and "***" in s.error


def test_the_lead_page_shows_the_trace_as_plain_text(db, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.test_outreach import CFG
    from web import app as web_app
    from web import views
    monkeypatch.setattr(views, "DB", db)
    monkeypatch.setattr(web_app, "load_outreach", lambda: CFG)
    monkeypatch.setattr(web_app, "PASSWORD", "")
    with trace.run(COMPANY.name, "score", db):
        trace.step("Read the facts", output='{"x": 1}', input="<script>alert(1)</script>", model="m")
    page = TestClient(web_app.app).get("/leads?group=All&lead=X").text
    assert "What the agent did" in page and "Read the facts" in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert(1)" not in page
