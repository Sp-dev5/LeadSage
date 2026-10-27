"""Batch order and saving. No model, no network."""

from agent import pipeline, store
from agent.score_lead import Company, LeadScore
from tests.test_scoring import make_icp


def lead_score(fit, verdict, model):
    return LeadScore(fit=fit, verdict=verdict, criteria=[], disqualifier_hit=False,
                     disqualifier_reason="", summary="", model=model)


def test_a_csv_saved_by_excel_is_still_read(tmp_path, monkeypatch):
    header = "name,industry,size,location,url,description,my_label,why\r\n"
    (tmp_path / "excel_leads.csv").write_bytes(("﻿" + header + "Acme,SaaS,120,Pune,https://acme.dev,CRM,,\r\n")
                                               .encode("utf-8"))
    monkeypatch.setattr(pipeline, "DATA", tmp_path)
    assert pipeline.lead_files() == ["excel_leads.csv"]
    assert pipeline.read_leads("excel_leads.csv")[0]["name"] == "Acme"


def test_a_very_long_typed_description_is_cut_like_a_page():
    text, source = pipeline.lead_text({"name": "Long", "description": "CRM software. " * 10_000}, "typed")
    assert source == "typed" and len(text) == pipeline.MAX_CHARS


def test_main_model_scores_every_lead_before_the_second_model_runs(tmp_path, monkeypatch):
    main = {"Clear": lead_score(85, "qualify", "main"), "Edge": lead_score(55, "review", "main"),
            "Dropped": lead_score(30, "disqualify", "main")}
    calls = []

    def research(row, icp, signals):
        return Company(name=row["name"], industry="", size="", location="", description="x"), "typed"

    def score_lead(icp, company, model=None):
        calls.append((company.name, model or "main"))
        return main[company.name] if model is None else lead_score(60, "review", model)

    monkeypatch.setattr(pipeline, "research", research)
    monkeypatch.setattr(pipeline, "score_lead", score_lead)
    db = tmp_path / "leads.db"
    rows = [{"name": n, "url": ""} for n in main]
    pipeline.score_all(rows, make_icp(), {}, report=lambda msg: None, db=db)

    second = pipeline.second_model_name()
    assert calls == [("Clear", "main"), ("Edge", "main"), ("Dropped", "main"),
                     ("Edge", second), ("Dropped", second)]
    saved = {lead.name: lead.score for lead in store.load_all(db)}
    assert saved["Clear"].second_opinion is None
    assert (saved["Edge"].verdict, saved["Edge"].second_opinion.fit) == ("review", 60)
    assert saved["Dropped"].verdict == "review"
