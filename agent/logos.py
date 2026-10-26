"""
Company logos, taken from the company's own website: the icon its homepage declares
(apple-touch-icon first, then the largest icon, then /favicon.ico). No logo service, no
outside lookup. Saved in data/logos/ so the app reads them without the network.

Only picture files are kept (PNG, ICO, JPEG, WebP, GIF). An SVG is skipped: it can carry script,
and the app serves logos from its own address, where that script would run as the app.

Run it:  python -m agent.logos      fetch logos for every stored lead that has none
"""

from __future__ import annotations

import base64
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from agent import store
from agent.fetch import fetch_html, fetch_image, slug

LOGOS = Path(__file__).resolve().parent.parent / "data" / "logos"
EXTENSIONS = {"image/png": "png", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico",
              "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


class _IconLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.icons: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel, href = a.get("rel", "").lower().split(), a.get("href", "")
        if not href or "icon" not in " ".join(rel):
            return
        size = max((int(p) for p in a.get("sizes", "").lower().replace("x", " ").split() if p.isdigit()), default=0)
        rank = 1000 + size if "apple-touch-icon" in rel else size
        self.icons.append((rank, href))


def icon_urls(html: str, base: str) -> list[str]:
    """Icon links the page declares, best first, then the conventional /favicon.ico."""
    parser = _IconLinks()
    parser.feed(html)
    found = [urljoin(base, href) for _, href in sorted(parser.icons, key=lambda i: -i[0])]
    return found + [urljoin(base, "/favicon.ico")]


def logo_path(name: str, logos: Path = LOGOS) -> Path | None:
    """The saved logo, if it is one of the picture types in EXTENSIONS. An SVG saved before they were
    refused is ignored: it can carry script, so it is never shown."""
    kinds = set(EXTENSIONS.values())
    return min((p for p in logos.glob(f"{slug(name)}.*") if p.suffix[1:] in kinds), default=None)


def fetch_logo(name: str, url: str, logos: Path = LOGOS) -> Path | None:
    """The saved logo for a company, or the first icon its homepage gives. None if there is none."""
    if saved := logo_path(name, logos):
        return saved
    if not url:
        return None
    page = fetch_html(url)
    candidates = icon_urls(page.text, page.url) if page.ok else [urljoin(url, "/favicon.ico")]
    for icon in candidates:
        got = fetch_image(icon)
        if got and got[1] in EXTENSIONS:
            data, content_type = got
            logos.mkdir(parents=True, exist_ok=True)
            path = logos / f"{slug(name)}.{EXTENSIONS[content_type]}"
            path.write_bytes(data)
            return path
    return None


def data_uri(name: str, logos: Path = LOGOS) -> str | None:
    """The saved logo as a data URI the app can show without serving files, or None."""
    path = logo_path(name, logos)
    if path is None:
        return None
    mime = next((m for m, ext in EXTENSIONS.items() if ext == path.suffix[1:]), "image/png")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    leads = store.load_all()
    found = 0
    for lead in leads:
        path = fetch_logo(lead.name, lead.url)
        found += path is not None
        print(f"{lead.name}: {path.name if path else 'no logo'}", flush=True)
    print(f"\n{found} of {len(leads)} have a logo in {LOGOS}")


if __name__ == "__main__":
    main()
