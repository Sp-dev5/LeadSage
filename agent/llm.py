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

from agent import trace

load_dotenv()

OLLAMA_DEFAULT_MODEL = "qwen3.5:9b"
OLLAMA_DEFAULT_SECOND_MODEL = "qwen3.5:4b"
OLLAMA_DEFAULT_EMBED_MODEL = "nomic-embed-text"
EMBED_BATCH = 16
EMBED_PREFIX = {"document": "search_document: ", "query": "search_query: "}
OLLAMA_TIMEOUT = 300
INTERACTIVE_BUDGET = 60
DOCUMENTS_BUDGET = 120

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_TIMEOUT = 60
GROQ_BUDGET = 90
GROQ_MAX_WAIT = 65
GROQ_ATTEMPTS = 3
MIN_FALLBACK_SECONDS = 5
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
FIXED_SEED = 0
DEFAULT_CONTEXT = 8192
WRITE_CONTEXT = DEFAULT_CONTEXT
CONTEXT_STEPS = (8192, 16384, 32768)
REPLY_TOKENS = 1024
LATIN_TOKENS_MAX = 0.35
OTHER_TOKENS_MAX = 1.0
LATIN_TOKENS_MIN = 0.125
OTHER_TOKENS_MIN = 0.3
CUT_TOLERANCE = 0.8
LATIN_LIMIT = 0x250


class OllamaUnreachable(RuntimeError):
    """Ollama isn't running or can't be reached; another model wouldn't help."""


class PromptTooLong(RuntimeError):
    """The prompt would not fit the largest window we use, so it was not sent."""


class PromptCut(RuntimeError):
    """Ollama reported far fewer prompt tokens than were sent: the start of the prompt, where the
    instructions are, was dropped. Another model would be cut the same way."""


FALLBACK_ERRORS = (ollama.ResponseError, httpx.TimeoutException, ValidationError, RuntimeError)


def _split_script(text: str) -> tuple[int, int]:
    latin = sum(ord(c) < LATIN_LIMIT for c in text)
    return latin, len(text) - latin


def tokens_at_most(*texts: str) -> int:
    """A safe over-estimate of how many tokens the texts take. Latin text runs near 0.25 tokens a
    character; Devanagari, Thai and Arabic text 0.5 to 0.8; Chinese and Japanese about 1."""
    latin, other = _split_script("".join(texts))
    return int(latin * LATIN_TOKENS_MAX + other * OTHER_TOKENS_MAX) + 1


def tokens_at_least(*texts: str) -> int:
    """A safe under-estimate: fewer tokens than this cannot hold the texts."""
    latin, other = _split_script("".join(texts))
    return int(latin * LATIN_TOKENS_MIN + other * OTHER_TOKENS_MIN)


def context_for(system: str, user_prompt: str, floor: int | None = None) -> int:
    """The window to ask Ollama for: at least `floor` (default 8,192), and the smallest of the steps
    that holds both prompts and the reply. Left to itself Ollama uses 4,096 and cuts a longer
    prompt from the start without a word, taking the instructions with it."""
    need = tokens_at_most(system, user_prompt) + REPLY_TOKENS
    for size in CONTEXT_STEPS:
        if size >= (floor or DEFAULT_CONTEXT) and size >= need:
            return size
    raise PromptTooLong(f"the prompt needs about {need} tokens; the largest window is {CONTEXT_STEPS[-1]}")


def _check_not_cut(system: str, user_prompt: str, seen: int | None, model: str) -> None:
    if seen and seen < tokens_at_least(system, user_prompt) * CUT_TOLERANCE:
        raise PromptCut(f"{model} read {seen} tokens of a prompt of at least {tokens_at_least(system, user_prompt)}: "
                        "the start was cut off")


def _ask_ollama(system: str, user_prompt: str, schema: type[T], model: str, temperature: float,
                context: int | None = None, timeout: float | None = None) -> T:
    """One call to Ollama. Inside a trace run, the full prompt and the raw answer (or the error) are written."""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    started, raw, error = time.monotonic(), "", ""
    try:
        options = {"temperature": temperature, "num_ctx": context_for(system, user_prompt, context)}
        if temperature == 0:
            options["seed"] = FIXED_SEED
        try:
            client = _ollama_client(host) if timeout is None else ollama.Client(host=host, timeout=timeout)
            response = client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                format=schema.model_json_schema(),
                options=options,
                think=False,
            )
        except ConnectionError as e:
            raise OllamaUnreachable(f"Could not reach Ollama at {host}. Is `ollama serve` running? ({e})") from e
        raw = (response.message.content or "").strip()
        _check_not_cut(system, user_prompt, getattr(response, "prompt_eval_count", None), model)
        if not raw:
            raise RuntimeError("Model returned no usable output.")
        return schema.model_validate_json(raw)
    except Exception as e:
        error = f"{type(e).__name__}: {str(e)[:300]}"
        raise
    finally:
        trace.model_step(schema.__name__, model, system, user_prompt, raw, error, started)


