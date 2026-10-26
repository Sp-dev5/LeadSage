import pytest

from agent import fetch


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve", lambda host: ["93.184.216.34"])
