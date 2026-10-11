"""
The model wall.

Every "ask the model to decide something" call in the app goes through this one
file. It can talk to Google Gemini's free API or to a local open-source model
running in Ollama. Pick one with LLM_PROVIDER ("ollama" is the default,
"gemini" for the cloud model), or per call with the `provider` argument. Nothing that calls generate_structured has to change.

Gemini: needs GEMINI_API_KEY. Free key at https://aistudio.google.com/
Ollama: needs `ollama serve` running. Optional OLLAMA_MODEL (default
qwen2.5:7b) and OLLAMA_HOST (default http://127.0.0.1:11434).
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.request
from typing import TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

# Reads GEMINI_API_KEY, LLM_PROVIDER etc. from a .env file in the project folder.
load_dotenv()

# Gemini's fast, free-tier model. Swap this string for another Gemini model, or
# replace this whole file with a local-model version later.
MODEL = "gemini-3.8-flash"
OLLAMA_TIMEOUT = 300  # seconds; the first call loads the model into the GPU
RETRYABLE_CODES = {500, 503, 504}  # temporary Gemini errors worth retrying (429 = quota spent, retrying only burns more)

T = TypeVar("T", bound=BaseModel)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """One shared client, created on first use. Reads GEMINI_API_KEY from env."""
    global _client
    if _client is None:
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError(
                "GEMINI_API_KEY is not set.\n"
                "Copy .env.example to .env, add your free key from "
                "https://aistudio.google.com/, then:\n"
                "  export $(grep -v '^#' .env | xargs)"
            )
        _client = genai.Client()
    return _client


def _with_retry(call, max_attempts: int = 5, base_delay: float = 2.0):
    """Run `call`, retrying temporary Gemini errors (overload, rate limit) with
    growing waits: ~2s, 4s, 8s, 16s. Other errors (bad key, bad model) raise at once."""
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except errors.APIError as e:
            if e.code not in RETRYABLE_CODES or attempt == max_attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1) + random.uniform(0, 1)
            print(f"Gemini {e.code}, retrying in {delay:.0f}s ({attempt}/{max_attempts})")
            time.sleep(delay)


def _ollama_structured(system: str, user_prompt: str, schema: type[T]) -> T:
    """Same job as the Gemini path, but against a local Ollama server."""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    body = {
        "model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        # Ollama constrains the output to this JSON schema.
        "format": schema.model_json_schema(),
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            data = json.load(resp)
    except OSError as e:
        raise RuntimeError(
            f"Could not reach Ollama at {host}. Is `ollama serve` running? ({e})"
        ) from e
    raw = (data.get("message", {}).get("content") or "").strip()
    if not raw:
        raise RuntimeError("Model returned no usable output.")
    return schema.model_validate_json(raw)


def generate_structured(
    system: str, user_prompt: str, schema: type[T], provider: str | None = None
) -> T:
    """Ask the model a question and get back clean, validated data (not prose).

    `schema` is a Pydantic model describing the shape we want back. The model is
    told to return JSON matching it, and we hand back a validated instance.

    `provider` ("ollama" or "gemini") overrides LLM_PROVIDER for this one call.
    With neither set, we use Ollama.
    """
    provider = (provider or os.environ.get("LLM_PROVIDER", "ollama")).lower()
    if provider == "ollama":
        return _ollama_structured(system, user_prompt, schema)
    if provider != "gemini":
        raise RuntimeError(f"Unknown LLM_PROVIDER {provider!r}. Use 'gemini' or 'ollama'.")

    client = _get_client()
    response = _with_retry(
        lambda: client.models.generate_content(
            model=MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.2,  # low = consistent, repeatable judgements
            ),
        )
    )

    parsed = response.parsed
    if not isinstance(parsed, schema):
        # Fallback: the SDK couldn't auto-parse; validate the raw text ourselves.
        raw = (response.text or "").strip()
        if not raw:
            raise RuntimeError("Model returned no usable output.")
        return schema.model_validate_json(raw)
    return parsed
