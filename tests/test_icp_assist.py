"""Ideal-customer suggestions. The model is faked."""

import pytest

from agent import icp_assist


def test_suggest_sends_the_field_draft_and_context(monkeypatch):
    seen = {}

    def fake(system, user_prompt, schema):
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
