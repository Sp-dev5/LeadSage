"""Fetcher tests. No real network: requests.get and the robots check are faked."""

from urllib.parse import urlsplit

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
                 content_type: str = "text/html; charset=utf-8", url: str = "https://acme.test/",
                 location: str | None = None):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        if location:
            self.headers["Location"] = location
        self.url = url


@pytest.fixture
def robots_allow(monkeypatch):
    monkeypatch.setattr(fetch, "_allowed_by_robots", lambda url: True)


def redirect(location: str, status: int = 301) -> FakeResponse:
    return FakeResponse(status_code=status, location=location)


class Calls(list):
    """URLs requested, in order; `kwargs` holds the options of the last call."""
    kwargs: dict = {}


def fake_get(monkeypatch, response=None, raises=None, responses=None):
    """One response for every call, or `responses`: a list used in order, one per call."""
    calls = Calls()
    queue = list(responses) if responses is not None else None

    def get(url, **kwargs):
        calls.append(url)
        calls.kwargs = kwargs
        if raises:
            raise raises
        return queue.pop(0) if queue is not None else response

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


def robots_by_host(monkeypatch, blocked: set[str]):
    """Fake robots check that says no to the listed hosts and yes to the rest."""
    monkeypatch.setattr(fetch, "_allowed_by_robots",
                        lambda url: urlsplit(url).netloc not in blocked)


def test_page_download_does_not_auto_follow_redirects(monkeypatch, robots_allow):
    calls = fake_get(monkeypatch, FakeResponse(html_page(BODY)))
    fetch.fetch_page("https://acme.test/")
    assert calls.kwargs["allow_redirects"] is False


def test_redirect_to_allowed_host_is_followed(monkeypatch):
    robots_by_host(monkeypatch, blocked=set())
    calls = fake_get(monkeypatch, responses=[
        redirect("https://www.acme-group.test/home"), FakeResponse(html_page(BODY))])
    page = fetch.fetch_page("https://acme.test/")
    assert page.ok
    assert page.url == "https://www.acme-group.test/home"
    assert calls == ["https://acme.test/", "https://www.acme-group.test/home"]


def test_redirect_to_blocked_host_is_not_downloaded(monkeypatch):
    robots_by_host(monkeypatch, blocked={"private.test"})
    calls = fake_get(monkeypatch, responses=[
        redirect("https://private.test/about"), FakeResponse(html_page(BODY))])
    page = fetch.fetch_page("https://acme.test/")
    assert not page.ok
    assert page.error == "redirect blocked by robots.txt"
    assert calls == ["https://acme.test/"]


def test_relative_redirect_stays_on_the_same_host(monkeypatch):
    robots_by_host(monkeypatch, blocked=set())
    fake_get(monkeypatch, responses=[redirect("/en/home", 302), FakeResponse(html_page(BODY))])
    page = fetch.fetch_page("https://acme.test/")
    assert page.ok
    assert page.url == "https://acme.test/en/home"


def test_redirect_loop_gives_up(monkeypatch):
    robots_by_host(monkeypatch, blocked=set())
    calls = fake_get(monkeypatch, response=redirect("https://acme.test/again"))
    page = fetch.fetch_page("https://acme.test/")
    assert not page.ok
    assert page.error == "too many redirects"
    assert len(calls) == fetch.MAX_REDIRECTS + 1


def test_five_redirects_are_still_followed(monkeypatch):
    robots_by_host(monkeypatch, blocked=set())
    hops = [redirect(f"https://acme.test/{i}") for i in range(fetch.MAX_REDIRECTS)]
    fake_get(monkeypatch, responses=hops + [FakeResponse(html_page(BODY))])
    assert fetch.fetch_page("https://acme.test/").ok
