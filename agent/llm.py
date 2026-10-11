"""
The model wall.

Every "ask the model to decide something" call in the app goes through this one
file. It can talk to Google Gemini's free API or to a local open-source model
running in Ollama. Pick one with LLM_PROVIDER ("ollama" is the default,
"gemini" for the cloud model), or per call with the `provider` argument. Nothing that calls generate_structured has to change.

Gemini: needs GEMINI_API_KEY. Free key at https://aistudio.google.com/
Ollama: needs `ollama serve` running. Optional OLLAMA_MODEL (default
qwen3.5:9b) and OLLAMA_HOST (default http://127.0.0.1:11434).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TypeVar

import ollama
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

# Reads GEMINI_API_KEY, LLM_PROVIDER etc. from a .env file in the project folder.
load_dotenv()

MODEL = "gemini-3.8-flash"           # Gemini's fast, free-tier model
OLLAMA_DEFAULT_MODEL = "qwen3.5:9b"  # best of 3 on the unseen leads; override with OLLAMA_MODEL
OLLAMA_TIMEOUT = 300  # seconds; the first call loads the model into the GPU
RETRYABLE_CODES = [500, 503, 504]  # temporary Gemini errors worth retrying (429 = quota spent, retrying only burns more)

T = TypeVar("T", bound=BaseModel)


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    """One shared Gemini client, created on first use. The SDK retries temporary
    errors itself: up to 5 attempts, waits growing from 2s."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError(
            "GEMINI_API_KEY is not set.\n"
            "Add this line to the .env file in the project folder:\n"
            "  GEMINI_API_KEY=your-key\n"
            "Free key: https://aistudio.google.com/"
        )
    retry = types.HttpRetryOptions(attempts=5, initial_delay=2.0, http_status_codes=RETRYABLE_CODES)
    return genai.Client(http_options=types.HttpOptions(retry_options=retry))


@lru_cache(maxsize=1)
def _ollama_client(host: str) -> ollama.Client:
    return ollama.Client(host=host, timeout=OLLAMA_TIMEOUT)


def _ollama_structured(system: str, user_prompt: str, schema: type[T]) -> T:
    """Same job as the Gemini path, but against a local Ollama server."""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    try:
        response = _ollama_client(host).chat(
            model=os.environ.get("OLLAMA_MODEL", OLLAMA_DEFAULT_MODEL),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            format=schema.model_json_schema(),  # Ollama constrains the output to this schema
            options={"temperature": 0.2},
            # qwen3.5 thinks by default, and with a JSON schema set it then returns nothing.
            # Other models ignore this.
            think=False,
        )
    except ConnectionError as e:
        raise RuntimeError(f"Could not reach Ollama at {host}. Is `ollama serve` running? ({e})") from e
    raw = (response.message.content or "").strip()
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

    response = _get_client().models.generate_content(
        model=MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,  # low = consistent, repeatable judgements
        ),
    )

    parsed = response.parsed
    if not isinstance(parsed, schema):
        # Fallback: the SDK couldn't auto-parse; validate the raw text ourselves.
        raw = (response.text or "").strip()
        if not raw:
            raise RuntimeError("Model returned no usable output.")
        return schema.model_validate_json(raw)
    return parsed
