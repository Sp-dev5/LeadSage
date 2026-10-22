"""Rule logic tests. No model is called: these cover everything code decides."""

import json
from pathlib import Path

import pytest

from agent import score_lead as sl
from agent.evaluate import _grade

DATA = Path(__file__).resolve().parent.parent / "data"
NAMES = ["industry match", "company size match", "geography match", "buyer persona fit", "pain match"]

BASE_ICP = {
    "buyer_persona": "Head of Sales",
    "pain_solved": "Manual prospecting",
    "sales_roles": {"definition": "a job that sells", "titles": ["account executive", "sdr"]},
    "company_size_min": 50,
    "company_size_max": 500,
    "geography_countries": ["India", "Singapore", "Vietnam", "Australia"],
    "gates": ["industry match", "geography match"],
    "industry_map": [
        {"offering": "own_software", "customers": "businesses", "score": 5},
        {"offering": "own_software", "customers": "mixed", "score": 3},
        {"offering": "services", "score": 0},
    ],
    "industry_default": 2,
    "fact_rules": [
        {"fact": "sales_motion", "value": "no_sales", "label": "self-serve", "action": "review"},
        {"fact": "customers", "value": "consumers", "label": "B2C", "action": "disqualify"},
    ],
    "settings": {
        "min_headcount": 10, "size_tolerance": 0.1, "unknown_score": 2, "gate_fail_max": 1,
        "fail_cap": 20, "disqualify_below": 40, "qualify_at": 70, "disagree_gap": 15,
        "weights": {n: 1 for n in NAMES},
    },
}


def make_icp(**changes) -> sl.ICP:
    raw = json.loads(json.dumps(BASE_ICP))
    for key, value in changes.items():
        if key in raw["settings"]:
            raw["settings"][key] = value
        else:
            raw[key] = value
    return sl.ICP.model_validate(raw)


def crit(*scores) -> list[sl.CriterionScore]:
    return [sl.CriterionScore(criterion=n, score=s, evidence="") for n, s in zip(NAMES, scores)]


@pytest.mark.parametrize("text, expected", [
    ("120 employees", 120), ("1,200 employees", 1200), ("1.5k staff", 1500),
    ("about 75 people", 75), ("200-250 employees", 200), ("unknown", None),
])
def test_parse_headcount(text, expected):
    assert sl._parse_headcount(text) == expected


@pytest.mark.parametrize("headcount, score", [
    (None, 2), (9, 0), (120, 5), (45, 3), (550, 3), (551, 1), (5200, 1),
])
def test_size_criterion(headcount, score):
    assert sl._size_criterion(make_icp(), headcount).score == score


@pytest.mark.parametrize("location, score", [
    ("Bengaluru, India", 5), ("Hanoi, Viet Nam", 5), ("Toronto, Canada", 1),
    ("Austin, USA", 1), ("London, UK", 1), ("Indiana, USA", 1), ("Nowhereville", 2), ("", 2),
])
def test_geography_criterion(location, score):
    assert sl._geography_criterion(make_icp(), location).score == score


@pytest.mark.parametrize("location, score", [
    ("Sydney, New South Wales", 5), ("Bangalore, Karnataka", 5), ("Chennai, Tamil Nadu", 5),
    ("Greater Sydney Area", 5),
    ("San Francisco, California", 1), ("Wilmington, Delaware", 1), ("Toronto, Ontario", 1),
    ("San Francisco", 2), ("Toronto", 2),
])
def test_geography_from_state_or_city(location, score):
    assert sl._geography_criterion(make_icp(), location).score == score


def test_city_name_shared_by_countries_goes_to_the_biggest():
    assert sl._place_country("Sydney") == ("AU", False)
    assert sl._place_country("Sydney, New South Wales") == ("AU", True)
    assert sl._place_country("Nowhereville") is None


def test_unknown_score_comes_from_settings():
    icp = make_icp(unknown_score=3)
    assert sl._size_criterion(icp, None).score == 3
    assert sl._geography_criterion(icp, "Nowhereville").score == 3


