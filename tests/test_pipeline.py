"""Batch order and saving. No model, no network."""

from agent import pipeline, store
from agent.score_lead import Company, LeadScore
from tests.test_scoring import make_icp


def lead_score(fit, verdict, model):
    return LeadScore(fit=fit, verdict=verdict, criteria=[], disqualifier_hit=False,
                     disqualifier_reason="", summary="", model=model)


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
