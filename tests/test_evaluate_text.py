"""Which text the evaluation scores: typed description or fetched website. No network."""

import pytest

from agent import pipeline
from agent.fetch import Page

TYPED = "Acme builds billing software for logistics companies."
FETCHED = "Acme: invoicing and payment reminders for freight firms, from the real website."


def row(url: str = "https://acme.test/") -> dict:
    return {"name": "Acme Co.", "description": TYPED, "url": url}


def page(ok: bool = True) -> Page:
    return Page(url="https://acme.test/", text=FETCHED if ok else "",
                fetched_at="2026-10-15T12:00:00+00:00", ok=ok, error="" if ok else "HTTP 403")


class Calls(list):
    """URLs fetch_homepage was asked for; set `result` to change the page it returns."""
    result: Page | None = None


@pytest.fixture
def fetch_calls(monkeypatch):
    calls = Calls()

    def fake(url):
        calls.append(url)
        return calls.result or page()

    monkeypatch.setattr(pipeline, "fetch_homepage", fake)
    return calls


def test_no_url_uses_typed_and_never_fetches(tmp_path, fetch_calls):
    assert pipeline.lead_text(row(url=""), "fetched", tmp_path) == (TYPED, "typed")
    assert fetch_calls == []


def test_typed_mode_never_fetches(tmp_path, fetch_calls):
    assert pipeline.lead_text(row(), "typed", tmp_path) == (TYPED, "typed")
    assert fetch_calls == []


def test_csv_without_url_column_uses_typed(tmp_path, fetch_calls):
    no_url = {"name": "Acme Co.", "description": TYPED}
    assert pipeline.lead_text(no_url, "fetched", tmp_path) == (TYPED, "typed")


def test_fetch_ok_uses_page_and_caches_it(tmp_path, fetch_calls):
    assert pipeline.lead_text(row(), "fetched", tmp_path) == (FETCHED, "fetched")
    assert fetch_calls == ["https://acme.test/"]
    cached = tmp_path / "acme-co.json"
    assert Page.model_validate_json(cached.read_text(encoding="utf-8")).text == FETCHED


def test_cached_page_is_reused(tmp_path, fetch_calls):
    (tmp_path / "acme-co.json").write_text(page().model_dump_json(), encoding="utf-8")
    assert pipeline.lead_text(row(), "fetched", tmp_path) == (FETCHED, "fetched")
    assert fetch_calls == []


def test_refetch_ignores_cache(tmp_path, fetch_calls):
    stale = page().model_copy(update={"text": "old text"})
    (tmp_path / "acme-co.json").write_text(stale.model_dump_json(), encoding="utf-8")
    assert pipeline.lead_text(row(), "fetched", tmp_path, refetch=True) == (FETCHED, "fetched")
    assert fetch_calls == ["https://acme.test/"]


def test_fetch_failure_falls_back_to_typed_and_is_not_cached(tmp_path, fetch_calls):
    fetch_calls.result = page(ok=False)
    assert pipeline.lead_text(row(), "fetched", tmp_path) == (TYPED, "typed (fetch failed)")
    assert not (tmp_path / "acme-co.json").exists()
