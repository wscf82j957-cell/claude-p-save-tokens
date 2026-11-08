from __future__ import annotations

import json
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server import create_app
from app.settings import Settings

ROOT = Path(__file__).resolve().parent
ORIGIN = "http://127.0.0.1:8765"


@dataclass
class World:
    client: TestClient
    token: str
    mock: Path
    tmp: Path
    settings: Settings

    def write_mode(self, payload: dict) -> None:
        (self.mock.parent / "mock_mode.json").write_text(json.dumps(payload), encoding="utf-8")

    def report(self) -> dict:
        return json.loads((self.mock.parent / "mock_report.json").read_text(encoding="utf-8"))

    def headers(self, csrf: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf,
        }


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mock = tmp_path / "bin" / "claude"
    mock.parent.mkdir()
    shutil.copy(ROOT / "mock_claude.py", mock)
    mock.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    token_path = tmp_path / "auth.token"
    token = secrets.token_urlsafe(32)
    token_path.write_text(token, encoding="utf-8")
    token_path.chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LOGNAME", "tester")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key-value")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "auth-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-test-secret")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_FOUNDRY", "1")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/not-a-real-key.json")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    sentinel = tmp_path / "sentinel-bin"
    sentinel.mkdir()
    monkeypatch.setenv("PATH", str(sentinel) + os.pathsep + os.environ.get("PATH", ""))
    settings = Settings(
        claude_bin=str(mock),
        host="127.0.0.1",
        port=8765,
        lan=False,
        token_file=token_path,
        work_root=work,
        timeout_sec=2,
        extra_origins=(),
        home=home,
    )
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as client:
        yield World(client=client, token=token, mock=mock, tmp=tmp_path, settings=settings)


def csrf_of(world: World) -> str:
    response = world.client.get(
        "/api/session",
        headers={"Authorization": f"Bearer {world.token}", "Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf"]
