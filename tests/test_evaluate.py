"""Grading the scorer against blind labels, as the Label page runs it. No model, no network."""

import pytest

from agent import evaluate
from agent.llm import OllamaUnreachable

ROWS = [
    {"name": "Acme", "industry": "B2B", "size": "120", "location": "India", "url": "", "description": "Payroll.",
     "my_label": "Qualify"},
    {"name": "Bolt", "industry": "B2C", "size": "80", "location": "India", "url": "", "description": "Games.",
     "my_label": "qualify"},
    {"name": "Cove", "industry": "B2B", "size": "60", "location": "India", "url": "", "description": "Expenses.",
     "my_label": ""},
]


class Scored:
    def __init__(self, verdict, fit):
        self.verdict, self.fit = verdict, fit


@pytest.fixture
def scorer(monkeypatch):
    verdicts = {}
    monkeypatch.setattr(evaluate, "lead_text", lambda row, how: (row["description"], "typed"))

    def score(icp, company):
        outcome = verdicts[company.name]
        if isinstance(outcome, Exception):
            raise outcome
        return Scored(*outcome)

    monkeypatch.setattr(evaluate, "score_with_second_opinion", score)
    return verdicts


def test_only_labelled_rows_are_scored_and_graded(scorer):
    scorer.update(Acme=("review", 60), Bolt=("disqualify", 20))
    lines = []
    results = evaluate.grade_rows(ROWS, icp=None, report=lines.append)
    assert [(r["name"], r["grade"]) for r in results] == [("Acme", "near"), ("Bolt", "MISS")]
    assert evaluate.grade_summary(results) == ["OK 0  near 1  MISS 1  errors 0  (of 2)",
                                               "MISS: Bolt: you qualify, scorer disqualify 20"]
    assert lines[0] == "[1/2] Acme: you qualify, scorer review 60 (near)"


def test_one_failed_lead_is_an_error_but_ollama_down_stops_the_run(scorer):
    scorer.update(Acme=RuntimeError("bad json"), Bolt=("qualify", 80))
    results = evaluate.grade_rows(ROWS, icp=None, report=lambda line: None)
    assert [r["grade"] for r in results] == ["ERR", "OK"]
    scorer["Acme"] = OllamaUnreachable("down")
    with pytest.raises(OllamaUnreachable):
        evaluate.grade_rows(ROWS, icp=None, report=lambda line: None)
