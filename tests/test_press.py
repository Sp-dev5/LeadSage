"""Funding news: reading saved feed shapes, the strict name match, the opening check. No network."""

from datetime import date

import pytest

from agent import press
from agent.fetch import Page

TODAY = date(2026, 10, 31)
RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<title>Inc42 Media</title>
<item><title>CapGrid Secures ₹50 Cr Funding To Deepen Its Manufacturing Play</title>
<link>https://inc42.com/buzz/capgrid/</link><pubDate>Sat, 31 Oct 2026 13:46:37 +0000</pubDate>
<description><![CDATA[SUMMARY TEXT]]></description><content:encoded><![CDATA[<p>FULL BODY</p>]]></content:encoded></item>
<item><title>Old Co raises $1 Mn</title><link>https://inc42.com/buzz/old/</link>
<pubDate>Sat, 03 Jan 2026 13:46:37 +0000</pubDate></item>
<item><title>No date raises $1 Mn</title><link>https://inc42.com/buzz/nodate/</link></item>
</channel></rss>"""
SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type="xml" href="https://entrackr.com/main-sitemap.xsl"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<url><loc>https://entrackr.com/news/qiroko-raises</loc><news:news>
<news:publication_date>2026-10-30T13:58:53+05:30</news:publication_date>
<news:title>Qiroko raises Rs 1.5 Cr in pre-seed round led by ISV Capital</news:title>
</news:news></url></urlset>"""
BOMB = """<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>
<rss><channel><item><title>&b;</title><link>https://x.test/</link></item></channel></rss>"""


def page(text: str = "", ok: bool = True, error: str = "") -> Page:
    return Page(url="https://feed.test/", text=text, fetched_at="2026-10-31T00:00:00+00:00", ok=ok, error=error)


def test_rss_keeps_headline_link_and_date_only():
    items = press.parse_feed("Inc42", page(RSS), 90, TODAY)
    assert [i.headline for i in items] == ["CapGrid Secures ₹50 Cr Funding To Deepen Its Manufacturing Play"]
    item = items[0]
    assert (item.source, item.date, item.date_text, item.outlet, item.kind) == (
        "https://inc42.com/buzz/capgrid/", "2026-10-31", "31 October 2026", "Inc42", "funding")
    assert "SUMMARY" not in item.model_dump_json() and "FULL BODY" not in item.model_dump_json()


def test_news_sitemap_is_read():
    items = press.parse_feed("Entrackr", page(SITEMAP), 90, TODAY)
    assert [(i.headline[:6], i.date, i.source) for i in items] == [
        ("Qiroko", "2026-10-30", "https://entrackr.com/news/qiroko-raises")]


def test_a_link_that_is_not_a_web_address_is_dropped():
    text = RSS.replace("https://inc42.com/buzz/capgrid/", "javascript:alert(1)")
    assert press.parse_feed("Inc42", page(text), 90, TODAY) == []


def test_a_feed_with_no_items_is_a_failure_not_a_quiet_day():
    with pytest.raises(ValueError):
        press.parse_feed("Inc42", page("<rss><channel><title>x</title></channel></rss>"), 90, TODAY)


def test_read_feeds_reports_each_bad_feed_and_keeps_the_good_one():
    answers = {press.FEEDS[0].url: page(RSS), press.FEEDS[1].url: page(ok=False, error="blocked by robots.txt"),
               press.FEEDS[2].url: page("<rss><broken")}
    items, failed = press.read_feeds(90, TODAY, fetch=answers.__getitem__)
    assert len(items) == 1
    assert failed[0] == "YourStory: blocked by robots.txt" and failed[1].startswith("Entrackr: ")


def test_entity_tricks_in_a_feed_are_refused():
    _, failed = press.read_feeds(90, TODAY, fetch=lambda url: page(BOMB))
    assert len(failed) == 3


@pytest.mark.parametrize("name, headline", [
    ("CapGrid", "CapGrid Secures ₹50 Cr Funding To Deepen Its Manufacturing Play"),
    ("Qiroko", "Qiroko raises Rs 1.5 Cr in pre-seed round led by ISV Capital"),
    ("Tensrai", "Physical AI startup Tensrai raises Rs 3.4 Cr in pre-seed round"),
    ("Venttup", "Venttup raises $3 Mn in pre Series A round led by Rainmatter"),
    ("Ace Blend", "Nutrition startup Ace Blend raises $9.91 Mn led by 3one4 Capital"),
    ("ISLAND Beauty", "ISLAND Beauty raises Rs 2.58 Cr from AJ VC to expand skincare range"),
    ("acme labs", "Acme Labs Bags Series B Funding"),
    ("Acme Labs", "Acme Labs raises fresh $5 Mn"),
    ("Acme Labs", "Acme Labs closes $5 Mn round"),
])
def test_real_rounds_match(name, headline):
    assert press.matches(name, headline)


@pytest.mark.parametrize("name, headline", [
    ("Delhivery", "Delhivery Posts ₹41 Cr Profit In Q2, Revenue Crosses ₹3,000 Cr Mark"),
    ("Yulu", "Yulu reports Rs 281 Cr revenue and Rs 97 Cr loss in FY26"),
    ("upliance.ai", "Exclusive: upliance.ai In Talks To Raise Up To $4 Mn For R&D"),
    ("Cent", "From Cent to Matiks — Indian Startups Raised Over $132 Mn This Week"),
    ("Cashfree", "Cashfree-backed Foo raises $3 Mn"),
    ("Foo", "Bar-Foo raises $3 Mn"),
    ("Pay", "Paytm raises $3 Mn"),
    ("Tm", "Paytm raises $3 Mn"),
    ("Aspire", "Aspire Systems raises $3 Mn"),
    ("Acme", "Acme raises prices for enterprise plans"),
    ("Acme", "Acme closes its Bengaluru office"),
    ("Acme", "Acme bags 3 awards at TechSparks"),
    ("Acme", "Acme raises concerns over Rs 50 Cr tax demand"),
    ("Acme", "Acme secures SOC 2 certification"),
])
def test_other_stories_do_not_match(name, headline):
    assert not press.matches(name, headline)


def test_only_one_word_names_need_the_opening():
    assert press.needs_opening("Fyle") and not press.needs_opening("Cashfree Payments")


@pytest.mark.parametrize("text, url, location, expected", [
    ("Pune-based Fyle raised money", "https://fyle.example", "Pune, India", True),
    ("pune-based fyle raised money", "https://fyle.example", "Pune, India", True),
    ("Read more at fyle.example today", "https://www.fyle.example/about", "Pune, India", True),
    ("Mumbai-based Fyle raised money", "https://fyle.example", "Pune, India", False),
    ("The Punekar brand raised money", "https://fyle.example", "Pune, India", False),
    ("An Indian startup raised money", "https://fyle.example", "India", False),
    ("A remote team raised money", "https://fyle.example", "Remote", False),
    ("Anything at all", "", "", False),
    ("x" * press.OPENING_CHARS + " Pune", "https://fyle.example", "Pune, India", False),
])
def test_opening_check(text, url, location, expected):
    assert press.opening_confirms(text, url, location) is expected
