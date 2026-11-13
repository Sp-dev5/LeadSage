import pytest

from agent import fetch


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve", lambda host: ["93.184.216.34"])


@pytest.fixture(autouse=True)
def fresh_fetch_state(monkeypatch):
    monkeypatch.setattr(fetch, "_ROBOTS", {})
    monkeypatch.setattr(fetch, "_ROBOTS_FETCHING", {})
    monkeypatch.setattr(fetch, "_NEXT_REQUEST", {})
    monkeypatch.setattr(fetch, "HOST_DELAY", 0.0)
