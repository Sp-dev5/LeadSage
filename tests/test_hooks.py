"""Finding recent news on a company's own site. No model, no network."""

from datetime import date

import pytest

from agent import fetch, hooks, outreach, store
from agent.fetch import Page
from agent.score_lead import Company, LeadScore
from tests.test_outreach import CFG

TODAY = date(2026, 10, 24)
PAGE = Page(url="https://acme.example/news", fetched_at="2026-10-24T00:00:00+00:00", ok=True,
            text="News. Acme raises 10 million, Oct 20, 2026. Acme opens a Pune office, Aug 1, 2026. "
                 "Acme joins a webinar, 3 Jan 2026. Older story, March 2024.")


@pytest.mark.parametrize("text, expected", [
    ("APRIL 21, 2026", date(2026, 4, 21)), ("24 Oct 2026", date(2026, 10, 24)), ("22 Oct, 2026", date(2026, 10, 22)),
    ("6 September 2026", date(2026, 9, 6)), ("2026-10-22", date(2026, 10, 22)),
    ("Oct 23", None), ("Last week", None), ("", None),
])
def test_parse_date(text, expected):
    assert hooks.parse_date(text) == expected


@pytest.fixture
def model_sees(monkeypatch):
    seen = {}
    monkeypatch.setattr(hooks, "generate_structured",
                        lambda system, user_prompt, schema, **kw: hooks.ItemsSeen(items=seen["items"]))
    return seen


def item(headline, date_text, kind="post"):
    return hooks.ItemSeen(headline=headline, date_text=date_text, kind=kind)


def test_only_recent_items_that_are_really_on_the_page_are_kept(model_sees):
    model_sees["items"] = [item("Acme raises 10 million", "Oct 20, 2026", "funding"),
                           item("Acme opens a Pune office", "Aug 1, 2026", "expansion"),
                           item("Acme joins a webinar", "3 Jan 2026", "event"),
                           item("Acme wins an award", "Oct 21, 2026", "award"),
                           item("Acme raises 10 million", "Oct 22, 2026", "funding"),
                           item("An undated story about nothing", "last week", "post")]
    kept, rejected = hooks.read_items(PAGE, 90, TODAY)
    assert [(i.headline, i.date) for i in kept] == [("Acme raises 10 million", "2026-10-20"),
                                                    ("Acme opens a Pune office", "2026-08-01")]
    assert len(rejected) == 4
    assert any("1 item on https://acme.example/news older than 90 days" in r for r in rejected)
    assert any("Acme wins an award" in r and "not on" in r for r in rejected)
    assert any("Oct 22, 2026" in r for r in rejected)
    assert any("last week" in r for r in rejected)


def test_an_item_dated_in_the_future_is_dropped(model_sees):
    page = PAGE.model_copy(update={"text": "Big launch coming, Nov 30, 2026."})
    model_sees["items"] = [item("Big launch coming", "Nov 30, 2026", "launch")]
    kept, rejected = hooks.read_items(page, 90, TODAY)
    assert kept == [] and "in the future" in rejected[0]


def news(headline, kind, day):
    return hooks.NewsItem(headline=headline, date_text=day, date=day, kind=kind, source="s", fetched_at="t")


def test_best_item_is_the_most_newsworthy_kind_then_the_newest():
    items = [news("a post", "post", "2026-10-23"), news("old funding", "funding", "2026-09-01"),
             news("new funding", "funding", "2026-10-01"), news("a launch", "launch", "2026-10-22")]
    assert hooks.best_item(items).headline == "new funding"
    assert hooks.best_item([]) is None


def test_a_plain_blog_post_is_never_the_hook():
    assert hooks.best_item([news("How the algorithm works", "post", "2026-10-23")]) is None


def test_a_menu_label_is_not_a_headline(model_sees):
    page = PAGE.model_copy(update={"text": "Compliance Hub. Oct 24, 2026. Compliance Hub. Oct 20, 2026."})
    model_sees["items"] = [item("Compliance Hub", "Oct 24, 2026"), item("Compliance Hub", "Oct 20, 2026")]
    kept, rejected = hooks.read_items(page, 90, TODAY)
    assert kept == [] and len(rejected) == 2 and "too short" in rejected[0]


def test_find_news_skips_dead_pages_and_pages_with_no_dates(tmp_path):
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("/news"):
            return PAGE.model_copy(update={"url": url})
        if url.endswith("/blog"):
            return Page(url=url, fetched_at="t", ok=True, text="Welcome to our blog. No dates here.")
        return Page(url=url, fetched_at="t", ok=False, error="HTTP 404")

    read_pages = []

    def read(page, max_age_days, today):
        read_pages.append(page.url)
        return [news("Acme raises 10 million", "funding", "2026-10-20")], []

    search = hooks.find_news("Acme", "https://acme.example", ["/news", "/blog", "/press"], 90, tmp_path, fetch,
                             TODAY, read)
    assert sorted(fetched) == ["https://acme.example/blog", "https://acme.example/news", "https://acme.example/press"]
    assert read_pages == ["https://acme.example/news"]
    assert [i.headline for i in search.items] == ["Acme raises 10 million"]
    assert any("HTTP 404" in p for p in search.pages_tried)


def test_find_news_with_no_url_searches_nothing(tmp_path):
    search = hooks.find_news("Acme", "", ["/news"], 90, tmp_path, lambda url: pytest.fail("fetched"), TODAY)
    assert search.items == [] and search.pages_tried == []


COMPANY = Company(name="Acme", industry="B2B SaaS", size="120", location="Pune, India", description="Payroll software.")
SCORE = LeadScore(fit=80, verdict="qualify", criteria=[], disqualifier_hit=False, disqualifier_reason="", summary="s")


def make_lead(db, roles=()):
    company = COMPANY.model_copy(update={"sales_roles": list(roles)})
    store.save(company, SCORE, url="https://acme.example", text_source="typed", path=db)
    return store.load("Acme", db)


def test_news_search_is_saved_and_reused_for_a_day(tmp_path, monkeypatch):
    db = tmp_path / "leads.db"
    lead = make_lead(db)
    calls = []

    def find(name, url, paths, max_age_days):
        calls.append(name)
        return hooks.NewsSearch(items=[news("Acme raises 10 million", "funding", "2026-10-20")],
                                searched_at=fetch.now())

    monkeypatch.setattr(outreach, "find_news", find)
    first = outreach.news_for(lead, CFG, db)
    again = outreach.news_for(lead, CFG, db)
    assert calls == ["Acme"] and first == again and store.load_news("Acme", db) == first
    outreach.news_for(lead, CFG, db, refresh=True)
    assert calls == ["Acme", "Acme"]


def test_a_news_search_older_than_a_day_is_redone(tmp_path, monkeypatch):
    db = tmp_path / "leads.db"
    lead = make_lead(db)
    store.save_news("Acme", hooks.NewsSearch(searched_at="2026-01-01T00:00:00+00:00"), db)
    calls = []
    monkeypatch.setattr(outreach, "find_news", lambda *a: calls.append(a) or hooks.NewsSearch(searched_at=fetch.now()))
    outreach.news_for(lead, CFG, db)
    assert len(calls) == 1
