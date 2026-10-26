"""Rule logic tests. No model is called: these cover everything code decides."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent import score_lead as sl
from agent.evaluate import _grade

DATA = Path(__file__).resolve().parent.parent / "data"
NAMES = ["industry match", "company size match", "buyer persona fit", "pain match"]

BASE_ICP = {
    "buyer_persona": "Head of Sales",
    "pain_solved": "Manual prospecting",
    "sales_roles": {"definition": "a job that sells", "titles": ["account executive", "sdr"]},
    "company_size_min": 50,
    "company_size_max": 500,
    "geography_countries": ["India", "Singapore", "Vietnam", "Australia"],
    "gates": ["industry match"],
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
        "min_headcount": 10, "max_size_factor": 3, "size_tolerance": 0.1, "unknown_score": 2, "gate_fail_max": 1,
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
    (None, 2), (9, 0), (120, 5), (45, 3), (550, 3), (551, 1), (1500, 1), (1501, 0), (5200, 0),
])
def test_size_criterion(headcount, score):
    assert sl._size_criterion(make_icp(), headcount).score == score


@pytest.mark.parametrize("headcount, reason", [
    (None, ""), (9, "under the minimum headcount"), (120, ""), (1500, ""), (1501, "far over the maximum headcount"),
])
def test_size_fail(headcount, reason):
    assert sl._size_fail(make_icp(), headcount) == reason


def test_max_size_factor_is_required():
    raw = json.loads(json.dumps(BASE_ICP))
    del raw["settings"]["max_size_factor"]
    with pytest.raises(ValidationError):
        sl.ICP.model_validate(raw)


@pytest.mark.parametrize("location, country, in_region", [
    ("Bengaluru, India", "India", "yes"), ("Hanoi, Viet Nam", "Viet Nam", "yes"),
    ("Toronto, Canada", "Canada", "no"), ("Austin, USA", "United States", "no"),
    ("London, UK", "United Kingdom", "no"), ("Indiana, USA", "United States", "no"),
    ("Nowhereville", "", "unknown"), ("", "", "unknown"),
])
def test_locate_from_country(location, country, in_region):
    where = sl.locate(make_icp(), location)
    assert (where.country, where.in_region) == (country, in_region)


@pytest.mark.parametrize("location, in_region", [
    ("Sydney, New South Wales", "yes"), ("Bangalore, Karnataka", "yes"), ("Chennai, Tamil Nadu", "yes"),
    ("Greater Sydney Area", "yes"),
    ("San Francisco, California", "no"), ("Wilmington, Delaware", "no"), ("Toronto, Ontario", "no"),
    ("San Francisco", "unknown"), ("Toronto", "unknown"),
])
def test_locate_from_state_or_city(location, in_region):
    assert sl.locate(make_icp(), location).in_region == in_region


def test_location_evidence_names_the_country_once():
    assert sl.locate(make_icp(), "Bengaluru, India").evidence == "Bengaluru, India: in your ideal-customer region"
    assert sl.locate(make_icp(), "Toronto, Ontario").evidence.endswith("outside your ideal-customer region")


def test_geography_is_not_a_criterion():
    assert "geography match" not in sl.ALL_CRITERIA


def test_city_name_shared_by_countries_goes_to_the_biggest():
    assert sl._place_country("Sydney") == ("AU", False)
    assert sl._place_country("Sydney, New South Wales") == ("AU", True)
    assert sl._place_country("Nowhereville") is None


def test_unknown_score_comes_from_settings():
    icp = make_icp(unknown_score=3)
    assert sl._size_criterion(icp, None).score == 3


COMPANY = sl.Company(name="X", industry="B2B SaaS - CRM", size="80", location="Pune, India",
                     description="Sells a CRM to small businesses. Founders do all the selling.")


def facts(**values):
    base = {name: {"quote": "", "value": "no"} for name in
            ("is_services", "is_marketplace", "is_non_software", "builds_own_software", "sells_sales_tools",
             "non_profit_or_government")}
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


@pytest.mark.parametrize("quote", [
    "b2b saas ‑ crm", "B2B SaaS — CRM", "B2B SaaS – CRM", "b2b saas - crm",
])
def test_quote_match_ignores_dash_and_space_variants(quote):
    checked, _ = sl._check_facts(facts(builds_own_software=("yes", quote)), COMPANY)
    assert checked["builds_own_software"] == "yes"


def test_quote_match_ignores_curly_quote_marks():
    company = COMPANY.model_copy(update={"description": "It's the founders' CRM, built for \"small\" teams."})
    quote = "It’s the founders’ CRM, built for “small” teams"
    checked, rejected = sl._check_facts(facts(sales_motion=("founder_led", quote)), company)
    assert (checked["sales_motion"], rejected) == ("founder_led", [])


def test_a_different_word_still_fails_after_typography_is_unified():
    checked, _ = sl._check_facts(facts(builds_own_software=("yes", "b2b saas ‑ erp")), COMPANY)
    assert checked["builds_own_software"] == "unknown"


def test_quote_shortened_with_dots_is_kept():
    checked, rejected = sl._check_facts(facts(sales_motion=("founder_led", "Sells a CRM... Founders do all")), COMPANY)
    assert checked["sales_motion"] == "founder_led"
    assert rejected == []


def test_quote_pieces_out_of_order_are_rejected():
    checked, _ = sl._check_facts(facts(sales_motion=("founder_led", "Founders do all… Sells a CRM")), COMPANY)
    assert checked["sales_motion"] == "unknown"


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
    assert sl._fit_from_criteria(crit(5, 2, 5, 5), False, make_icp()) == 85


def test_failed_gate_caps_the_fit():
    assert sl._fit_from_criteria(crit(0, 5, 4, 4), False, make_icp()) == 20


def test_hard_fail_caps_the_fit():
    assert sl._fit_from_criteria(crit(5, 5, 5, 5), True, make_icp()) == 20


def test_weights_change_the_average_but_not_the_gate():
    weights = {n: 1 for n in NAMES} | {"industry match": 3}
    assert sl._fit_from_criteria(crit(1, 5, 5, 5), False, make_icp(weights=weights, gates=[])) == 60
    assert sl._fit_from_criteria(crit(1, 5, 5, 5), False, make_icp(weights=weights)) == 20


@pytest.mark.parametrize("fit, hit, verdict", [
    (39, False, "disqualify"), (40, False, "review"), (69, False, "review"),
    (70, False, "qualify"), (95, True, "disqualify"),
])
def test_verdict(fit, hit, verdict):
    assert sl._verdict(fit, hit, make_icp().settings) == verdict


def test_shipped_icp_loads():
    sl.load_icp(DATA / "icp.json")


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
    {"gates": ["geography match"]},
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


def ratings(buyer, pain):
    def rating(score_quote):
        score, quote = score_quote
        return sl.QuotedRating(quote=quote, evidence="e", score=score)
    return sl.FitScores(buyer_persona_fit=rating(buyer), pain_match=rating(pain), summary="")


def test_company_with_no_text_is_not_sent_to_the_model(monkeypatch):
    def no_model(*args, **kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(sl, "generate_structured", no_model)
    result = sl.score_lead(make_icp(), COMPANY.model_copy(update={"description": "  "}))
    assert (result.verdict, result.fit, result.review_flags) == ("review", 0, [sl.NO_TEXT_FLAG])
    assert result.location.country == "India"


@pytest.fixture
def fake_model(monkeypatch):
    """Fake model: `answers` maps a schema to what it returns; `asked` lists the schemas called."""
    class Asked(list):
        temps: dict = {}

    asked, answers, temps = Asked(), {}, {}

    def fake(system, user_prompt, schema, model=None, temperature=None, used=None):
        asked.append(schema)
        temps[schema] = temperature
        if used is not None:
            used.append(answers.get("answered_by", model or sl.model_name()))
        return answers[schema]

    monkeypatch.setattr(sl, "generate_structured", fake)
    asked.temps = temps
    return asked, answers


def test_ruled_out_lead_skips_the_buyer_and_pain_call(fake_model):
    asked, answers = fake_model
    answers[sl.CompanyFacts] = facts(customers=("consumers", "Sells a CRM"))
    result = sl.score_lead(make_icp(), COMPANY)
    assert asked == [sl.CompanyFacts]
    assert (result.verdict, result.disqualifier_reason) == ("disqualify", "B2C")
    assert [c.score for c in result.criteria[2:]] == [2, 2]


def test_company_far_over_the_size_range_is_dropped_without_the_scoring_call(fake_model):
    asked, answers = fake_model
    answers[sl.CompanyFacts] = facts()
    result = sl.score_lead(make_icp(), COMPANY.model_copy(update={"size": "5,200 employees"}))
    assert asked == [sl.CompanyFacts]
    assert result.verdict == "disqualify" and result.fit <= 20
    assert "far over the maximum headcount" in result.summary


def test_failed_gate_skips_the_call_only_when_the_cap_is_a_disqualify(fake_model):
    asked, answers = fake_model
    answers[sl.CompanyFacts] = facts(is_services=("yes", "Sells a CRM"))
    answers[sl.FitScores] = ratings((3, ""), (3, ""))
    assert sl.score_lead(make_icp(), COMPANY).verdict == "disqualify"
    assert asked == [sl.CompanyFacts]
    sl.score_lead(make_icp(fail_cap=50), COMPANY)
    assert asked[1:] == [sl.CompanyFacts, sl.FitScores]


def test_live_lead_gets_both_calls(fake_model):
    asked, answers = fake_model
    answers[sl.CompanyFacts] = facts(builds_own_software=("yes", "Sells a CRM"),
                                     customers=("businesses", "to small businesses"))
    answers[sl.FitScores] = ratings((4, "Founders do all the selling"), (4, "Sells a CRM"))
    result = sl.score_lead(make_icp(), COMPANY)
    assert asked == [sl.CompanyFacts, sl.FitScores]
    assert [c.score for c in result.criteria[2:]] == [4, 4]


def lead_score(fit, verdict, model="main", hit=False):
    return sl.LeadScore(fit=fit, verdict=verdict, criteria=[], disqualifier_hit=hit,
                        disqualifier_reason="", summary="", model=model)


@pytest.fixture
def two_models(monkeypatch):
    """Fake score_lead: `results` maps a model (None = main) to the score it returns."""
    calls, results = [], {}

    def fake(icp, company, model=None):
        calls.append(model)
        if isinstance(results[model], Exception):
            raise results[model]
        return results[model].model_copy()

    monkeypatch.setattr(sl, "score_lead", fake)
    return calls, results


def test_clear_result_gets_no_second_opinion(two_models):
    calls, results = two_models
    results[None] = lead_score(85, "qualify")
    result = sl.score_with_second_opinion(make_icp(), COMPANY)
    assert (calls, result.verdict, result.second_opinion) == ([None], "qualify", None)


def test_borderline_result_with_agreement_keeps_its_verdict(two_models):
    calls, results = two_models
    results[None], results[sl.second_model_name()] = lead_score(55, "review"), lead_score(60, "review", "second")
    result = sl.score_with_second_opinion(make_icp(), COMPANY)
    assert len(calls) == 2
    assert (result.verdict, result.fit, result.second_opinion.fit) == ("review", 55, 60)


def test_disagreement_sends_a_disqualified_lead_to_review(two_models):
    _, results = two_models
    results[None] = lead_score(35, "disqualify")
    results[sl.second_model_name()] = lead_score(50, "review", "second")
    result = sl.score_with_second_opinion(make_icp(), COMPANY)
    assert result.verdict == "review"
    assert "second model disagrees: main disqualify 35, second review 50" in result.review_flags


def test_two_disqualifies_agree_however_far_apart(two_models):
    _, results = two_models
    results[None] = lead_score(20, "disqualify")
    results[sl.second_model_name()] = lead_score(35, "disqualify", "second")
    result = sl.score_with_second_opinion(make_icp(), COMPANY)
    assert (result.verdict, result.review_flags) == ("disqualify", [])


def test_two_reviews_far_apart_still_disagree(two_models):
    _, results = two_models
    results[None] = lead_score(45, "review")
    results[sl.second_model_name()] = lead_score(65, "review", "second")
    assert sl.score_with_second_opinion(make_icp(), COMPANY).verdict == "review"
    assert "second model disagrees" in sl.score_with_second_opinion(make_icp(), COMPANY).review_flags[-1]


def test_failed_second_model_keeps_the_main_score(two_models):
    _, results = two_models
    results[None], results[sl.second_model_name()] = lead_score(55, "review"), RuntimeError("down")
    result = sl.score_with_second_opinion(make_icp(), COMPANY)
    assert (result.verdict, result.second_opinion) == ("review", None)


def test_rating_quoting_our_icp_drops_to_unknown():
    checked, rejected = sl._check_ratings(
        ratings((2, ""), (0, "Sales reps spend hours researching")), COMPANY, 2)
    assert [c.score for c in checked] == [2, 2]
    assert len(rejected) == 1 and "pain match=0" in rejected[0]


def test_rating_with_a_real_quote_is_kept():
    checked, rejected = sl._check_ratings(
        ratings((1, "Founders do all the selling"), (4, "Sells a CRM to small businesses")), COMPANY, 2)
    assert [c.score for c in checked] == [1, 4]
    assert rejected == []


def test_unsupported_high_rating_drops_to_unknown():
    checked, _ = sl._check_ratings(ratings((5, "They hire SDRs"), (2, "")), COMPANY, 2)
    assert checked[0].score == 2


def test_unknown_rating_needs_no_quote():
    checked, rejected = sl._check_ratings(ratings((3, ""), (3, "")), COMPANY, 3)
    assert [c.score for c in checked] == [3, 3]
    assert rejected == []


def test_every_rating_requires_a_quote():
    defs = sl.FitScores.model_json_schema()["$defs"]
    assert "quote" in defs["QuotedRating"]["required"]


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


def test_facts_and_the_judgement_both_run_at_temperature_zero(fake_model):
    asked, answers = fake_model
    answers[sl.CompanyFacts] = facts()
    answers[sl.FitScores] = ratings((4, "Sells a CRM"), (4, "Sells a CRM"))
    sl.score_lead(make_icp(), COMPANY)
    assert asked.temps[sl.CompanyFacts] == 0.0
    assert asked.temps[sl.FitScores] == 0.0


def test_non_profit_or_government_goes_to_review_not_disqualify(fake_model):
    asked, answers = fake_model
    quote = "Sells a CRM"
    answers[sl.CompanyFacts] = facts(builds_own_software=("yes", quote), customers=("businesses", quote),
                                     sales_motion=("outbound_team", quote),
                                     non_profit_or_government=("yes", quote))
    answers[sl.FitScores] = ratings((5, quote), (5, quote))
    icp = make_icp(fact_rules=[{"fact": "non_profit_or_government", "value": "yes",
                                "label": "non-profit or government (check it fits)", "action": "review"}])
    result = sl.score_lead(icp, COMPANY)
    assert result.verdict == "review"
    assert result.review_flags == ["non-profit or government (check it fits)"]
    assert not result.disqualifier_hit


def test_facts_prompt_counts_freelancers_as_businesses_and_asks_about_non_profits():
    assert "Freelancers, sole proprietors and creators" in sl.FACTS_PROMPT
    assert "non_profit_or_government" in sl.FACTS_PROMPT


def test_a_lead_scored_by_the_fallback_model_says_so_and_stays_at_review(fake_model):
    asked, answers = fake_model
    answers[sl.CompanyFacts] = facts()
    answers[sl.FitScores] = ratings((5, "Sells a CRM"), (5, "Sells a CRM"))
    assert sl.score_lead(make_icp(), COMPANY).verdict == "qualify"
    answers["answered_by"] = "stand-in:1b"
    result = sl.score_lead(make_icp(), COMPANY)
    assert result.model == f"stand-in:1b (fallback: {sl.model_name()} failed)"
    assert any("stand-in:1b" in f for f in result.review_flags)
    assert result.verdict != "qualify"