def generate_structured(system: str, user_prompt: str, schema: type[T], model: str | None = None,
                        temperature: float = JUDGE_TEMPERATURE, used: list[str] | None = None,
                        context: int | None = None, budget: float | None = None) -> T:
    """Ask the model a question and get back clean, validated data (not prose).

    `schema` is a Pydantic model describing the shape we want back. Ollama is told
    to return JSON matching it, and we hand back a validated instance. `model`
    defaults to the main model. Reading facts and judging a lead run at temperature 0 with a
    fixed seed, which makes a rerun give the same answer almost always. It is not a guarantee:
    GPU batching can change the last digits of the arithmetic, and a near tie can then go the
    other way. Writing keeps some variety so "write it again" differs.

    If the model crashes, times out or answers with broken JSON, the question goes once to
    the fallback model. Ollama being down is not retried. The model that answered is
    appended to `used`, so a caller can say which one it was.

    Every call sets the model's window: `context` is the smallest wanted (default 8,192), and a
    longer prompt gets a larger one, up to 32,768, past which it is refused (PromptTooLong). If
    Ollama still reports far fewer prompt tokens than were sent, the call fails (PromptCut) rather
    than answering from the tail of the page.

    `budget` is the most seconds the whole call may take, the fallback model included, for a rep
    waiting on a page; left out, each model gets OLLAMA_TIMEOUT.
    """
    first = model or model_name()
    deadline = time.monotonic() + budget if budget else None
    try:
        result, answered = _ask_ollama(system, user_prompt, schema, first, temperature, context, budget), first
    except (OllamaUnreachable, PromptTooLong, PromptCut):
        raise
    except FALLBACK_ERRORS as e:
        second = fallback_model_name(first)
        left = deadline - time.monotonic() if deadline else None
        if second == first or (left is not None and left < MIN_FALLBACK_SECONDS):
            raise
        log.warning("%s failed (%s); asking %s instead", first, str(e)[:120], second)
        result, answered = _ask_ollama(system, user_prompt, schema, second, temperature, context, left), second
    if used is not None:
        used.append(answered)
    return result


def embed_model_name() -> str:
    return os.environ.get("OLLAMA_EMBED_MODEL", OLLAMA_DEFAULT_EMBED_MODEL)


def embed(texts: list[str], kind: str = "document") -> list[list[float]]:
    """One vector per text from the local embedding model, for finding passages in the seller's own
    documents. `kind` is "document" for passages and "query" for what is searched for: the model was
    trained with a different prefix for each. Nothing leaves the machine."""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [EMBED_PREFIX[kind] + t for t in texts[i:i + EMBED_BATCH]]
        try:
            response = _ollama_client(host).embed(model=embed_model_name(), input=batch)
        except ConnectionError as e:
            raise OllamaUnreachable(f"Could not reach Ollama at {host}. Is `ollama serve` running? ({e})") from e
        vectors += [list(v) for v in response.embeddings]
    if len(vectors) != len(texts):
        raise RuntimeError(f"asked for {len(texts)} vectors, got {len(vectors)}")
    return vectors


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


def _groq_raw(system: str, user_prompt: str, schema: type[T]) -> str:
    """Ask Groq for schema-valid JSON and return its raw text. A rate limit (429) is waited out if
    the wait is short; any other failure raises."""
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
    deadline = time.monotonic() + GROQ_BUDGET
    for attempt in range(1, GROQ_ATTEMPTS + 1):
        left = deadline - time.monotonic()
        if left <= 0:
            raise RuntimeError(f"no answer within {GROQ_BUDGET}s")
        resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=min(GROQ_TIMEOUT, left))
        if resp.status_code != 429:
            break
        wait = _retry_after(resp)
        if attempt == GROQ_ATTEMPTS or wait > GROQ_MAX_WAIT or wait + 1 > deadline - time.monotonic():
            raise RuntimeError(f"rate limited, asked to wait {wait:.0f}s")
        time.sleep(wait + 1)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
    raw = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    if not raw:
        raise RuntimeError("no output")
    return raw


def _groq(system: str, user_prompt: str, schema: type[T]) -> T:
    """Groq's answer as a validated instance. Inside a trace run, the prompt and the answer (or the
    error, with the key masked) are written."""
    started, raw, error = time.monotonic(), "", ""
    try:
        raw = _groq_raw(system, user_prompt, schema)
        return schema.model_validate_json(raw)
    except Exception as e:
        error = _masked(f"{type(e).__name__}: {str(e)[:300]}")
        raise
    finally:
        trace.model_step(schema.__name__, cloud_model_id(), system, user_prompt, raw, error, started)


def _masked(text: str) -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    return text.replace(key, "***") if key else text


def generate_writing(system: str, user_prompt: str, schema: type[T], cloud: bool = True) -> Written:
    """Structured output for text a person will read: the Groq model if a key is set, else the
    local main model. If Groq fails for any reason, the local model writes it instead and the
    reason comes back so the caller can say so. `cloud=False` keeps the prompt on this machine."""
    reason = ""
    if cloud and cloud_enabled():
        try:
            return Written(_groq(system, user_prompt, schema), cloud_model_id(), "")
        except Exception as e:
            reason = f"{type(e).__name__}: {str(e).replace(os.environ['GROQ_API_KEY'].strip(), '***')[:90]}"
    return Written(generate_structured(system, user_prompt, schema, temperature=WRITE_TEMPERATURE,
                                       context=WRITE_CONTEXT), model_name(), reason)
