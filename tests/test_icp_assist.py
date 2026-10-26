"""Ideal-customer suggestions. The model is faked."""

import pytest

from agent import icp_assist


def test_suggest_sends_the_field_draft_and_context(monkeypatch):
    seen = {}

    def fake(system, user_prompt, schema, **kw):
        seen["prompt"] = user_prompt
        return schema(text="Head or VP of Sales  at B2B software companies — usually 5 to 30 reps.")

    monkeypatch.setattr(icp_assist, "generate_structured", fake)
    out = icp_assist.suggest("buyer_persona", "Head or VP of Sales",
                             {"buyer_persona": "Head or VP of Sales", "pain_solved": "Manual research"})
    assert out == "Head or VP of Sales at B2B software companies, usually 5 to 30 reps."
    assert "FIELD: buyer_persona" in seen["prompt"]
    assert "REP'S TEXT: Head or VP of Sales" in seen["prompt"]
    assert "pain_solved: Manual research" in seen["prompt"]
    assert "buyer_persona: Head" not in seen["prompt"]


def test_unknown_field_is_refused():
    with pytest.raises(ValueError):
        icp_assist.suggest("weights", "x", {})


def test_other_fields_are_context_only_and_nothing_else_is_read():
    assert "Never carry a fact" in icp_assist.SYSTEM
    assert "no list of companies" in icp_assist.SYSTEM


def test_clean_titles_keeps_short_new_unique_titles():
    raw = ["VP of Sales", "vp of sales", "Head of Sales", "Chief Revenue Officer", "  Director, Sales Operations. ",
           "Senior Vice President of Global Field Sales Operations", "Head — Revenue", ""]
    out = icp_assist.clean_titles(raw, "Head of Sales at B2B software companies")
    assert out == ["VP of Sales", "Chief Revenue Officer", "Director, Sales Operations", "Head, Revenue"]


def test_clean_titles_stops_at_eight():
    out = icp_assist.clean_titles([f"Title {i}" for i in range(20)], "")
    assert len(out) == icp_assist.MAX_TITLES


def test_suggest_buyer_returns_text_and_cleaned_titles(monkeypatch):
    seen = {}

    def fake(system, user_prompt, schema, **kw):
        seen.update(system=system, prompt=user_prompt, schema=schema)
        return schema(text="Head of Sales who runs the outbound team.", titles=["VP Sales", "Head of Sales", "CRO"])

    monkeypatch.setattr(icp_assist, "generate_structured", fake)
    text, titles = icp_assist.suggest_buyer("Head of Sales", {"buyer_persona": "Head of Sales", "notes": "AI agent"})
    assert text == "Head of Sales who runs the outbound team."
    assert titles == ["VP Sales", "CRO"]
    assert seen["schema"] is icp_assist.BuyerSuggestion
    assert "FIELD: buyer_persona" in seen["prompt"]
    assert "6 to 8 other job titles" in seen["system"]


def test_suggestions_use_the_suggest_model_else_the_main_model(monkeypatch):
    models = []

    def fake(system, user_prompt, schema, **kw):
        models.append(kw.get("model"))
        return schema(text="A plain sentence.", **({"titles": ["CRO"]} if "titles" in schema.model_fields else {}))

    monkeypatch.setattr(icp_assist, "generate_structured", fake)
    monkeypatch.setenv("OLLAMA_SUGGEST_MODEL", "tiny:1b")
    icp_assist.suggest("notes", "x", {})
    icp_assist.suggest_buyer("x", {})
    assert models == ["tiny:1b", "tiny:1b"]
    monkeypatch.delenv("OLLAMA_SUGGEST_MODEL")
    icp_assist.suggest("notes", "x", {})
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    icp_assist.suggest("notes", "x", {})
    assert models[-1] == "qwen3.5:9b"
