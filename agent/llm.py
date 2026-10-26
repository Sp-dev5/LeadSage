"""
The one file that talks to a model.

Every "ask the model to decide something" call in the app goes through
generate_structured, which asks a local open-source model running in Ollama.
Swapping the model only touches this file.

Needs `ollama serve` running. Optional OLLAMA_MODEL (default qwen3.5:9b),
OLLAMA_SECOND_MODEL (default qwen3.5:4b, the second opinion), OLLAMA_SUGGEST_MODEL (default:
the main model, so typing never makes Ollama swap models), OLLAMA_FALLBACK_MODEL (default: the
other of the main and second models, asked once when a model crashes or answers badly) and OLLAMA_HOST (default http://127.0.0.1:11434), from the environment or .env.

Writing that a person will read (email drafts) goes through generate_writing: with
GROQ_API_KEY set it asks a larger model on Groq's free tier (GROQ_MODEL, default
openai/gpt-oss-120b); with no key, or if Groq fails or is rate limited for too long, the
local model writes it. Scoring and everything else stays local. Only the drafting prompt
leaves the machine: public website text about the company, the contact's name and title,
and our own pitch.
"""

from __future__ import annotations

import copy
import logging
import os
import time
from functools import lru_cache
from typing import NamedTuple, TypeVar

import httpx
import ollama
import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

OLLAMA_DEFAULT_MODEL = "qwen3.5:9b"
OLLAMA_DEFAULT_SECOND_MODEL = "qwen3.5:4b"
OLLAMA_TIMEOUT = 300

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_TIMEOUT = 60
GROQ_MAX_WAIT = 65
GROQ_ATTEMPTS = 3
GROQ_TEMPERATURE = 0.5
GROQ_REASONING_EFFORT = "medium"

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)


class Written(NamedTuple):
    """Writing from generate_writing: the result, the model that wrote it, and why the
    cloud model was skipped after it was tried ("" if it wasn't tried or it worked)."""

    result: BaseModel
    model: str
    fallback_reason: str


def model_name() -> str:
    return os.environ.get("OLLAMA_MODEL", OLLAMA_DEFAULT_MODEL)


def second_model_name() -> str:
    return os.environ.get("OLLAMA_SECOND_MODEL", OLLAMA_DEFAULT_SECOND_MODEL)


def fallback_model_name(model: str) -> str:
    """The model asked when `model` fails: the other of the main and second models."""
    if chosen := os.environ.get("OLLAMA_FALLBACK_MODEL"):
        return chosen
    return model_name() if model == second_model_name() else second_model_name()


def suggest_model_name() -> str:
    return os.environ.get("OLLAMA_SUGGEST_MODEL") or model_name()


@lru_cache(maxsize=1)
def _ollama_client(host: str) -> ollama.Client:
    return ollama.Client(host=host, timeout=OLLAMA_TIMEOUT)


EXTRACT_TEMPERATURE = 0.0
JUDGE_TEMPERATURE = 0.0
WRITE_TEMPERATURE = 0.4


class OllamaUnreachable(RuntimeError):
    """Ollama isn't running or can't be reached; another model wouldn't help."""


FALLBACK_ERRORS = (ollama.ResponseError, httpx.TimeoutException, ValidationError, RuntimeError)


def _ask_ollama(system: str, user_prompt: str, schema: type[T], model: str, temperature: float) -> T:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    try:
        response = _ollama_client(host).chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            format=schema.model_json_schema(),
            options={"temperature": temperature},
            think=False,
        )
    except ConnectionError as e:
        raise OllamaUnreachable(f"Could not reach Ollama at {host}. Is `ollama serve` running? ({e})") from e
    raw = (response.message.content or "").strip()
    if not raw:
        raise RuntimeError("Model returned no usable output.")
    return schema.model_validate_json(raw)


def generate_structured(system: str, user_prompt: str, schema: type[T], model: str | None = None,
                        temperature: float = JUDGE_TEMPERATURE, used: list[str] | None = None) -> T:
    """Ask the model a question and get back clean, validated data (not prose).

    `schema` is a Pydantic model describing the shape we want back. Ollama is told
    to return JSON matching it, and we hand back a validated instance. `model`
    defaults to the main model. Reading facts and judging a lead run at temperature 0, so the
    same text gives the same answer; writing keeps some variety so "write it again" differs.

    If the model crashes, times out or answers with broken JSON, the question goes once to
    the fallback model. Ollama being down is not retried. The model that answered is
    appended to `used`, so a caller can say which one it was.
    """
    first = model or model_name()
    try:
        result, answered = _ask_ollama(system, user_prompt, schema, first, temperature), first
    except OllamaUnreachable:
        raise
    except FALLBACK_ERRORS as e:
        second = fallback_model_name(first)
        if second == first:
            raise
        log.warning("%s failed (%s); asking %s instead", first, str(e)[:120], second)
        result, answered = _ask_ollama(system, user_prompt, schema, second, temperature), second
    if used is not None:
        used.append(answered)
    return result


def cloud_model_id() -> str:
    return f"groq:{os.environ.get('GROQ_MODEL', GROQ_DEFAULT_MODEL)}"


def cloud_enabled() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", "").strip())


def strict_schema(schema: dict) -> dict:
    """A copy of the JSON schema with every object closed (no extra fields, all required),
    which Groq's strict mode demands on nested objects too."""
    out = copy.deepcopy(schema)

    def close(node) -> None:
        if isinstance(node, dict):
            if "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for child in node.values():
                close(child)
        elif isinstance(node, list):
            for child in node:
                close(child)

    close(out)
    return out


def _retry_after(resp: requests.Response) -> float:
    try:
        return float(resp.headers.get("retry-after", 10))
    except ValueError:
        return 10.0


def _groq(system: str, user_prompt: str, schema: type[T]) -> T:
    """Ask Groq for schema-valid JSON. A rate limit (429) is waited out if the wait is short;
    any other failure raises."""
    model = os.environ.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
        "temperature": GROQ_TEMPERATURE,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "schema": strict_schema(schema.model_json_schema()), "strict": True}},
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = GROQ_REASONING_EFFORT
    headers = {"Authorization": f"Bearer {os.environ['GROQ_API_KEY'].strip()}"}
    for attempt in range(1, GROQ_ATTEMPTS + 1):
        resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=GROQ_TIMEOUT)
        if resp.status_code != 429:
            break
        wait = _retry_after(resp)
        if attempt == GROQ_ATTEMPTS or wait > GROQ_MAX_WAIT:
            raise RuntimeError(f"rate limited, asked to wait {wait:.0f}s")
        time.sleep(wait + 1)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
    raw = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    if not raw:
        raise RuntimeError("no output")
    return schema.model_validate_json(raw)


def generate_writing(system: str, user_prompt: str, schema: type[T]) -> Written:
    """Structured output for text a person will read: the Groq model if a key is set, else the
    local main model. If Groq fails for any reason, the local model writes it instead and the
    reason comes back so the caller can say so."""
    reason = ""
    if cloud_enabled():
        try:
            return Written(_groq(system, user_prompt, schema), cloud_model_id(), "")
        except Exception as e:
            reason = f"{type(e).__name__}: {str(e).replace(os.environ['GROQ_API_KEY'].strip(), '***')[:90]}"
    return Written(generate_structured(system, user_prompt, schema, temperature=WRITE_TEMPERATURE),
                   model_name(), reason)
