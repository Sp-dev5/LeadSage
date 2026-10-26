"""Logos from the company's own homepage. No network: fetches are faked."""

from agent import logos
from agent.fetch import Page

HTML = """<html><head>
<link rel="icon" href="/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple.png" sizes="180x180">
<link rel="stylesheet" href="/site.css">
<link rel="shortcut icon" href="https://cdn.acme.example/icon-16.png" sizes="16x16">
</head></html>"""


def test_icon_urls_best_first_then_favicon():
    assert logos.icon_urls(HTML, "https://acme.example/") == [
        "https://acme.example/apple.png",
        "https://acme.example/favicon-32.png",
        "https://cdn.acme.example/icon-16.png",
        "https://acme.example/favicon.ico",
    ]


def test_no_declared_icon_falls_back_to_favicon():
    assert logos.icon_urls("<html></html>", "https://acme.example/about") == ["https://acme.example/favicon.ico"]


def test_fetch_logo_saves_the_first_image_and_reuses_it(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(logos, "fetch_html", lambda url: Page(url=url, text=HTML, fetched_at="t", ok=True))

    def image(url):
        calls.append(url)
        return (b"png-bytes", "image/png") if url.endswith("favicon-32.png") else None

    monkeypatch.setattr(logos, "fetch_image", image)
    path = logos.fetch_logo("Acme Co.", "https://acme.example/", tmp_path)
    assert path.name == "acme-co.png" and path.read_bytes() == b"png-bytes"
    assert calls == ["https://acme.example/apple.png", "https://acme.example/favicon-32.png"]
    assert logos.fetch_logo("Acme Co.", "https://acme.example/", tmp_path) == path
    assert len(calls) == 2


def test_no_logo_when_nothing_is_an_image(monkeypatch, tmp_path):
    monkeypatch.setattr(logos, "fetch_html", lambda url: Page(url=url, fetched_at="t", ok=False, error="x"))
    monkeypatch.setattr(logos, "fetch_image", lambda url: None)
    assert logos.fetch_logo("Acme", "https://acme.example/", tmp_path) is None
    assert logos.data_uri("Acme", tmp_path) is None


def test_data_uri(tmp_path):
    (tmp_path / "acme.png").write_bytes(b"png")
    assert logos.data_uri("Acme", tmp_path) == "data:image/png;base64,cG5n"


def test_an_svg_icon_is_skipped_for_the_next_picture(monkeypatch, tmp_path):
    monkeypatch.setattr(logos, "fetch_html", lambda url: Page(url=url, text=HTML, fetched_at="t", ok=True))
    replies = {"https://acme.example/apple.png": (b"<svg onload='x()'/>", "image/svg+xml"),
               "https://acme.example/favicon-32.png": (b"png-bytes", "image/png")}
    monkeypatch.setattr(logos, "fetch_image", replies.get)
    path = logos.fetch_logo("Acme", "https://acme.example/", tmp_path)
    assert path.name == "acme.png"
    assert [p.name for p in tmp_path.iterdir()] == ["acme.png"]


def test_an_unknown_picture_type_is_not_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(logos, "fetch_html", lambda url: Page(url=url, text="", fetched_at="t", ok=True))
    monkeypatch.setattr(logos, "fetch_image", lambda url: (b"?", "image/x-something"))
    assert logos.fetch_logo("Acme", "https://acme.example/", tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_an_svg_saved_earlier_is_never_shown(tmp_path):
    (tmp_path / "acme.svg").write_bytes(b"<svg onload='x()'/>")
    assert logos.logo_path("Acme", tmp_path) is None
    assert logos.data_uri("Acme", tmp_path) is None