COMPANY = sl.Company(name="X", industry="B2B SaaS - CRM", size="80", location="Pune, India",
                     description="Sells a CRM to small businesses. Founders do all the selling.")


def facts(**values):
    base = {name: {"quote": "", "value": "no"} for name in
            ("is_services", "is_marketplace", "is_non_software", "builds_own_software", "sells_sales_tools")}
    base.update(customers={"quote": "", "value": "unknown"},
                sales_motion={"quote": "", "value": "unknown"},
                primary_business={"quote": "", "value": "unclear"})
    for name, (value, quote) in values.items():
        base[name] = {"quote": quote, "value": value}
    return sl.CompanyFacts(what_they_sell="CRM", **base)


def test_quote_in_text_is_kept():
    checked, rejected = sl._check_facts(
        facts(builds_own_software=("yes", "Sells a CRM"), customers=("businesses", "to small businesses")),
        COMPANY)
    assert checked["builds_own_software"] == "yes"
    assert checked["customers"] == "businesses"
    assert checked["offering"] == "own_software"
    assert rejected == []


def test_quote_not_in_text_drops_to_unknown():
    checked, rejected = sl._check_facts(facts(sales_motion=("no_sales", "they have no sales team")), COMPANY)
    assert checked["sales_motion"] == "unknown"
    assert len(rejected) == 1


def test_quote_match_ignores_case_and_dash_style():
    checked, _ = sl._check_facts(facts(builds_own_software=("yes", "b2b saas — crm")), COMPANY)
    assert checked["builds_own_software"] == "yes"


ROLE = sl.SalesRole(title="Account Executive", match="exact",
                    source="https://api.ashbyhq.com/posting-api/job-board/x", fetched_at="2026-10-23T00:00:00+00:00")


def test_sales_roles_fill_an_unknown_sales_motion():
    hiring = COMPANY.model_copy(update={"sales_roles": [ROLE]})
    checked, _ = sl._check_facts(facts(), hiring)
    assert checked["sales_motion"] == "sales_team_unclear"


def test_sales_roles_never_override_what_the_text_says():
    hiring = COMPANY.model_copy(update={"sales_roles": [ROLE]})
    checked, _ = sl._check_facts(facts(sales_motion=("founder_led", "Founders do all the selling")), hiring)
    assert checked["sales_motion"] == "founder_led"


def test_no_sales_roles_leaves_sales_motion_unknown():
    checked, _ = sl._check_facts(facts(), COMPANY)
    assert checked["sales_motion"] == "unknown"


def test_rejected_primary_business_falls_back_to_unclear():
    checked, _ = sl._check_facts(facts(primary_business=("software", "invented quote")), COMPANY)
    assert checked["primary_business"] == "unclear"


@pytest.mark.parametrize("answers, offering", [
    ({"is_services": "yes"}, "services"),
    ({"is_services": "yes", "is_marketplace": "yes"}, "other_unclear"),
    ({"builds_own_software": "yes"}, "own_software"),
    ({}, "unknown"),
    ({"is_services": "yes", "builds_own_software": "yes", "primary_business": "software"}, "own_software"),
    ({"is_services": "yes", "builds_own_software": "yes", "primary_business": "services"}, "services"),
    ({"is_services": "yes", "builds_own_software": "yes", "primary_business": "unclear"}, "other_unclear"),
])
def test_derive_offering(answers, offering):
    base = {"is_services": "no", "is_marketplace": "no", "is_non_software": "no",
            "builds_own_software": "no", "primary_business": "unclear"}
    assert sl._derive_offering({**base, **answers}) == offering


@pytest.mark.parametrize("offering, customers, score", [
    ("own_software", "businesses", 5), ("own_software", "mixed", 3), ("services", "businesses", 0),
    ("unknown", "unknown", 2),
])
def test_industry_criterion(offering, customers, score):
    f = {"offering": offering, "customers": customers, "what_they_sell": "x"}
    assert sl._industry_criterion(make_icp(), f).score == score


