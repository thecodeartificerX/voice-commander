"""Visual E2E harness for dictation-pipeline timing observability (ADR 0101).

Exercises the full timing data path the way a real user would trigger it:

  PHASE 1 — Data path (DictationSession → DictationStore → last_timings.json)
    Start the in-repo MockWsServer configured to return a done frame WITH a
    known timings block; drive one DictationSession cycle (feed synthetic PCM
    via handle_utterance, call finish() + get_timings()); build the timing
    record via the real build_timing_record + persist via DictationStore.
    Assert last_timings.json contains the expected field values.

  PHASE 2 — Web pixels (FastAPI TestClient → /page/dictation + /page/dictation/timing)
    Build the web app via create_app pointed at the same tmp dir; assert the
    rendered HTML contains the panel id, phase labels, and rendered ms numbers.
    Write the rendered HTML to an evidence file. Assert status codes are 200.

  PHASE 3 — Graceful-degradation (server returns no timings)
    Run a second cycle with mock server returning done WITHOUT timings (server={});
    persist; GET /page/dictation/timing; assert HTML contains "Server breakdown
    unavailable" and does not 500.

Evidence written to: outputs/dictation_timing_e2e/
  last_timings.json         — persisted timing record (copied from tmp)
  timing_fragment_full.html — rendered HTML for full record
  timing_fragment_absent.html — rendered HTML for server-absent record
  dictation_timing_e2e.json — checkpoint summary
  dictation_timing_e2e.log  — full run log

Exit 0 when all checkpoints pass, 1 on any failure.

Run with::

    python scripts/dictation_timing_e2e.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Repo root + local src/ on the path (mirrors sprite_dim_e2e.py pattern)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# Make tests/integration importable so we can reuse MockWsServer.
sys.path.insert(0, str(ROOT / "tests" / "integration"))

OUT_DIR = ROOT / "outputs" / "dictation_timing_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT_DIR / "dictation_timing_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_timing_e2e")

# ---------------------------------------------------------------------------
# Known server timings used throughout the harness
# ---------------------------------------------------------------------------

_SERVER_TIMINGS = {
    "transcribe_ms": 1200.0,
    "clean_ms": 550.0,
    "format_ms": 10.0,
    "server_total_ms": 1780.0,
}
_DONE_TEXT = "hello world this is a dictation test"

# ---------------------------------------------------------------------------
# Checkpoint registry (mirrors dictation_streaming_e2e.py pattern)
# ---------------------------------------------------------------------------

_checkpoints: list[tuple[int, str, bool | None, bool]] = []


def _record(
    n: int,
    label: str,
    result: bool | None,
    *,
    skip_msg: str = "",
    blocking: bool = True,
) -> None:
    _checkpoints.append((n, label, result, blocking))
    if result is None:
        log.info("CHECKPOINT %d: SKIPPED — %s", n, skip_msg)
    elif result:
        log.info("CHECKPOINT %d: PASS — %s", n, label)
    else:
        log.error("CHECKPOINT %d: FAIL — %s", n, label)


def _assert(n: int, label: str, cond: bool) -> bool:
    """Record a hard checkpoint. Returns cond."""
    _record(n, label, cond)
    return cond


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


class _FakeBus:
    """Minimal EventBus stand-in — records events, never blocks."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, data: dict | None = None) -> None:
        self.events.append((event_type, data or {}))


def _make_web_client(dictation_dir: Path, tmp_root: Path):
    """Build a FastAPI TestClient whose dictation dir resolves to *dictation_dir*.

    The web app resolves ``Path("outputs/dictation")`` at request time via
    ``Path(_DICTATION_DIR.as_posix())``, so we chdir to *tmp_root* (where
    ``outputs/dictation/`` is a subdir) before building the app and keep the
    cwd there for the lifetime of the client.
    """
    from fastapi.testclient import TestClient

    from voice_commander.registry import ToolEntry, ToolRegistry
    from voice_commander.tool_metadata import ToolMetadataStore
    from voice_commander.web.app import create_app

    (tmp_root / "tools_meta").mkdir(exist_ok=True)
    (tmp_root / "config.toml").write_text(
        '[hotkey]\nkey = "scroll_lock"\n', encoding="utf-8"
    )
    store = ToolMetadataStore(tmp_root / "tools_meta")
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
# Phase 1 — Data path
# ---------------------------------------------------------------------------


