"""Integration tests for the dictation timing panel (ADR 0101).

Covers:
  - GET /page/dictation/timing — no file   → "No timing recorded yet."
  - GET /page/dictation/timing — full record (server data present) → phase labels + ms values
  - GET /page/dictation/timing — server-absent record              → "Server breakdown unavailable"
  - GET /page/dictation          — full page includes the panel (#dictation-timing)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_commander.dictation.store import DictationStore
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path) -> TestClient:
    """Build a minimal TestClient that satisfies create_app's requirements."""
    (tmp_path / "tools_meta").mkdir(exist_ok=True)
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


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dictation_dir(tmp_path: Path) -> Path:
    """Create outputs/dictation under tmp_path and return its Path."""
    d = tmp_path / "outputs" / "dictation"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def client_no_timings(
    tmp_path: Path, dictation_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """TestClient whose dictation dir has NO last_timings.json."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[hotkey]\nkey = "scroll_lock"\n', encoding="utf-8"
    )
    return _make_client(tmp_path)


@pytest.fixture()
def client_full_timings(
    tmp_path: Path, dictation_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """TestClient whose dictation dir has a full last_timings.json (server data present)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[hotkey]\nkey = "scroll_lock"\n', encoding="utf-8"
    )
    record: dict = {
        "ts": 1716556800.0,
        "chars": 42,
        "server": {
            "transcribe_ms": 1234.5,
            "clean_ms": 567.8,
            "format_ms": 89.0,
            "server_total_ms": 1891.3,
        },
        "roundtrip_ms": 2050.1,
        "network_ms": 158.8,
        "postprocess_ms": 12.3,
        "paste_ms": 45.6,
        "total_ms": 2108.0,
    }
    DictationStore(dictation_dir).save_timings(record)
    return _make_client(tmp_path)