def test_apply_rules_splits_disqualify_and_review():
    icp = make_icp()
    assert sl._apply_rules(icp, {"customers": "consumers", "sales_motion": "no_sales"}) == (["B2C"], ["self-serve"])
    assert sl._apply_rules(icp, {"customers": "businesses", "sales_motion": "founder_led"}) == ([], [])


def test_fit_is_the_average_when_nothing_fails():
    assert sl._fit_from_criteria(crit(5, 5, 2, 5, 5), False, make_icp()) == 88


def test_failed_gate_caps_the_fit():
    assert sl._fit_from_criteria(crit(0, 5, 5, 4, 4), False, make_icp()) == 20
    assert sl._fit_from_criteria(crit(5, 5, 1, 5, 5), False, make_icp()) == 20


def test_hard_fail_caps_the_fit():
    assert sl._fit_from_criteria(crit(5, 5, 5, 5, 5), True, make_icp()) == 20


def test_weights_change_the_average_but_not_the_gate():
    weights = {n: 1 for n in NAMES} | {"industry match": 3}
    assert sl._fit_from_criteria(crit(5, 5, 1, 5, 5), False, make_icp(weights=weights, gates=[])) == 89
    assert sl._fit_from_criteria(crit(5, 5, 1, 5, 5), False, make_icp(weights=weights)) == 20


@pytest.mark.parametrize("fit, hit, verdict", [
    (39, False, "disqualify"), (40, False, "review"), (69, False, "review"),
    (70, False, "qualify"), (95, True, "disqualify"),
])
def test_verdict(fit, hit, verdict):
    assert sl._verdict(fit, hit, make_icp().settings) == verdict


@pytest.mark.parametrize("name", ["icp.json", "icp_geo_soft.json"])
def test_shipped_icps_load(name):
    sl.load_icp(DATA / name)


def _load(tmp_path, raw):
    path = tmp_path / "icp.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return sl.load_icp(path)


def test_missing_setting_is_named(tmp_path):
    raw = json.loads(json.dumps(BASE_ICP))
    del raw["settings"]["min_headcount"]
    with pytest.raises(ValueError, match="min_headcount.*hard fail"):
        _load(tmp_path, raw)


@pytest.mark.parametrize("change", [
    {"disqualifiers": ["fewer than 10 employees"]},
    {"fact_rules": [{"fact": "customers", "value": "government", "label": "x"}]},
    {"fact_rules": [{"fact": "salez", "value": "yes", "label": "x"}]},
    {"gates": ["industry"]},
    {"industry_map": [{"offering": "saas", "score": 5}]},
])
def test_dead_config_is_rejected(tmp_path, change):
    with pytest.raises(ValueError):
        _load(tmp_path, {**BASE_ICP, **change})


def test_bad_weights_are_rejected(tmp_path):
    raw = json.loads(json.dumps(BASE_ICP))
    del raw["settings"]["weights"]["pain match"]
    with pytest.raises(ValueError, match="weights"):
        _load(tmp_path, raw)


def test_every_fact_requires_a_quote():
    defs = sl.CompanyFacts.model_json_schema()["$defs"]
    assert all("quote" in d.get("required", []) for d in defs.values())


def test_buyer_and_pain_are_required():
    required = sl.FitScores.model_json_schema()["required"]
    assert {"buyer_persona_fit", "pain_match"} <= set(required)


def test_fact_values_match_the_schema():
    for name, field in sl.CompanyFacts.model_fields.items():
        if name == "what_they_sell":
            continue
        value_type = field.annotation.model_fields["value"].annotation
        assert sl.FACT_VALUES[name] == sl.get_args(value_type), name


@pytest.mark.parametrize("label, verdict, grade", [
    ("qualify", "qualify", "OK"), ("qualify", "review", "near"),
    ("qualify", "disqualify", "MISS"), ("qualify", "error", "ERR"),
])
def test_grade(label, verdict, grade):
    assert _grade(label, verdict) == grade
