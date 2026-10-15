"""Fetcher tests. No real network: requests.get and the robots check are faked."""

import pytest
import requests

from agent import fetch

BODY = ("Acme builds billing software for mid-sized logistics companies. "
        "Our platform automates invoicing, payment reminders and reconciliation "
        "so finance teams close the month in days, not weeks.")
MENU = "Home Pricing Careers Login"


def html_page(body: str) -> bytes:
    return f"""<html><head><title>Acme</title></head><body>
    <nav><ul><li>Home</li><li>Pricing</li><li>Careers</li><li>Login</li></ul></nav>
    <main><article><h1>About Acme</h1><p>{body}</p><p>{body}</p></article></main>
    <footer>Copyright Acme. Privacy policy. Cookie settings.</footer>
    </body></html>""".encode()


class FakeResponse:
    def __init__(self, content: bytes = b"", status_code: int = 200,
                 content_type: str = "text/html; charset=utf-8", url: str = "https://acme.test/"):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.url = url


@pytest.fixture
def robots_allow(monkeypatch):
    monkeypatch.setattr(fetch, "_allowed_by_robots", lambda url: True)


def fake_get(monkeypatch, response=None, raises=None):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if raises:
            raise raises
        return response

    monkeypatch.setattr(fetch.requests, "get", get)
    return calls


def test_good_page_keeps_body_drops_menu(monkeypatch, robots_allow):
    fake_get(monkeypatch, FakeResponse(html_page(BODY)))
    page = fetch.fetch_page("https://acme.test/")
    assert page.ok and page.error == ""
    assert "billing software for mid-sized logistics companies" in page.text
    assert MENU not in page.text
    assert page.fetched_at.endswith("+00:00")


def test_timeout_returns_not_ok(monkeypatch, robots_allow):
    fake_get(monkeypatch, raises=requests.Timeout("slow"))
    page = fetch.fetch_page("https://acme.test/")
    assert not page.ok
    assert "timed out" in page.error


def test_dead_site_is_not_reported_as_blocked(monkeypatch):
    fake_get(monkeypatch, raises=requests.ConnectionError("no such domain"))
    page = fetch.fetch_page("https://no-such-company.test/")
    assert not page.ok
    assert "could not connect" in page.error


def test_404_returns_not_ok(monkeypatch, robots_allow):
    fake_get(monkeypatch, FakeResponse(b"Not found", status_code=404))
    page = fetch.fetch_page("https://acme.test/missing")
    assert not page.ok
    assert page.error == "HTTP 404"


def test_robots_no_means_no_download(monkeypatch):
    monkeypatch.setattr(fetch, "_allowed_by_robots", lambda url: False)
    calls = fake_get(monkeypatch, FakeResponse(html_page(BODY)))
    page = fetch.fetch_page("https://acme.test/")
    assert not page.ok
    assert page.error == "blocked by robots.txt"
    assert calls == []


def test_long_page_is_capped(monkeypatch, robots_allow):
    fake_get(monkeypatch, FakeResponse(html_page(BODY * 200)))
    page = fetch.fetch_page("https://acme.test/")
    assert page.ok
    assert len(page.text) <= fetch.MAX_CHARS


def test_not_html_returns_not_ok(monkeypatch, robots_allow):
    fake_get(monkeypatch, FakeResponse(b"%PDF-1.7", content_type="application/pdf"))
    page = fetch.fetch_page("https://acme.test/brochure.pdf")
    assert not page.ok
    assert page.error.startswith("not HTML")


@pytest.mark.parametrize("robots_txt, path, allowed", [
    ("User-agent: *\nDisallow: /", "/", False),
    ("User-agent: *\nDisallow: /private", "/about", True),
    ("User-agent: LeadSage\nDisallow: /", "/", False),
])
def test_robots_rules_are_read(monkeypatch, robots_txt, path, allowed):
    response = FakeResponse(robots_txt.encode(), content_type="text/plain")
    response.text = robots_txt
    fake_get(monkeypatch, response)
    assert fetch._allowed_by_robots(f"https://acme.test{path}") is allowed


@pytest.mark.parametrize("status, allowed", [(404, True), (403, False), (503, False)])
def test_robots_status_codes(monkeypatch, status, allowed):
    fake_get(monkeypatch, FakeResponse(status_code=status))
    assert fetch._allowed_by_robots("https://acme.test/") is allowed
