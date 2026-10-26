"""
Long tasks (scoring a lead list, drafting a batch of emails) run in a background thread so a
page can show their progress line by line. One task at a time: they share the local model.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    title: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    failed: str = ""


JOBS: dict[str, Job] = {}
_lock = threading.Lock()


def running() -> Job | None:
    return next((j for j in JOBS.values() if not j.done), None)


def start(title: str, work: Callable[[Callable[[str], None]], None]) -> Job:
    """Run `work(report)` in the background, or return the task already running."""
    with _lock:
        if busy := running():
            return busy
        job = Job(id=uuid.uuid4().hex[:12], title=title)
        JOBS[job.id] = job

    def run() -> None:
        try:
            work(job.lines.append)
        except Exception as e:
            job.failed = str(e)[:200]
        finally:
            job.done = True

    threading.Thread(target=run, daemon=True).start()
    return job
