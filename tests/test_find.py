import json

import pytest

from agent import find, store
from agent.fetch import Page
from agent.score_lead import Company, LeadScore
from tests.test_scoring import make_icp

ICP_ = make_icp()


def yc(name="Acme", size=120, status="Active", website="https://www.acme.io/", **extra):
    return {"name": name, "website": website, "team_size": size, "status": status, "nonprofit": False,
            "all_locations": "Pune, MH, India; Remote", "one_liner": "CRM for small shops",
            "industry": "B2B", "subindustry": "B2B -> Sales", **extra}


NOBODY = (set(), set())


def test_keeps_active_companies_inside_the_icp_size_range():
    lo, hi = ICP_.company_size_min, ICP_.company_size_max
    rows = [yc("In", lo), yc("Small", lo - 1), yc("Big", hi + 1), yc("Gone", status="Inactive"),
            yc("Charity", nonprofit=True), yc("NoSite", website="")]
    assert [c["name"] for c in find.candidates(rows, ICP_, NOBODY)] == ["In"]


def test_any_size_drops_only_the_size_check():
    rows = [yc("Tiny", 3), yc("Gone", 3, status="Inactive")]
    assert [c["name"] for c in find.candidates(rows, ICP_, NOBODY, any_size=True)] == ["Tiny"]


def test_in_region_keeps_any_region_location_and_writes_it():
    rows = [yc("Pune"), yc("Mixed", all_locations="New York, NY, USA; Singapore"),
            yc("Indy", all_locations="Indianapolis, IN, USA"), yc("Sf", all_locations="San Francisco, CA, USA")]
    kept = find.candidates(rows, ICP_, NOBODY, in_region=True)
    assert [(c["name"], find.to_row(c)["location"]) for c in kept] == [
        ("Pune", "Pune, MH, India"), ("Mixed", "Singapore")]
    assert rows[1]["all_locations"].startswith("New York")


def test_hiring_keeps_only_companies_hiring_now():
    rows = [yc("Hires", isHiring=True), yc("Quiet", isHiring=False), yc("Unsaid")]
    assert [c["name"] for c in find.candidates(rows, ICP_, NOBODY, hiring=True)] == ["Hires"]


def test_known_names_and_domains_are_skipped():
    rows = [yc("Acme"), yc("Other", website="https://beta.dev"), yc("Fresh", website="https://fresh.dev")]
    known = ({"acme"}, {"beta.dev"})
    assert [c["name"] for c in find.candidates(rows, ICP_, known)] == ["Fresh"]


def test_pick_is_repeatable_and_drops_duplicate_sites():
    rows = [yc(f"C{i}", website=f"https://c{i}.dev") for i in range(20)] + [yc("Copy", website="https://c1.dev/")]
    first, again = find.pick(rows, 5, seed=7), find.pick(rows, 5, seed=7)
    assert [c["name"] for c in first] == [c["name"] for c in again]
    assert len(find.pick(rows, 50, seed=1)) == 20


def test_row_fills_every_pipeline_column():
    row = find.to_row(yc(one_liner="Advanced manufacturing (for Chinaâ€¦"))
    assert row == {"name": "Acme", "industry": "B2B, Sales", "size": "120 employees", "location": "Pune, MH, India",
                   "url": "https://www.acme.io", "description": "Advanced manufacturing (for China…",
                   "my_label": "", "why": ""}


def test_source_url_names_the_industry_file():
    assert find.source_url("Real Estate and Construction") == \
        "https://yc-oss.github.io/api/industries/real-estate-and-construction.json"


def test_known_leads_reads_the_store_and_lead_csvs(tmp_path):
    db = tmp_path / "leads.db"
    company = Company(name="Stored Co", industry="SaaS", size="80", location="Pune", description="x")
    store.save(company, LeadScore(fit=50, verdict="review", criteria=[], disqualifier_hit=False,
                                  disqualifier_reason="", summary=""), url="https://www.stored.io", text_source="typed", path=db)
    (tmp_path / "test_leads.csv").write_text("name,url\nCsv Co,https://csv.dev\n", encoding="utf-8")
    (tmp_path / "icp.json").write_text("{}", encoding="utf-8")
    names, domains = find.known_leads(db, tmp_path)
    assert names == {"stored co", "csv co"} and domains == {"stored.io", "csv.dev"}


def test_failed_download_says_so(monkeypatch):
    monkeypatch.setattr(find, "fetch_json", lambda url: Page(url=url, fetched_at="t", ok=False, error="HTTP 404"))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        find.download("Nope")


def test_download_reads_the_json(monkeypatch):
    monkeypatch.setattr(find, "fetch_json", lambda url: Page(url=url, text=json.dumps([yc()]), fetched_at="t", ok=True))
    assert find.download("B2B")[0]["name"] == "Acme"
