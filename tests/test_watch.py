"""Signal watcher: baselines, what fires, rescoring and failures. No model, no network."""

import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from agent import outreach, pipeline, press, store, watch
from agent.fetch import Page
from agent.hooks import NewsItem, NewsSearch, best_item
from agent.score_lead import Company, LeadScore, SalesRole
from agent.signals import Hiring
from tests.test_outreach import CFG
from tests.test_scoring import make_icp

ICP = make_icp()
TODAY = date(2026, 10, 27)


def role(title: str) -> SalesRole:
    return SalesRole(title=title, match="exact", source="https://jobs.example/board", fetched_at="2026-10-27T00:00:00+00:00")


def item(headline: str, days_ago: int, kind: str = "launch") -> NewsItem:
    day = (TODAY - timedelta(days=days_ago)).isoformat()
    return NewsItem(headline=headline, date_text=day, date=day, kind=kind, source="https://acme.example/news",
                    fetched_at="2026-10-27T00:00:00+00:00")


def lead_score(fit: int, verdict: str, model: str = "main") -> LeadScore:
    return LeadScore(fit=fit, verdict=verdict, criteria=[], disqualifier_hit=False, disqualifier_reason="",
                     summary="s", model=model)


@pytest.fixture
def w(tmp_path, monkeypatch):
    w = SimpleNamespace(db=tmp_path / "leads.db", roles={}, news={}, board_down=set(), broken=set(),
                        hiring_calls=[], news_calls=[], scored=[], main=lead_score(85, "qualify"),
                        press=[], press_failed=[], articles={}, article_reads=[])

    def hiring_for(row, icp, refetch=False):
        name = row["name"]
        w.hiring_calls.append(name)
        if name in w.broken:
            raise RuntimeError("model down")
        if name in w.board_down:
            return Hiring(ok=False, error="timed out")
        return Hiring(ok=True, board="ashby:x", jobs_seen=9, roles=[role(t) for t in w.roles.get(name, [])])

    def news_for(lead, cfg, db, refresh=False):
        w.news_calls.append(lead.name)
        return NewsSearch(items=w.news.get(lead.name, []), pages_read=1, searched_at="2026-10-27T00:00:00+00:00")

    def score_lead(icp, company, model=None):
        w.scored.append((company.name, model or "main"))
        return w.main if model is None else lead_score(60, "review", model)

    monkeypatch.setattr(watch, "hiring_for", hiring_for)
    def read_article(url):
        w.article_reads.append(url)
        text = w.articles.get(url)
        return Page(url=url, text=text or "", fetched_at="2026-10-27T00:00:00+00:00", ok=text is not None,
                    error="" if text is not None else "timed out")

    monkeypatch.setattr(watch, "news_for", news_for)
    monkeypatch.setattr(pipeline, "score_lead", score_lead)
    monkeypatch.setattr(press, "read_feeds", lambda days, today: (w.press, w.press_failed))
    monkeypatch.setattr(press, "read_article", read_article)
    return w


def add(w, name="Acme", roles=(), verdict="qualify", fit=85, stage=""):
    company = Company(name=name, industry="B2B SaaS", size="120", location="Pune, India", description="CRM.",
                      sales_roles=[role(t) for t in roles])
    store.save(company, lead_score(fit, verdict), url=f"https://{name.lower()}.example", text_source="typed", path=w.db)
    w.roles[name] = list(roles)
    if stage:
        store.set_stage(name, stage, w.db)


def run(w, **kw):
    signals = {n.lower(): {"name": n} for n in w.roles}
    return watch.watch_leads(ICP, CFG, db=w.db, report=lambda msg: None, signals=signals, today=TODAY, **kw)


def fired(result):
    return [(lead, e.kind, e.quote) for lead, e in result.fired]


def test_baseline_run_stores_everything_and_fires_nothing(w):
    add(w, roles=["Account Executive"])
    w.news["Acme"] = [item("Acme launches a billing module", 2)]
    result = run(w)
    assert result.fired == [] and result.rescored == []
    assert store.known_events("Acme", "hiring", w.db) == {"account executive"}
    assert len(store.known_events("Acme", "news", w.db)) == 1
    assert store.fresh_events("2000-01-01", w.db) == {}


def test_a_new_role_fires_once_and_rescores_once(w):
    add(w, roles=["Account Executive"])
    run(w)
    w.roles["Acme"].append("SDR, India")
    second = run(w)
    assert fired(second) == [("Acme", "hiring", "SDR, India")]
    assert second.rescored == ["Acme"]
    assert len(store.load("Acme", w.db).company.sales_roles) == 2
    third = run(w)
    assert third.fired == [] and third.rescored == []
    assert list(store.fresh_events("2000-01-01", w.db)) == ["Acme"]


def test_old_news_first_seen_today_does_not_fire_but_recent_news_does(w):
    add(w)
    run(w)
    w.news["Acme"] = [item("Acme opens an office in Singapore", 60), item("Acme launches a billing module", 3)]
    result = run(w)
    assert fired(result) == [("Acme", "news", "Acme launches a billing module")]


