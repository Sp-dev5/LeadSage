"""Wikipedia summaries: only a real article about the company counts. No network."""

import json

import pytest

from agent import fetch, wikipedia
from agent.fetch import Page


def answer(**data) -> Page:
    return Page(url="https://en.wikipedia.org/", text=json.dumps(data), fetched_at="2026-11-01T00:00:00+00:00", ok=True)


def summary(name, page):
    return wikipedia.company_summary(name, fetch=lambda url: page)


def test_a_company_article_gives_its_summary():
    page = answer(type="standard", description="Indian multinational technology company",
                  extract="Infosys Limited is an Indian multinational technology company.")
    assert summary("Infosys", page).startswith("Infosys Limited")


@pytest.mark.parametrize("data", [
    {"type": "standard", "description": "2000s French recreational keelboat", "extract": "The Sprinto is a sailboat."},
    {"type": "standard", "description": "Person authorized to act", "extract": "The title of deputy is a person."},
    {"type": "disambiguation", "description": "Topics referred to by the same term", "extract": "Swipe may refer to:"},
    {"type": "standard", "description": "American software company", "extract": "Another name altogether."},
    {"type": "standard", "extract": "Sprinto with no description."},
])
def test_a_page_about_something_else_gives_nothing(data):
    assert summary("Sprinto", answer(**data)) == ""


def test_a_failed_read_or_bad_json_gives_nothing():
    down = Page(url="https://en.wikipedia.org/", fetched_at="2026-11-01T00:00:00+00:00", ok=False, error="HTTP 404")
    assert summary("Acme", down) == ""
    assert summary("Acme", Page(url="x", text="not json", fetched_at="t", ok=True)) == ""
    assert summary("Acme", Page(url="x", text="[1]", fetched_at="t", ok=True)) == ""


def test_the_name_is_made_safe_for_the_url():
    seen = []
    wikipedia.company_summary("Tata Consultancy/Services", fetch=lambda url: seen.append(url) or answer())
    assert seen == [wikipedia.API + "Tata_Consultancy%2FServices"]


def test_only_the_summary_path_skips_robots(monkeypatch):
    monkeypatch.setattr(fetch, "_allowed_by_robots", lambda url: False)
    assert fetch._robots_ok(wikipedia.API + "Infosys")
    assert not fetch._robots_ok("https://en.wikipedia.org/wiki/Infosys")
    assert not fetch._robots_ok("https://en.wikipedia.org/api/rest_v1/page/html/Infosys")
