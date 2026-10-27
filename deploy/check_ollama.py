"""
Run on the server: do the laptop's models answer over Tailscale?

    /opt/leadsage/venv/bin/python deploy/check_ollama.py

Reads OLLAMA_HOST from .env (the laptop's Tailscale address, port 11434), lists the models the
laptop has, checks the main and second models are among them, and asks the main one for one word.
Exit code 0 when everything answers, 1 otherwise.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.path.insert(0, str(ROOT))
    import ollama
    from agent.llm import model_name, second_model_name

    host = os.environ.get("OLLAMA_HOST", "")
    if not host:
        print("OLLAMA_HOST is not set in .env. Set it to http://<laptop tailscale address>:11434")
        return 1
    client = ollama.Client(host=host, timeout=60)
    try:
        installed = {m.model for m in client.list().models}
    except Exception as e:
        print(f"Can't reach Ollama at {host}: {e}")
        print("Is the laptop awake, on Tailscale, and is Ollama listening on 0.0.0.0? See deploy/README.md.")
        return 1
    missing = [m for m in (model_name(), second_model_name()) if m not in installed]
    if missing:
        print(f"Reached {host}, but these models are not on the laptop: {', '.join(missing)}")
        return 1
    start = time.monotonic()
    reply = client.generate(model=model_name(), prompt="Answer with one word: ready?", think=False,
                            options={"num_predict": 5})
    print(f"OK: {host} answers. {model_name()} said {reply.response.strip()!r} in {time.monotonic() - start:.1f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