def test_a_plain_blog_post_is_stored_but_does_not_fire(w):
    add(w)
    run(w)
    w.news["Acme"] = [item("4 features I can't live without", 2, kind="post"),
                      item("Acme partners with Zoho", 2, kind="partnership")]
    result = run(w)
    assert fired(result) == [("Acme", "news", "Acme partners with Zoho")]
    assert len(store.known_events("Acme", "news", w.db)) == 2


@pytest.mark.parametrize("stage", ["lost", "replied", "meeting"])
def test_stopped_leads_are_not_watched(w, stage):
    add(w, stage=stage)
    result = run(w)
    assert result.watched == 0 and w.hiring_calls == [] and w.news_calls == []


def test_do_not_contact_leads_are_not_watched(w):
    add(w)
    with sqlite3.connect(w.db) as conn:
        conn.execute("INSERT INTO suppressed (lead, reason, added_at) VALUES ('Acme', 'asked', '2026-10-27')")
    assert run(w).watched == 0 and w.hiring_calls == []


def test_news_never_rescores_or_changes_the_score(w):
    add(w, roles=["Account Executive"])
    run(w)
    before = store.load("Acme", w.db).score
    w.news["Acme"] = [item("Acme raises a Series A", 1)]
    result = run(w)
    assert fired(result) == [("Acme", "news", "Acme raises a Series A")]
    assert result.rescored == [] and w.scored == []
    after = store.load("Acme", w.db).score
    assert (after.fit, after.verdict) == (before.fit, before.verdict)


def test_rescore_runs_the_second_model_on_a_borderline_lead(w):
    add(w, roles=["Account Executive"])
    run(w)
    w.main = lead_score(55, "review")
    w.roles["Acme"] = []
    result = run(w)
    assert result.rescored == ["Acme"]
    assert w.scored == [("Acme", "main"), ("Acme", pipeline.second_model_name())]
    assert store.load("Acme", w.db).score.second_opinion is not None


def test_a_board_that_cannot_be_read_is_unknown_not_empty(w):
    add(w, roles=["Account Executive", "SDR"])
    run(w)
    w.board_down.add("Acme")
    down = run(w)
    assert down.fired == [] and down.rescored == [] and w.scored == []
    assert down.failed == ["Acme: job board not read (timed out)"]
    assert len(store.load("Acme", w.db).company.sales_roles) == 2
    w.board_down.clear()
    back = run(w)
    assert back.fired == [] and back.rescored == [] and back.failed == []


def test_a_failed_first_read_does_not_use_up_the_baseline(w):
    add(w, roles=["Account Executive"])
    w.board_down.add("Acme")
    run(w)
    assert not store.is_baselined("Acme", "hiring", w.db)
    w.board_down.clear()
    assert run(w).fired == []


def test_a_news_search_that_read_no_page_is_a_failure_not_a_baseline(w, monkeypatch):
    add(w)
    monkeypatch.setattr(watch, "news_for", lambda lead, cfg, db, refresh=False: NewsSearch(pages_read=0))
    result = run(w)
    assert result.failed == ["Acme: no news page could be read"]
    assert not store.is_baselined("Acme", "news", w.db)


def test_one_lead_failing_does_not_stop_the_others(w):
    add(w, name="Broken", roles=["SDR"])
    add(w, name="Acme", roles=["Account Executive"])
    run(w)
    w.broken.add("Broken")
    w.roles["Acme"].append("SDR")
    result = run(w)
    assert fired(result) == [("Acme", "hiring", "SDR")]
    assert result.failed == ["Broken: hiring check failed (model down)"]


def test_disqualified_leads_get_hiring_watched_but_no_news(w):
    add(w, roles=["SDR"], verdict="disqualify", fit=20)
    run(w)
    assert w.hiring_calls == ["Acme"] and w.news_calls == []


def test_hiring_only_skips_news(w):
    add(w)
    run(w, hiring_only=True)
    assert w.news_calls == []