def phase_1_data_path(tmp_dir: Path) -> bool:
    """Drive one DictationSession cycle; assert last_timings.json is correct."""
    log.info("=== PHASE 1: data path (DictationSession → DictationStore) ===")

    # Import the real modules under test.
    from _dictation_ws import MockWsServer  # type: ignore[import]

    from voice_commander.dictation.session import DictationSession
    from voice_commander.dictation.store import DictationStore
    from voice_commander.dictation.timing import build_timing_record
    from voice_commander.dictation.vocab import Vocabulary

    dictation_dir = tmp_dir / "outputs" / "dictation"
    dictation_dir.mkdir(parents=True, exist_ok=True)
    store = DictationStore(dictation_dir)

    ok = True

    with MockWsServer(done_text=_DONE_TEXT, timings=_SERVER_TIMINGS) as srv:
        log.info("mock WS server on %s", srv.ws_url)
        bus = _FakeBus()
        sess = DictationSession(
            bus,
            ws_url=srv.ws_url,
            end_word="done",
            idle_timeout_s=15.0,
        )
        sess.start(Vocabulary())
        sess.handle_utterance(_audio(8000), "some words here")
        sess.handle_utterance(_audio(4000), "more dictation content")

        raw_text = sess.finish()
        timings_raw = sess.get_timings()

        log.info("finish() returned %r", raw_text)
        log.info("get_timings() = %s", json.dumps(timings_raw))

    # CP1: finish() returned the done text.
    ok &= _assert(1, f"finish() returns done_text {_DONE_TEXT!r}", raw_text == _DONE_TEXT)

    # CP2: roundtrip_ms is a positive float.
    roundtrip_ms = timings_raw.get("roundtrip_ms")
    ok &= _assert(2, "roundtrip_ms is a positive float", isinstance(roundtrip_ms, float) and roundtrip_ms > 0)
    log.info("roundtrip_ms = %.2f ms", roundtrip_ms or 0)

    # CP3: server timings dict matches what the mock injected.
    srv_t = timings_raw.get("server", {})
    ok &= _assert(3, "server.transcribe_ms == 1200", srv_t.get("transcribe_ms") == 1200.0)
    ok &= _assert(4, "server.clean_ms == 550", srv_t.get("clean_ms") == 550.0)
    ok &= _assert(5, "server.format_ms == 10", srv_t.get("format_ms") == 10.0)
    ok &= _assert(6, "server.server_total_ms == 1780", srv_t.get("server_total_ms") == 1780.0)

    # Build the timing record exactly as _finalize_dictation would.
    # Simulate postprocess_ms and paste_ms with realistic small values.
    postprocess_ms = 5.0
    paste_ms = 18.0

    record = build_timing_record(
        text=raw_text,
        server_timings=timings_raw["server"],
        roundtrip_ms=timings_raw["roundtrip_ms"],
        postprocess_ms=postprocess_ms,
        paste_ms=paste_ms,
    )
    log.info("built timing record: %s", json.dumps(record, indent=2))

    # Persist via real DictationStore.
    store.save_timings(record)

    # CP7: last_timings.json exists.
    ok &= _assert(7, "last_timings.json exists", store.timings_path.exists())

    # Read it back and validate the schema.
    persisted = store.read_timings()
    ok &= _assert(8, "read_timings() returns non-None", persisted is not None)
    if persisted is None:
        log.error("last_timings.json could not be read — aborting phase 1")
        return False

    # CP9: server sub-dict has the expected values.
    p_srv = persisted.get("server", {})
    ok &= _assert(9, "persisted server.transcribe_ms == 1200", p_srv.get("transcribe_ms") == 1200.0)
    ok &= _assert(10, "persisted server.clean_ms == 550", p_srv.get("clean_ms") == 550.0)

    # CP11: roundtrip_ms > 0.
    p_rt = persisted.get("roundtrip_ms")
    ok &= _assert(11, "persisted roundtrip_ms > 0", isinstance(p_rt, float) and p_rt > 0)

    # CP12: network_ms == roundtrip_ms - server_total_ms (within 1 ms tolerance).
    p_net = persisted.get("network_ms")
    expected_net = (p_rt or 0.0) - 1780.0
    net_ok = p_net is not None and abs(p_net - expected_net) < 1.0
    ok &= _assert(12, f"network_ms == roundtrip_ms - 1780 (expected ~{expected_net:.2f})", net_ok)
    log.info("network_ms = %.2f (expected %.2f)", p_net or 0.0, expected_net)

    # CP13: paste_ms present.
    ok &= _assert(13, "paste_ms present in record", "paste_ms" in persisted)

    # CP14: total_ms == roundtrip + postprocess + paste (within 1 ms).
    p_total = persisted.get("total_ms", 0.0)
    expected_total = (p_rt or 0.0) + postprocess_ms + paste_ms
    total_ok = abs(p_total - expected_total) < 1.0
    ok &= _assert(14, f"total_ms == roundtrip+postprocess+paste (expected ~{expected_total:.2f})", total_ok)
    log.info("total_ms = %.2f (expected %.2f)", p_total, expected_total)

    # Print the full record for evidence.
    print("\n--- EVIDENCE: last_timings.json ---")
    print(json.dumps(persisted, indent=2))
    print("-----------------------------------\n")

    # Copy the timings JSON to the evidence dir.
    shutil.copy2(store.timings_path, OUT_DIR / "last_timings.json")
    log.info("PHASE 1 %s", "PASS" if ok else "FAIL")
    return ok


