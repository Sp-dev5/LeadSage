"""
What the agent did on each lead, step by step: every model call with its full prompt and answer,
and every step outside the model (pages read, quote checks, rules, contact search, approve, send,
reply), kept in the lead store's `trace` table.

A run ties one pass over one lead together: one scoring, one draft or one watch. Steps are only
written while a run is open, so a model call made outside one (an evaluation, a suggestion while
typing) leaves no trace. Only the last `trace_runs_per_lead` runs of each kind are kept per lead.

The trace is read by people only. It never goes back into a model.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

KINDS = {"score": "Scoring", "draft": "Email", "watch": "Watch"}
DEFAULT_RUNS = 3
MODEL_STEPS = {
    "CompanyFacts": "Read the facts",
    "FitScores": "Rate buyer and pain",
    "PeopleSeen": "Find people on a page",
    "ItemsSeen": "Read news items",
    "EmailOut": "Write the email",
    "EmailWithProof": "Write the email",
    "DocDraft": "Draft the ideal customer",
    "TitleCalls": "Pick the sales roles",
}


@dataclass(frozen=True)
class _Run:
    id: str
    lead: str
    kind: str
    path: Path


_current: ContextVar[_Run | None] = ContextVar("trace_run", default=None)


class Step(BaseModel):
    """One step of a run as the app shows it."""

    run_id: str
    lead: str
    kind: str
    step: str
    model: str = ""
    input: str = ""
    output: str = ""
    ok: bool = True
    error: str = ""
    started: str
    ms: int = 0


class Run(BaseModel):
    id: str
    kind: str
    started: str
    steps: list[Step]

    @property
    def label(self) -> str:
        return KINDS.get(self.kind, self.kind.capitalize())

    @property
    def failed(self) -> int:
        return sum(not s.ok for s in self.steps)


def _keep() -> int:
    from agent.outreach import load_outreach
    try:
        return load_outreach().trace_runs_per_lead
    except Exception:
        return DEFAULT_RUNS


@contextmanager
def run(lead: str, kind: str, path: Path | None = None, run_id: str = "") -> Iterator[str]:
    """Open a run for one lead. Inside an open run for the same lead the open one is used, so a
    rescore during a watch lands in the watch. `run_id` continues an earlier run (a second opinion
    given after the whole batch). Older runs past the limit are removed when a new one starts."""
    from agent import store
    active = _current.get()
    if active and active.lead == lead and not run_id:
        yield active.id
        return
    path = path or store.DB_PATH
    if not run_id:
        run_id = uuid.uuid4().hex[:16]
        store.prune_trace(lead, kind, _keep() - 1, path)
    token = _current.set(_Run(run_id, lead, kind, path))
    try:
        yield run_id
    finally:
        _current.reset(token)


def active() -> str:
    """The open run's id, or "" outside a run."""
    current = _current.get()
    return current.id if current else ""


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=1)
    return json.dumps(value, indent=1, ensure_ascii=False, default=str)


def step(name: str, output="", input="", ok: bool = True, error: str = "", model: str = "",
         started: float | None = None) -> None:
    """Write one step into the open run; nothing happens outside a run. `started` is a
    time.monotonic() reading taken when the step began, for its length."""
    current = _current.get()
    if current is None:
        return
    from agent import store
    ms = int((time.monotonic() - started) * 1000) if started is not None else 0
    store.add_trace(Step(run_id=current.id, lead=current.lead, kind=current.kind, step=name, model=model,
                         input=_text(input), output=_text(output), ok=ok, error=error[:500],
                         started=store.now(), ms=ms), current.path)


def model_step(schema: str, model: str, system: str, user_prompt: str, output: str, error: str,
               started: float) -> None:
    """A model call: the full system and user prompt in, the raw answer out."""
    step(MODEL_STEPS.get(schema, schema), output=output, input=f"SYSTEM\n{system}\n\nUSER\n{user_prompt}",
         ok=not error, error=error, model=model, started=started)


def on_email(lead: str, name: str, output="", path: Path | None = None) -> None:
    """A step on a lead's emails that happens outside any run (approve, send, reply): it joins the
    lead's latest email run, or starts one if there is none."""
    from agent import store
    path = path or store.DB_PATH
    if _current.get() is not None:
        step(name, output)
        return
    latest = store.latest_trace_run(lead, "draft", path)
    with run(lead, "draft", path, run_id=latest):
        step(name, output)


def runs(lead: str, path: Path | None = None) -> list[Run]:
    """The lead's kept runs, newest first, each with its steps in order."""
    from agent import store
    grouped: dict[str, list[Step]] = {}
    last: dict[str, int] = {}
    for n, s in enumerate(store.load_trace(lead, path or store.DB_PATH)):
        grouped.setdefault(s.run_id, []).append(s)
        last[s.run_id] = n
    order = sorted(grouped, key=lambda rid: last[rid], reverse=True)
    return [Run(id=rid, kind=grouped[rid][0].kind, started=grouped[rid][0].started, steps=grouped[rid]) for rid in order]