def test_forgetting_a_lead_forgets_its_signals(w, tmp_path):
    add(w, roles=["SDR"])
    w.news["Acme"] = [item("Acme raises a Series A", 1)]
    run(w)
    outreach.forget_lead(store.load("Acme", w.db), w.db, tmp_path / "pages", tmp_path / "logos")
    with sqlite3.connect(w.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM watched").fetchone()[0] == 0


def test_the_next_draft_uses_the_news_the_watcher_saved(w, monkeypatch):
    add(w)
    found = []

    def find_news(*args, **kwargs):
        found.append(1)
        return NewsSearch(items=[item("Acme launches a billing module", 2)], pages_read=1,
                          searched_at=store.now())

    monkeypatch.setattr(outreach, "find_news", find_news)
    monkeypatch.setattr(watch, "news_for", outreach.news_for)
    run(w)
    search = outreach.news_for(store.load("Acme", w.db), CFG, w.db)
    assert found == [1]
    assert best_item(search.items).headline == "Acme launches a billing module"


@pytest.mark.parametrize("a, b", [("Account Executive, APAC", "account executive  apac"),
                                  ("SDR (India)", "sdr india")])
def test_event_keys_ignore_case_spacing_and_punctuation(a, b):
    assert watch.event_key(a) == watch.event_key(b)


def test_news_keys_include_the_date():
    assert watch.event_key("Launch", "2026-10-01") != watch.event_key("Launch", "2026-10-02")


def press_item(headline: str, days_ago: int = 1, link: str = "https://entrackr.com/news/a-round") -> NewsItem:
    day = (TODAY - timedelta(days=days_ago)).isoformat()
    return NewsItem(headline=headline, date_text=day, date=day, kind="funding", source=link, outlet="Entrackr",
                    fetched_at="2026-10-27T00:00:00+00:00")


def test_a_new_round_fires_after_the_baseline_and_a_repeat_does_not(w):
    add(w, name="Acme Labs")
    run(w)
    w.press = [press_item("Acme Labs raises $5 Mn in Series A")]
    assert fired(run(w)) == [("Acme Labs", "press", "Acme Labs raises $5 Mn in Series A")]
    assert run(w).fired == []
    assert [i.outlet for i in press.saved_news("Acme Labs", 90, w.db, TODAY)] == ["Entrackr"]


def test_press_baseline_stores_a_match_without_firing_and_it_can_still_be_a_hook(w):
    add(w, name="Acme Labs")
    w.press = [press_item("Acme Labs raises $5 Mn in Series A")]
    assert run(w).fired == []
    assert len(press.saved_news("Acme Labs", 90, w.db, TODAY)) == 1
    assert store.fresh_events("2000-01-01", w.db) == {}


def test_a_lead_added_later_matches_history_the_feed_no_longer_shows(w):
    add(w, name="Other Co")
    w.press = [press_item("Acme Labs raises $5 Mn in Series A")]
    run(w)
    w.press = []
    add(w, name="Acme Labs")
    run(w)
    assert len(press.saved_news("Acme Labs", 90, w.db, TODAY)) == 1


def test_an_old_round_first_seen_today_does_not_fire(w):
    add(w, name="Acme Labs")
    run(w)
    w.press = [press_item("Acme Labs raises $5 Mn in Series A", days_ago=30)]
    assert run(w).fired == []


def test_all_feeds_down_is_unknown_and_records_nothing_for_any_lead(w):
    add(w, name="Acme Labs")
    w.press_failed = [f"{f.outlet}: timed out" for f in press.FEEDS]
    result = run(w)
    assert len([f for f in result.failed if "news feed not read" in f]) == 3
    assert not store.is_baselined("Acme Labs", "press", w.db)


def test_one_feed_down_is_reported_and_the_others_are_used(w):
    add(w, name="Acme Labs")
    w.press_failed = ["Inc42: timed out"]
    w.press = [press_item("Acme Labs raises $5 Mn in Series A")]
    result = run(w)
    assert result.failed == ["news feed not read (Inc42: timed out)"]
    assert len(press.saved_news("Acme Labs", 90, w.db, TODAY)) == 1


def test_hiring_only_skips_the_feeds(w, monkeypatch):
    add(w, name="Acme Labs")
    monkeypatch.setattr(press, "read_feeds", lambda days, today: pytest.fail("feeds read"))
    run(w, hiring_only=True)


def test_one_word_name_needs_its_city_in_the_opening(w):
    add(w, name="Fyle")
    w.press = [press_item("Fyle raises $5 Mn")]
    w.articles[w.press[0].source] = "Pune-based Fyle, an expense tool, has raised funding."
    run(w)
    assert len(press.saved_news("Fyle", 90, w.db, TODAY)) == 1


def test_one_word_name_without_city_or_domain_stays_unmatched_and_is_read_once(w):
    add(w, name="Fyle")
    w.press = [press_item("Fyle raises $5 Mn")]
    w.articles[w.press[0].source] = "Mumbai-based Fyle, a fitness brand, has raised funding."
    run(w)
    run(w)
    assert press.saved_news("Fyle", 90, w.db, TODAY) == []
    assert len(w.article_reads) == 1


def test_an_article_that_cant_be_read_is_asked_about_again_next_run(w):
    add(w, name="Fyle")
    w.press = [press_item("Fyle raises $5 Mn")]
    run(w)
    assert press.saved_news("Fyle", 90, w.db, TODAY) == []
    w.articles[w.press[0].source] = "Pune-based Fyle has raised funding."
    run(w)
    assert len(press.saved_news("Fyle", 90, w.db, TODAY)) == 1
    assert len(w.article_reads) == 2


def test_two_word_name_reads_no_article(w):
    add(w, name="Acme Labs")
    w.press = [press_item("Acme Labs raises $5 Mn in Series A")]
    run(w)
    assert w.article_reads == []


def test_nothing_of_the_article_is_stored(w):
    add(w, name="Fyle")
    w.press = [press_item("Fyle raises $5 Mn")]
    w.articles[w.press[0].source] = "Pune-based Fyle has raised funding, the secret sentence."
    run(w)
    with sqlite3.connect(w.db) as conn:
        dump = "\n".join(str(row) for row in conn.execute("SELECT * FROM signal_events"))
    assert "secret sentence" not in dump
