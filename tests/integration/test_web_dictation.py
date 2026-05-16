"""Integration tests for /page/dictation and /dictation/retranscribe."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app

HX = {"HX-Request": "true"}


def _make_client(tmp_path: Path) -> TestClient:
    """Construct a minimal create_app TestClient rooted at *tmp_path*."""
    (tmp_path / "tools_meta").mkdir()
    store = ToolMetadataStore(tmp_path / "tools_meta")
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="focus",
            phrases=(),
            func=lambda **_: None,
            module="test",
            docstring=None,
        )
    )
    app = create_app(registry, store, threading.Lock())
    return TestClient(app)


@pytest.fixture()
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with cwd = tmp_path and a minimal config.toml present.

    The routes use ``Path("outputs/dictation")`` (relative), so cwd must match
    tmp_path. A minimal config.toml is written so Config.load() doesn't fail.
    """
    monkeypatch.chdir(tmp_path)

    # Minimal config.toml — DictationConfig defaults supply the endpoint.
    (tmp_path / "config.toml").write_text(
        "[hotkey]\nkey = \"scroll_lock\"\n",
        encoding="utf-8",
    )

    # Seed outputs/dictation/last.txt for the first test.
    dictation_dir = tmp_path / "outputs" / "dictation"
    dictation_dir.mkdir(parents=True)
    (dictation_dir / "last.txt").write_text("prior dictation", encoding="utf-8")
    # Seed last.wav (any bytes) for the retranscribe test.
    (dictation_dir / "last.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    return _make_client(tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_page_dictation_renders_last_text(app_client: TestClient) -> None:
    """GET /page/dictation shows the text from outputs/dictation/last.txt."""
    resp = app_client.get("/page/dictation")
    assert resp.status_code == 200
    assert "prior dictation" in resp.text


def test_retranscribe_sets_clipboard(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /dictation/retranscribe stubs remote.post_audio + clipboard."""
    sets: list[str] = []

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav, endpoint, **kw: "re-done text",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.set_clipboard_text",
        lambda t: sets.append(t),
    )

    resp = app_client.post("/dictation/retranscribe", headers=HX)
    assert resp.status_code == 200
    assert "re-done text" in resp.text
    assert sets == ["re-done text"]


def test_retranscribe_no_audio_renders_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /dictation/retranscribe with no last.wav returns 200 with error text, not 500."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        "[hotkey]\nkey = \"scroll_lock\"\n",
        encoding="utf-8",
    )
    # outputs/dictation exists but last.wav is absent
    (tmp_path / "outputs" / "dictation").mkdir(parents=True)

    client = _make_client(tmp_path)
    resp = client.post("/dictation/retranscribe", headers=HX)
    assert resp.status_code == 200
    assert "no audio recorded yet" in resp.text


def test_retranscribe_remote_error_renders_error(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /dictation/retranscribe when remote raises DictationRemoteError returns 200 with error, not 500."""
    from voice_commander.dictation.remote import DictationRemoteError

    def _raise(wav, endpoint, **kw):  # noqa: ANN001, ANN202
        raise DictationRemoteError("endpoint down")

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _raise)

    resp = app_client.post("/dictation/retranscribe", headers=HX)
    assert resp.status_code == 200
    assert "endpoint down" in resp.text
