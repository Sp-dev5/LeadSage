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


def test_json_api_returns_raw_json(monkeypatch, robots_allow):
    response = FakeResponse(content_type="application/json")
    response.text = '{"jobs": []}'
    fake_get(monkeypatch, response)
    page = fetch.fetch_json("https://acme.test/api")
    assert page.ok and page.text == '{"jobs": []}'


def test_json_fetch_refuses_html(monkeypatch, robots_allow):
    fake_get(monkeypatch, FakeResponse(html_page(BODY)))
    page = fetch.fetch_json("https://acme.test/api")
    assert not page.ok and page.error.startswith("not JSON")


def test_documented_public_api_skips_robots(monkeypatch):
    monkeypatch.setattr(fetch, "_allowed_by_robots", lambda url: False)
    response = FakeResponse(b"", content_type="application/json")
    response.text = "{}"
    fake_get(monkeypatch, response)
    assert fetch.fetch_json("https://api.ashbyhq.com/posting-api/job-board/acme").ok
    assert not fetch.fetch_json("https://other-api.test/jobs").ok


def test_load_or_fetch_saves_only_good_pages(tmp_path):
    good = fetch.Page(url="u", text="hi", fetched_at="t", ok=True)
    bad = fetch.Page(url="u", fetched_at="t", ok=False, error="HTTP 500")
    assert not fetch.load_or_fetch("u", tmp_path / "a.json", lambda url: bad).ok
    assert not (tmp_path / "a.json").exists()
    assert fetch.load_or_fetch("u", tmp_path / "a.json", lambda url: good).text == "hi"
    assert fetch.load_or_fetch("u", tmp_path / "a.json", lambda url: bad).text == "hi"


def test_five_redirects_are_still_followed(monkeypatch):
    robots_by_host(monkeypatch, blocked=set())
    hops = [redirect(f"https://acme.test/{i}") for i in range(fetch.MAX_REDIRECTS)]
    fake_get(monkeypatch, responses=hops + [FakeResponse(html_page(BODY))])
    assert fetch.fetch_page("https://acme.test/").ok


def test_html_fetch_keeps_links_and_markup(monkeypatch, robots_allow):
    response = FakeResponse(html_page(BODY))
    response.text = html_page(BODY).decode()
    fake_get(monkeypatch, response)
    page = fetch.fetch_html("https://acme.test/")
    assert page.ok and "<nav>" in page.text


HOME_LINKS = b"""<html><body><nav>
<a href="/company/about-us">About</a><a href="https://www.acme.test/our-team/">Team</a>
<a href="/pricing">Pricing</a><a href="https://other.test/about">Elsewhere</a>
<a href="/press-kit.pdf">Press kit</a><a href="/newsroom">News</a><a href="/company/about-us/">Again</a>
<a href="mailto:hi@acme.test">Mail</a></nav></body></html>"""


def test_site_links_keeps_same_site_pages_that_name_the_words(monkeypatch, robots_allow):
    page = FakeResponse(HOME_LINKS, url="https://acme.test/")
    page.text = HOME_LINKS.decode()
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: page)
    assert fetch.site_links("https://acme.test/", ("about", "team")) == ["/company/about-us", "/our-team"]
    assert fetch.site_links("https://acme.test/", ("news", "press")) == ["/newsroom"]


def test_site_links_gives_nothing_when_the_homepage_cannot_be_read(monkeypatch):
    monkeypatch.setattr(fetch, "_allowed_by_robots", lambda url: False)
    assert fetch.site_links("https://acme.test/", ("about",)) == []


def test_a_rate_limited_page_is_tried_once_more_after_a_wait(monkeypatch, robots_allow):
    replies = [FakeResponse(status_code=429), FakeResponse(html_page(BODY))]
    replies[0].headers["Retry-After"] = "120"
    waits = []
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(fetch.time, "sleep", waits.append)
    assert fetch.fetch_page("https://acme.test/about").ok
    assert waits == [fetch.RATE_LIMIT_WAIT]


def test_a_page_still_rate_limited_after_the_wait_is_reported(monkeypatch, robots_allow):
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: FakeResponse(status_code=429))
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    page = fetch.fetch_page("https://acme.test/about")
    assert not page.ok and page.error == "HTTP 429"