# ---------------------------------------------------------------------------
# Phase 2 — Web pixels
# ---------------------------------------------------------------------------


def phase_2_web_pixels(tmp_dir: Path) -> bool:
    """GET /page/dictation and /page/dictation/timing; assert rendered HTML."""
    log.info("=== PHASE 2: web pixels (FastAPI TestClient) ===")

    # The web app reads Path("outputs/dictation") relative to cwd, so we chdir
    # into tmp_dir (which has outputs/dictation/ with last_timings.json from phase 1).
    orig_cwd = os.getcwd()
    os.chdir(tmp_dir)
    try:
        return _phase_2_inner(tmp_dir)
    finally:
        os.chdir(orig_cwd)


def _phase_2_inner(tmp_dir: Path) -> bool:
    ok = True

    client = _make_web_client(tmp_dir / "outputs" / "dictation", tmp_dir)

    # ------------------------------------------------------------------
    # GET /page/dictation/timing — full record
    # ------------------------------------------------------------------
    resp_fragment = client.get("/page/dictation/timing")
    log.info("/page/dictation/timing status: %d", resp_fragment.status_code)

    ok &= _assert(15, "GET /page/dictation/timing returns 200", resp_fragment.status_code == 200)

    html_fragment = resp_fragment.text
    log.info("/page/dictation/timing HTML length: %d chars", len(html_fragment))

    # Write evidence file.
    frag_path = OUT_DIR / "timing_fragment_full.html"
    frag_path.write_text(html_fragment, encoding="utf-8")
    log.info("HTML fragment evidence: %s", frag_path)
    print(f"\nEVIDENCE: timing fragment HTML written to {frag_path}")

    # CP16: panel id present.
    ok &= _assert(16, 'HTML contains id="dictation-timing"', 'id="dictation-timing"' in html_fragment)

    # CP17: phase labels present.
    ok &= _assert(17, 'HTML contains "Transcription" label', "Transcription" in html_fragment)
    ok &= _assert(18, 'HTML contains "AI clean" label', "AI clean" in html_fragment)
    ok &= _assert(19, 'HTML contains "Network" label', "Network" in html_fragment)
    ok &= _assert(20, 'HTML contains "Paste" label', "Paste" in html_fragment)

    # CP21: rendered ms values (transcribe_ms=1200, clean_ms=550).
    ok &= _assert(21, 'HTML contains "1200" (transcribe_ms)', "1200" in html_fragment)
    ok &= _assert(22, 'HTML contains "550" (clean_ms)', "550" in html_fragment)

    # CP23: does NOT show "No timing recorded yet."
    ok &= _assert(23, 'HTML does not show "No timing recorded yet."', "No timing recorded yet." not in html_fragment)

    # CP24: does NOT show "Server breakdown unavailable"
    ok &= _assert(24, 'HTML does not show "Server breakdown unavailable"', "Server breakdown unavailable" not in html_fragment)

    # ------------------------------------------------------------------
    # GET /page/dictation — full page includes the panel
    # ------------------------------------------------------------------
    resp_page = client.get("/page/dictation")
    log.info("/page/dictation status: %d", resp_page.status_code)

    ok &= _assert(25, "GET /page/dictation returns 200", resp_page.status_code == 200)

    html_page = resp_page.text
    ok &= _assert(26, '/page/dictation HTML contains id="dictation-timing"', 'id="dictation-timing"' in html_page)
    ok &= _assert(27, '/page/dictation HTML contains "Transcription"', "Transcription" in html_page)

    log.info("PHASE 2 %s", "PASS" if ok else "FAIL")
    return ok