@pytest.fixture()
def client_server_absent(
    tmp_path: Path, dictation_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """TestClient whose last_timings.json has an empty server dict."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[hotkey]\nkey = "scroll_lock"\n', encoding="utf-8"
    )
    record: dict = {
        "ts": 1716556800.0,
        "chars": 10,
        "server": {},
        "roundtrip_ms": 3100.0,
        "network_ms": None,
        "postprocess_ms": 9.0,
        "paste_ms": 22.0,
        "total_ms": 3131.0,
    }
    DictationStore(dictation_dir).save_timings(record)
    return _make_client(tmp_path)


# ---------------------------------------------------------------------------
# tests — /page/dictation/timing fragment endpoint
# ---------------------------------------------------------------------------


class TestTimingFragmentNoFile:
    def test_status_200(self, client_no_timings: TestClient) -> None:
        resp = client_no_timings.get("/page/dictation/timing")
        assert resp.status_code == 200

    def test_contains_panel_id(self, client_no_timings: TestClient) -> None:
        resp = client_no_timings.get("/page/dictation/timing")
        assert 'id="dictation-timing"' in resp.text

    def test_shows_no_timing_message(self, client_no_timings: TestClient) -> None:
        resp = client_no_timings.get("/page/dictation/timing")
        assert "No timing recorded yet." in resp.text


class TestTimingFragmentFullRecord:
    def test_status_200(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert resp.status_code == 200

    def test_contains_panel_id(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert 'id="dictation-timing"' in resp.text

    def test_shows_transcription_label(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert "Transcription" in resp.text

    def test_shows_ai_clean_label(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert "AI clean" in resp.text

    def test_shows_structural_format_label(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert "Structural format" in resp.text

    def test_shows_network_label(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert "Network" in resp.text

    def test_shows_paste_label(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert "Paste" in resp.text

    def test_shows_transcribe_ms_value(self, client_full_timings: TestClient) -> None:
        # 1234.5 ms → rendered as "1234 ms"
        resp = client_full_timings.get("/page/dictation/timing")
        assert "1234" in resp.text

    def test_shows_clean_ms_value(self, client_full_timings: TestClient) -> None:
        # 567.8 ms → "567 ms"
        resp = client_full_timings.get("/page/dictation/timing")
        assert "567" in resp.text

    def test_shows_paste_ms_value(self, client_full_timings: TestClient) -> None:
        # 45.6 ms → "45 ms"
        resp = client_full_timings.get("/page/dictation/timing")
        assert "45" in resp.text

    def test_shows_total_in_seconds(self, client_full_timings: TestClient) -> None:
        # total_ms=2108.0 → "2.11 s"
        resp = client_full_timings.get("/page/dictation/timing")
        assert "2.11 s" in resp.text

    def test_shows_network_honesty_caption(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert "both legs" in resp.text
        assert "approximate" in resp.text

    def test_does_not_show_no_timing_message(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert "No timing recorded yet." not in resp.text

    def test_does_not_show_server_absent_message(self, client_full_timings: TestClient) -> None:
        resp = client_full_timings.get("/page/dictation/timing")
        assert "Server breakdown unavailable" not in resp.text


class TestTimingFragmentServerAbsent:
    def test_status_200(self, client_server_absent: TestClient) -> None:
        resp = client_server_absent.get("/page/dictation/timing")
        assert resp.status_code == 200

    def test_contains_panel_id(self, client_server_absent: TestClient) -> None:
        resp = client_server_absent.get("/page/dictation/timing")
        assert 'id="dictation-timing"' in resp.text

    def test_shows_server_absent_message(self, client_server_absent: TestClient) -> None:
        resp = client_server_absent.get("/page/dictation/timing")
        assert "Server breakdown unavailable" in resp.text

    def test_shows_roundtrip_label(self, client_server_absent: TestClient) -> None:
        resp = client_server_absent.get("/page/dictation/timing")
        assert "Round-trip" in resp.text

    def test_shows_roundtrip_ms_value(self, client_server_absent: TestClient) -> None:
        # roundtrip_ms=3100.0 → "3100 ms"
        resp = client_server_absent.get("/page/dictation/timing")
        assert "3100" in resp.text

    def test_shows_paste_label(self, client_server_absent: TestClient) -> None:
        resp = client_server_absent.get("/page/dictation/timing")
        assert "Paste" in resp.text

    def test_total_in_seconds(self, client_server_absent: TestClient) -> None:
        # total_ms=3131.0 → "3.13 s"
        resp = client_server_absent.get("/page/dictation/timing")
        assert "3.13 s" in resp.text

    def test_does_not_crash(self, client_server_absent: TestClient) -> None:
        """None values (e.g. network_ms=None) must not raise — response is 200."""
        # network_ms is None; state C doesn't show that row, but the template
        # must not crash trying to access it (Jinja guard covers state B as well).
        resp = client_server_absent.get("/page/dictation/timing")
        assert resp.status_code == 200
        # Sanity: we see the known-non-None paste value
        assert "22 ms" in resp.text


# ---------------------------------------------------------------------------
# tests — None field in full server record renders as dash
# ---------------------------------------------------------------------------


def test_none_field_renders_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A None value in a field (e.g. network_ms=None) renders '—', not 'None' or crash."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('[hotkey]\nkey = "scroll_lock"\n', encoding="utf-8")
    dictation_dir = tmp_path / "outputs" / "dictation"
    dictation_dir.mkdir(parents=True)
    # Full server record but network_ms=None
    record: dict = {
        "ts": 1716556800.0,
        "chars": 5,
        "server": {
            "transcribe_ms": 100.0,
            "clean_ms": 50.0,
            "format_ms": 10.0,
            "server_total_ms": 160.0,
        },
        "roundtrip_ms": 200.0,
        "network_ms": None,
        "postprocess_ms": 5.0,
        "paste_ms": 8.0,
        "total_ms": 213.0,
    }
    DictationStore(dictation_dir).save_timings(record)
    client = _make_client(tmp_path)
    resp = client.get("/page/dictation/timing")
    assert resp.status_code == 200
    # None network_ms → "—"
    assert "—" in resp.text
    # Should NOT emit the Python repr "None"
    assert ">None<" not in resp.text


# ---------------------------------------------------------------------------
# tests — GET /page/dictation full page includes the panel
# ---------------------------------------------------------------------------


class TestDictationPageIncludesPanel:
    def test_panel_present_no_timings(self, client_no_timings: TestClient) -> None:
        """GET /page/dictation renders #dictation-timing even when no record exists."""
        resp = client_no_timings.get("/page/dictation")
        assert resp.status_code == 200
        assert 'id="dictation-timing"' in resp.text

    def test_panel_present_full_timings(self, client_full_timings: TestClient) -> None:
        """GET /page/dictation renders the full breakdown when record is present."""
        resp = client_full_timings.get("/page/dictation")
        assert resp.status_code == 200
        assert 'id="dictation-timing"' in resp.text
        assert "Transcription" in resp.text

    def test_panel_heading_present(self, client_no_timings: TestClient) -> None:
        """The 'Last dictation timing' heading should appear on the page."""
        resp = client_no_timings.get("/page/dictation")
        assert resp.status_code == 200
        assert "Last dictation timing" in resp.text
