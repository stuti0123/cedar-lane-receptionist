import json

import pytest

from app import config
from app.gcal import credentials_kind, google_service


def test_detects_service_account_and_oauth_files(tmp_path):
    sa, oauth, bad = tmp_path / "sa.json", tmp_path / "oauth.json", tmp_path / "bad.json"
    sa.write_text(json.dumps({"type": "service_account"}))
    oauth.write_text(json.dumps({"installed": {"client_id": "x"}}))
    bad.write_text(json.dumps({"hello": "world"}))
    assert credentials_kind(str(sa)) == "service_account"
    assert credentials_kind(str(oauth)) == "oauth"
    with pytest.raises(RuntimeError):
        credentials_kind(str(bad))


def test_oauth_without_login_gives_a_clear_instruction(tmp_path, monkeypatch):
    oauth = tmp_path / "oauth.json"
    oauth.write_text(json.dumps({"installed": {"client_id": "x"}}))
    monkeypatch.setattr(config, "GOOGLE_CREDENTIALS_FILE", str(oauth))
    monkeypatch.setattr(config, "GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))  # does not exist
    with pytest.raises(RuntimeError, match="python -m app.login"):
        google_service("calendar", "v3")