# ---------------------------------------------------------------------------
# Phase 3 — Graceful degradation (server returns no timings)
# ---------------------------------------------------------------------------


def phase_3_graceful_degradation(tmp_dir: Path) -> bool:
    """Run a second cycle with mock server returning no timings; assert HTML degrades."""
    log.info("=== PHASE 3: graceful degradation (server absent timings) ===")

    from _dictation_ws import MockWsServer  # type: ignore[import]

    from voice_commander.dictation.session import DictationSession
    from voice_commander.dictation.store import DictationStore
    from voice_commander.dictation.timing import build_timing_record
    from voice_commander.dictation.vocab import Vocabulary

    dictation_dir = tmp_dir / "outputs" / "dictation"
    store = DictationStore(dictation_dir)

    ok = True

    # Run a cycle with NO timings in done frame.
    with MockWsServer(done_text="short dictation", timings=None) as srv:
        log.info("mock WS server (no timings) on %s", srv.ws_url)
        bus = _FakeBus()
        sess = DictationSession(
            bus,
            ws_url=srv.ws_url,
            end_word="done",
            idle_timeout_s=15.0,
        )
        sess.start(Vocabulary())
        sess.handle_utterance(_audio(4000), "short text")
        raw_text = sess.finish()
        timings_raw = sess.get_timings()

    log.info("finish() (no-timings) returned %r", raw_text)
    log.info("get_timings() (no-timings) = %s", json.dumps(timings_raw))

    # CP28: server dict is empty when mock sends no timings.
    ok &= _assert(28, "server timings dict is {} when absent from done frame", timings_raw.get("server") == {})

    # Build and persist the record.
    record = build_timing_record(
        text=raw_text,
        server_timings=timings_raw["server"],
        roundtrip_ms=timings_raw["roundtrip_ms"],
        postprocess_ms=3.0,
        paste_ms=12.0,
    )
    store.save_timings(record)

    # CP29: network_ms is None when server timings absent.
    ok &= _assert(29, "network_ms is None when server timings absent", record.get("network_ms") is None)

    # Now query the web endpoint.
    orig_cwd = os.getcwd()
    os.chdir(tmp_dir)
    try:
        client = _make_web_client(dictation_dir, tmp_dir)
        resp = client.get("/page/dictation/timing")
        log.info("/page/dictation/timing (absent) status: %d", resp.status_code)
        html_absent = resp.text
    finally:
        os.chdir(orig_cwd)

    # Write evidence file.
    absent_path = OUT_DIR / "timing_fragment_absent.html"
    absent_path.write_text(html_absent, encoding="utf-8")
    log.info("HTML fragment (absent) evidence: %s", absent_path)
    print(f"\nEVIDENCE: timing fragment (server-absent) HTML written to {absent_path}")

    # CP30: 200 (no 500).
    ok &= _assert(30, "GET /page/dictation/timing (absent) returns 200", resp.status_code == 200)

    # CP31: contains "Server breakdown unavailable".
    ok &= _assert(31, 'HTML (absent) contains "Server breakdown unavailable"', "Server breakdown unavailable" in html_absent)

    # CP32: still has the panel id.
    ok &= _assert(32, 'HTML (absent) contains id="dictation-timing"', 'id="dictation-timing"' in html_absent)

    log.info("PHASE 3 %s", "PASS" if ok else "FAIL")
    return ok


