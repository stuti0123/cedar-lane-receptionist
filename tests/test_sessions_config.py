import pytest

from app import config
from app.sessions import SessionStore


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_same_id_returns_same_session_and_new_id_creates_one():
    store = SessionStore(lambda sid: {"id": sid})
    a = store.get("a")
    assert store.get("a") is a and store.get("b") is not a and len(store) == 2


def test_idle_sessions_expire():
    clock = Clock()
    store = SessionStore(lambda sid: object(), ttl_seconds=60, clock=clock)
    first = store.get("a")
    clock.t = 61
    assert store.get("a") is not first  # expired, so a fresh conversation was started


def test_activity_keeps_a_session_alive():
    clock = Clock()
    store = SessionStore(lambda sid: object(), ttl_seconds=60, clock=clock)
    first = store.get("a")
    clock.t = 50
    store.get("a")
    clock.t = 100  # 100s since creation but only 50s since last use
    assert store.get("a") is first


def test_cap_drops_least_recently_used():
    clock = Clock()
    store = SessionStore(lambda sid: sid, max_sessions=2, clock=clock)
    store.get("a"); clock.t = 1
    store.get("b"); clock.t = 2
    store.get("c")  # full: "a" (oldest) must go
    assert len(store) == 2 and "a" not in store._items


def test_drop():
    store = SessionStore(lambda sid: sid)
    store.get("a")
    store.drop("a")
    assert len(store) == 0


def test_validate_lists_every_problem(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "")
    monkeypatch.setattr(config, "GOOGLE_SHEET_ID", "")
    monkeypatch.setattr(config, "GOOGLE_CREDENTIALS_FILE", "does-not-exist.json")
    with pytest.raises(RuntimeError) as e:
        config.validate()
    msg = str(e.value)
    assert all(k in msg for k in ["ANTHROPIC_API_KEY", "GOOGLE_CALENDAR_ID", "GOOGLE_SHEET_ID", "credentials file"])


def test_validate_passes_when_everything_is_set(monkeypatch, tmp_path):
    creds = tmp_path / "sa.json"
    creds.write_text("{}")
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "cal")
    monkeypatch.setattr(config, "GOOGLE_SHEET_ID", "sheet")
    monkeypatch.setattr(config, "GOOGLE_CREDENTIALS_FILE", str(creds))
    config.validate()  # no exception


def test_validate_asks_for_the_right_key_per_provider(monkeypatch, tmp_path):
    creds = tmp_path / "sa.json"
    creds.write_text("{}")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "cal")
    monkeypatch.setattr(config, "GOOGLE_SHEET_ID", "sheet")
    monkeypatch.setattr(config, "GOOGLE_CREDENTIALS_FILE", str(creds))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        config.validate()
    monkeypatch.setattr(config, "LLM_PROVIDER", "nonsense")
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        config.validate()
