import pytest

from dashboard import control


@pytest.fixture(autouse=True)
def kite_accepts_every_token(monkeypatch):
    """Never ask the real Kite: the test platform's root is the project, so its real .env is read."""
    monkeypatch.setattr(control, "check_token", lambda key, token: True)
    monkeypatch.setattr(control, "_last_check", None)