# ---------------------------------------------------------------------------
# Summary + exit
# ---------------------------------------------------------------------------


def _summarize_and_exit() -> int:
    summary = {
        "checkpoints": [
            {
                "n": n,
                "label": label,
                "result": ("SKIPPED" if result is None else ("PASS" if result else "FAIL")),
                "blocking": blocking,
            }
            for n, label, result, blocking in _checkpoints
        ]
    }
    json_path = OUT_DIR / "dictation_timing_e2e.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("JSON summary: %s", json_path)

    print("\n" + "=" * 72)
    print("DICTATION TIMING E2E — CHECKPOINT SUMMARY")
    print("=" * 72)
    hard_fail = 0
    for n, label, result, blocking in _checkpoints:
        if result is None:
            status = "SKIPPED"
        elif result:
            status = "PASS"
        else:
            status = "FAIL"
            if blocking:
                hard_fail += 1
        nb_tag = " [non-blocking]" if not blocking else ""
        print(f"  CP {n:>2}: {status:<8}  {label}{nb_tag}")
    print("=" * 72)
    print(f"Evidence dir : {OUT_DIR}")
    print(f"Log          : {LOG_PATH}")
    print(f"JSON summary : {json_path}")
    print("=" * 72)
    if hard_fail == 0:
        print("RESULT: ALL HARD CHECKPOINTS PASSED")
    else:
        print(f"RESULT: {hard_fail} HARD CHECKPOINT(S) FAILED")
    print("=" * 72 + "\n")
    return 0 if hard_fail == 0 else 1


def main() -> int:
    log.info("dictation timing E2E harness starting (ROOT=%s)", ROOT)
    log.info("evidence dir: %s", OUT_DIR)

    tmp_root = Path(tempfile.mkdtemp(prefix="vc_timing_e2e_"))
    log.info("isolated tmp dir: %s", tmp_root)
    try:
        p1 = phase_1_data_path(tmp_root)
        p2 = phase_2_web_pixels(tmp_root)
        p3 = phase_3_graceful_degradation(tmp_root)
        log.info(
            "=== SUMMARY: phase1=%s phase2=%s phase3=%s ===",
            "PASS" if p1 else "FAIL",
            "PASS" if p2 else "FAIL",
            "PASS" if p3 else "FAIL",
        )
    finally:
        try:
            shutil.rmtree(tmp_root, ignore_errors=True)
        except Exception:
            log.warning("could not clean up tmp dir %s", tmp_root, exc_info=True)

    return _summarize_and_exit()


if __name__ == "__main__":
    sys.exit(main())
