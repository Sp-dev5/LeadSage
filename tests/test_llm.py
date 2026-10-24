"""Cloud writing with a local fallback. No network, no model."""

import json

import pytest
import requests
from pydantic import BaseModel

from agent import llm


class Inner(BaseModel):
    quote: str


class Draft(BaseModel):
    subject: str
    claims: list[Inner]


GOOD = json.dumps({"subject": "Hello", "claims": [{"quote": "q"}]})
KEY = "gsk_secret_test_key_value"


class Reply:
    def __init__(self, status=200, content=GOOD, headers=None):
        self.status_code = status
        self.ok = status < 400
        self.headers = headers or {}
        self.text = content
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def groq(monkeypatch):
    """Fake Groq: `replies` are returned in order; every request is kept in `sent`."""
    sent, replies, slept = [], [], []

    def post(url, headers, json, timeout):
        sent.append({"url": url, "headers": headers, "body": json, "timeout": timeout})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm.time, "sleep", slept.append)
    monkeypatch.setattr(llm, "generate_structured", lambda system, user_prompt, schema: Draft(subject="local", claims=[]))
    return sent, replies, slept


def test_cloud_writes_when_a_key_is_set(groq):
    sent, replies, _ = groq
    replies.append(Reply())
    written = llm.generate_writing("sys", "user", Draft)
    assert (written.result.subject, written.model, written.fallback_reason) == ("Hello", "groq:openai/gpt-oss-120b", "")
    request = sent[0]
    assert request["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert request["body"]["response_format"]["json_schema"]["strict"] is True
    assert request["body"]["reasoning_effort"] == llm.GROQ_REASONING_EFFORT
    assert [m["role"] for m in request["body"]["messages"]] == ["system", "user"]


def test_no_key_means_local_and_no_request(groq, monkeypatch):
    sent, _, _ = groq
    monkeypatch.setenv("GROQ_API_KEY", "  ")
    written = llm.generate_writing("sys", "user", Draft)
    assert (written.result.subject, written.model, written.fallback_reason) == ("local", llm.model_name(), "")
    assert sent == []


def test_other_groq_models_get_no_reasoning_setting(groq, monkeypatch):
    sent, replies, _ = groq
    monkeypatch.setenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    replies.append(Reply())
    assert llm.generate_writing("sys", "user", Draft).model == "groq:qwen/qwen3.8-27b"
    assert "reasoning_effort" not in sent[0]["body"]


def test_short_rate_limit_is_waited_out(groq):
    sent, replies, slept = groq
    replies += [Reply(429, "slow down", {"retry-after": "4"}), Reply()]
    written = llm.generate_writing("sys", "user", Draft)
    assert (written.model, len(sent), slept) == ("groq:openai/gpt-oss-120b", 2, [5.0])


def test_long_rate_limit_falls_back_to_local(groq):
    sent, replies, slept = groq
    replies.append(Reply(429, "daily limit", {"retry-after": "3000"}))
    written = llm.generate_writing("sys", "user", Draft)
    assert (written.model, slept) == (llm.model_name(), [])
    assert "rate limited" in written.fallback_reason and len(sent) == 1


@pytest.mark.parametrize("reply", [
    Reply(500, "server error"), Reply(401, "bad key"), Reply(200, "not json"),
    Reply(200, json.dumps({"subject": "no claims"})), Reply(200, ""), requests.Timeout("slow"),
])
def test_any_cloud_failure_falls_back_to_local(groq, reply):
    _, replies, _ = groq
    replies.append(reply)
    written = llm.generate_writing("sys", "user", Draft)
    assert (written.result.subject, written.model) == ("local", llm.model_name())
    assert written.fallback_reason


def test_the_key_never_appears_in_a_fallback_reason(groq):
    _, replies, _ = groq
    replies.append(Reply(401, f"invalid key {KEY}"[:60]))
    assert KEY not in llm.generate_writing("sys", "user", Draft).fallback_reason


def test_strict_schema_closes_every_object_including_nested_ones():
    strict = llm.strict_schema(Draft.model_json_schema())
    assert strict["additionalProperties"] is False and strict["required"] == ["subject", "claims"]
    assert strict["$defs"]["Inner"]["additionalProperties"] is False
    assert "additionalProperties" not in Draft.model_json_schema()
