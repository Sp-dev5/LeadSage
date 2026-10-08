"""
The model wall.

Every "ask the model to decide something" call in the app goes through this one
file. Today it talks to Google Gemini's free API. When we later switch to a
local open-source model, THIS is the only file that changes — nothing that calls
it has to change.

Needs GEMINI_API_KEY in your environment or a .env file.
Get a free key at https://aistudio.google.com/ → "Get API key".
"""

from __future__ import annotations

import os
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

# Gemini's fast, free-tier model. Swap this string for another Gemini model, or
# replace this whole file with a local-model version later.
MODEL = "gemini-2.5-flash"

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


def generate_structured(system: str, user_prompt: str, schema: type[T]) -> T:
    """Ask the model a question and get back clean, validated data (not prose).

    `schema` is a Pydantic model describing the shape we want back. The model is
    told to return JSON matching it, and we hand back a validated instance.
    """
    client = _get_client()
    response = client.models.generate_content(
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
