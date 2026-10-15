"""Careers-page probe. No network: fetch_page is faked."""

import pytest

from agent import fetch_careers as fc
from agent.fetch import Page


def page(text: str = "", ok: bool = True, error: str = "") -> Page:
    return Page(url="https://jobs.lever.co/acme", text=text,
                fetched_at="2026-10-15T12:00:00+00:00", ok=ok, error=error)


@pytest.fixture
def fake_fetch(monkeypatch):
    calls = []

    def install(result: Page):
        def fake(url):
            calls.append(url)
            return result
        monkeypatch.setattr(fc, "fetch_page", fake)
        return calls

    return install


@pytest.mark.parametrize("text, words", [
    ("We're hiring an Account Executive in Bengaluru", ["account executive"]),
    ("Open roles: SDR, Revenue Operations Lead", ["sdr", "revenue"]),
    ("Senior Backend Engineer, Product Designer", []),
    ("", []),
])
def test_sales_words_in(text, words):
    assert fc.sales_words_in(text) == words


def test_no_careers_url_skips_fetch(tmp_path, fake_fetch):
    calls = fake_fetch(page("Sales"))
    r = fc.probe_row({"name": "Acme", "careers_url": ""}, tmp_path)
    assert not r["ok"] and r["error"] == "no careers_url"
    assert calls == []


def test_readable_page_is_cached_with_careers_suffix(tmp_path, fake_fetch):
    fake_fetch(page("Account Executive, Mumbai"))
    r = fc.probe_row({"name": "Acme Co.", "careers_url": "https://acme.test/careers"}, tmp_path)
    assert r["ok"] and r["words"] == ["account executive"]
    cached = tmp_path / "acme-co-careers.json"
    assert Page.model_validate_json(cached.read_text(encoding="utf-8")).text == "Account Executive, Mumbai"


def test_failed_page_is_not_cached(tmp_path, fake_fetch):
    fake_fetch(page(ok=False, error="no readable text"))
    r = fc.probe_row({"name": "Acme", "careers_url": "https://acme.test/careers"}, tmp_path)
    assert not r["ok"] and r["error"] == "no readable text"
    assert list(tmp_path.iterdir()) == []


def result(url: str = "u", ok: bool = True, words: tuple = ("sales",)) -> dict:
    return {"url": url, "ok": ok, "words": list(words)}


@pytest.mark.parametrize("results, expected", [
    ([result(), result(), result(ok=False), result(words=())], (2, 4, True)),
    ([result(), result(ok=False), result(words=())], (1, 3, False)),
    ([result(), result(url=""), result(url="")], (1, 1, True)),
    ([result(url="")], (0, 0, False)),
])
def test_gate_needs_half_of_pages_with_a_link(results, expected):
    assert fc.gate(results) == expected
