"""E2E harness for Elements Mode (ADR 0087).

Exercises the full elements-mode flow through the real StreamingDaemon object
— real ``_process_utterance``, real ``ElementsSession``, real ``EventBus``,
real worker executors — with only the OS-touching boundaries stubbed out.

Flow exercised
--------------
1. Open a simulated voice session (``_session_active = True``).
2. Stub the OS boundaries: ``desktop.foreground_window``,
   ``desktop.monitor_rect``, ``scanner.scan_window``, ``clicker.click_point``.
   Subscribe to the EventBus to capture ``elements.show`` / ``elements.hide``.
3. Drive "elements" through ``_process_utterance`` → ``begin_scan()`` →
   worker calls ``_do_element_scan`` → ``show()`` → ``elements.show`` event.
4. Assert: state == HINTS_SHOWN, event published, 5 elements, correct monitor.
5. Drive "three" through ``_process_utterance`` → ``handle_utterance`` →
   worker calls ``_do_element_click`` → ``elements.hide`` event.
6. Assert: clicker called with correct center, ``elements.hide`` published,
   state == IDLE.
7. Cancel path: repeat scan to HINTS_SHOWN, then drive "never mind" →
   implicit cancel — assert IDLE, ``elements.hide`` published, NO click.

Hard checkpoints (exit non-zero if any fail):
  CP1  "elements" utterance transitions session out of IDLE
  CP2  elements.show event published on EventBus
  CP3  elements.show event carries the fake monitor rect
  CP4  elements.show event carries 5 elements
  CP5  ElementsSession state == HINTS_SHOWN after scan
  CP6  clicker.click_point was called (click submitted)
  CP7  clicker.click_point called with center of element[3]
  CP8  elements.hide event published after click path
  CP9  ElementsSession state == IDLE after click
  CP10 [cancel path] second scan reaches HINTS_SHOWN
  CP11 "never mind" produces elements.hide without a click
  CP12 ElementsSession state == IDLE after cancel path

Output dir: outputs/elements_mode_e2e/
Exits 0 only when all hard checkpoints pass.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Repo root + path setup
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / "outputs" / "elements_mode_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT_DIR / "elements_mode_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("elements_mode_e2e")

# ---------------------------------------------------------------------------
# Stub constants
# ---------------------------------------------------------------------------

_FAKE_HWND = 99999
_FAKE_MONITOR = (0, 0, 1920, 1080)

# ---------------------------------------------------------------------------
# Checkpoint registry
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


# ---------------------------------------------------------------------------
# Stub transcriber that returns fixed TranscriptionResult objects
# ---------------------------------------------------------------------------


@dataclass
class _StubTranscriber:
    """Returns pre-loaded TranscriptionResult objects in sequence."""

    _queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...

    def transcribe(self, _audio: np.ndarray) -> Any:
        if self._queue:
            return self._queue.pop(0)
        # Fallback — low confidence, noise
        from voice_commander.transcriber import TranscriptionResult
        return TranscriptionResult(
            text="(empty)",
            language="en",
            duration_ms=0,
            confidence=0.99,
            no_speech_prob=0.0,
        )


# ---------------------------------------------------------------------------
# Fake Element factory
# ---------------------------------------------------------------------------


def _make_fake_elements(n: int = 5):
    """Build *n* fake Element instances with predictable geometry."""
    from voice_commander.elements.scanner import Element

    elements = []
    for i in range(1, n + 1):
        x, y = i * 100, i * 50
        w, h = 80, 30
        elements.append(
            Element(
                index=i,
                label=f"Button {i}",
                control_type="ButtonControl",
                rect=(x, y, w, h),
                center=(x + w // 2, y + h // 2),
            )
        )
    return elements


# ---------------------------------------------------------------------------
# EventBus subscriber helper
# ---------------------------------------------------------------------------


def _drain_events(eq: queue.Queue, timeout_s: float = 0.0) -> list[Any]:
    """Pull all currently queued events; optionally wait up to timeout_s for the first."""
    events = []
    if timeout_s > 0:
        try:
            events.append(eq.get(timeout=timeout_s))
        except queue.Empty:
            return []
    while True:
        try:
            events.append(eq.get_nowait())
        except queue.Empty:
            break
    return events


# ---------------------------------------------------------------------------
# Daemon builder
# ---------------------------------------------------------------------------


def _build_daemon(
    transcripts: list[Any],
    out_dir: Path,
) -> tuple[Any, Any, Any, Any]:
    """Build a stripped-down StreamingDaemon for elements-mode testing.

    Returns (daemon, elements_session, feedback, bus).
    """
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.elements.session import ElementsSession
    from voice_commander.event_bus import EventBus
    from voice_commander.feedback import CapturingFeedbackSink
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry
    from voice_commander.verb_router import VerbRouter, build_default_rules

    reset_global_registry()
    reset_global_picker_registry()

    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(bus=bus, end_word="done")
    # Generous timeout so the auto-dismiss timer never fires during the test
    elements_session = ElementsSession(bus=bus, hint_timeout_s=30.0)

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(_queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        elements_session=elements_session,
        output_dir=str(out_dir),
    )
    daemon._transcriber_ready.set()
    daemon._session_active = True  # simulate an open voice session
    return daemon, elements_session, feedback, bus


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------


def run() -> int:
    """Run all checkpoints. Returns 0 if all hard checkpoints pass."""
    from voice_commander.elements import clicker, desktop, scanner
    from voice_commander.transcriber import TranscriptionResult

    # ------------------------------------------------------------------
    # Prepare fake elements and stubs
    # ------------------------------------------------------------------
    fake_elements = _make_fake_elements(5)

    clicked_at: list[tuple[int, int]] = []
    click_count_before_cancel: int = 0  # will be captured mid-run

    # Monkeypatched originals (restored in finally)
    _orig_foreground_window = desktop.foreground_window
    _orig_monitor_rect = desktop.monitor_rect
    _orig_scan_window = scanner.scan_window
    _orig_click_point = clicker.click_point

    def _stub_foreground_window() -> int:
        log.info("stub foreground_window() → %d", _FAKE_HWND)
        return _FAKE_HWND

    def _stub_monitor_rect(hwnd: int) -> tuple[int, int, int, int]:
        log.info("stub monitor_rect(%d) → %s", hwnd, _FAKE_MONITOR)
        return _FAKE_MONITOR

    def _stub_scan_window(hwnd: int, *, max_elements: int = 200, timeout_s: float = 3.0):
        log.info(
            "stub scan_window(hwnd=%d, max_elements=%d) → %d elements",
            hwnd,
            max_elements,
            len(fake_elements),
        )
        return fake_elements

    def _stub_click_point(x: int, y: int, *, settle_ms: int = 50) -> None:
        log.info("stub click_point(%d, %d)", x, y)
        clicked_at.append((x, y))

    desktop.foreground_window = _stub_foreground_window  # type: ignore[assignment]
    desktop.monitor_rect = _stub_monitor_rect  # type: ignore[assignment]
    scanner.scan_window = _stub_scan_window  # type: ignore[assignment]
    clicker.click_point = _stub_click_point  # type: ignore[assignment]

    # Preload transcription results (one per _process_utterance call in order):
    # 1. "elements" — triggers scan
    # 2. "three"    — triggers click (element index 3)
    # 3. "elements" — second scan (cancel path)
    # 4. "never mind" — non-number cancel
    transcripts = [
        TranscriptionResult(text="elements", language="en", duration_ms=300,
                            confidence=0.95, no_speech_prob=0.01),
        TranscriptionResult(text="three", language="en", duration_ms=200,
                            confidence=0.95, no_speech_prob=0.01),
        TranscriptionResult(text="elements", language="en", duration_ms=300,
                            confidence=0.95, no_speech_prob=0.01),
        TranscriptionResult(text="never mind", language="en", duration_ms=400,
                            confidence=0.95, no_speech_prob=0.01),
    ]

    audio = np.zeros(16000, dtype=np.float32)

    pipeline_thread: threading.Thread | None = None

    try:
        # ------------------------------------------------------------------
        # Build daemon
        # ------------------------------------------------------------------
        daemon, elements_session, feedback, bus = _build_daemon(
            transcripts=transcripts,
            out_dir=OUT_DIR / "store",
        )

        # Subscribe to EventBus to capture events
        event_q = bus.subscribe()

        # ------------------------------------------------------------------
        # Start pipeline loop thread
        # ------------------------------------------------------------------
        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop,
            name="vc-pipeline-elements-e2e",
            daemon=True,
        )
        pipeline_thread.start()
        log.info("pipeline thread started")

        # ==================================================================
        # PATH 1 — Scan and click
        # ==================================================================

        # ------------------------------------------------------------------
        # Drive utterance 1: "elements"
        # ------------------------------------------------------------------
        log.info("--- Driving utterance: 'elements' ---")
        daemon._utt_q.put((audio, daemon._audio_gen))

        # Wait briefly for _process_utterance to be called and begin_scan()
        # to be called (it happens synchronously on the pipeline thread before
        # the executor is submitted).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if elements_session.state.name != "IDLE":
                break
            time.sleep(0.02)

        # CP1: session transitioned to SCANNING (or already past it to HINTS_SHOWN)
        state_after_entry = elements_session.state.name
        log.info("state after 'elements' utterance: %s", state_after_entry)
        cp1 = state_after_entry in ("SCANNING", "HINTS_SHOWN")
        _record(1, "'elements' utterance transitions session out of IDLE", cp1)

        # Wait for scan worker to finish (HINTS_SHOWN state or elements.show event)
        deadline = time.monotonic() + 5.0
        show_events = []
        while time.monotonic() < deadline:
            evts = _drain_events(event_q, timeout_s=0.1)
            show_events += [e for e in evts if e.type == "elements.show"]
            if show_events:
                break
            if elements_session.state.name == "HINTS_SHOWN":
                # Event may have already landed; drain once more
                evts = _drain_events(event_q, timeout_s=0.05)
                show_events += [e for e in evts if e.type == "elements.show"]
                break
        log.info("elements.show events captured: %d", len(show_events))

        # CP2: elements.show event published
        cp2 = len(show_events) >= 1
        _record(2, "elements.show event published on EventBus", cp2)

        # CP3 + CP4: inspect the event payload
        if cp2:
            ev_data = show_events[0].data
            published_monitor = tuple(ev_data.get("monitor", []))
            published_elements = ev_data.get("elements", [])
            cp3 = published_monitor == _FAKE_MONITOR
            log.info("monitor in event: %s (expected %s)", published_monitor, _FAKE_MONITOR)
            _record(3, "elements.show carries correct fake monitor rect", cp3)

            cp4 = len(published_elements) == 5
            log.info("elements count in event: %d (expected 5)", len(published_elements))
            _record(4, "elements.show carries 5 elements", cp4)
        else:
            _record(3, "elements.show carries correct fake monitor rect", False)
            _record(4, "elements.show carries 5 elements", False)
            published_elements = []

        # CP5: ElementsSession state == HINTS_SHOWN
        state_hints = elements_session.state.name
        log.info("ElementsSession state after scan: %s", state_hints)
        _record(5, "ElementsSession state == HINTS_SHOWN after scan", state_hints == "HINTS_SHOWN")

        # ------------------------------------------------------------------
        # Drive utterance 2: "three"
        # ------------------------------------------------------------------
        log.info("--- Driving utterance: 'three' ---")
        daemon._utt_q.put((audio, daemon._audio_gen))

        # Wait for click worker to finish
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if clicked_at:
                break
            time.sleep(0.05)

        # Give elements.hide event a moment to land
        hide_events_click: list[Any] = []
        deadline2 = time.monotonic() + 1.0
        while time.monotonic() < deadline2:
            evts = _drain_events(event_q, timeout_s=0.05)
            hide_events_click += [e for e in evts if e.type == "elements.hide"]
            if hide_events_click:
                break

        log.info("clicked_at: %s", clicked_at)

        # CP6: click was submitted (clicked_at non-empty)
        cp6 = len(clicked_at) >= 1
        _record(6, "clicker.click_point was called (click submitted)", cp6)

        # CP7: clicker called with center of element[3] (index 3, i.e. list position 2)
        expected_center = fake_elements[2].center  # index=3 → position 2 (0-based)
        if cp6:
            cp7 = clicked_at[0] == expected_center
            log.info("click coords: %s (expected %s)", clicked_at[0], expected_center)
        else:
            cp7 = False
            log.error("no click recorded — cannot verify center")
        _record(7, f"click_point called with center of element #3 {expected_center}", cp7)

        # CP8: elements.hide published after click
        cp8 = len(hide_events_click) >= 1
        log.info("elements.hide events (click path): %d", len(hide_events_click))
        _record(8, "elements.hide event published after click path", cp8)

        # CP9: ElementsSession back to IDLE
        state_after_click = elements_session.state.name
        log.info("ElementsSession state after click: %s", state_after_click)
        _record(9, "ElementsSession state == IDLE after click", state_after_click == "IDLE")

        # ==================================================================
        # PATH 2 — Cancel (non-number utterance)
        # ==================================================================

        click_count_before_cancel = len(clicked_at)
        log.info("--- Cancel path: driving second 'elements' scan ---")

        # ------------------------------------------------------------------
        # Drive utterance 3: "elements" (second scan)
        # ------------------------------------------------------------------
        daemon._utt_q.put((audio, daemon._audio_gen))

        # Wait for HINTS_SHOWN
        deadline = time.monotonic() + 5.0
        show_events_2: list[Any] = []
        while time.monotonic() < deadline:
            evts = _drain_events(event_q, timeout_s=0.1)
            show_events_2 += [e for e in evts if e.type == "elements.show"]
            if elements_session.state.name == "HINTS_SHOWN":
                evts = _drain_events(event_q, timeout_s=0.05)
                show_events_2 += [e for e in evts if e.type == "elements.show"]
                break

        state_second_scan = elements_session.state.name
        log.info("state after second 'elements' utterance: %s", state_second_scan)
        cp10 = state_second_scan == "HINTS_SHOWN"
        _record(10, "second scan reaches HINTS_SHOWN (cancel path setup)", cp10)

        # ------------------------------------------------------------------
        # Drive utterance 4: "never mind"
        # ------------------------------------------------------------------
        log.info("--- Driving utterance: 'never mind' (cancel) ---")
        daemon._utt_q.put((audio, daemon._audio_gen))

        # Wait for elements.hide
        hide_events_cancel: list[Any] = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            evts = _drain_events(event_q, timeout_s=0.1)
            hide_events_cancel += [e for e in evts if e.type == "elements.hide"]
            if hide_events_cancel:
                break

        # Allow click worker a moment (it must NOT fire for the cancel path)
        time.sleep(0.3)
        daemon._elements_executor.shutdown(wait=True)

        # CP11: elements.hide published AND no additional click
        clicks_after_cancel = len(clicked_at) - click_count_before_cancel
        cp11_hide = len(hide_events_cancel) >= 1
        cp11_no_click = clicks_after_cancel == 0
        cp11 = cp11_hide and cp11_no_click
        log.info(
            "cancel path: hide_events=%d  extra_clicks=%d",
            len(hide_events_cancel),
            clicks_after_cancel,
        )
        _record(11, "cancel path: elements.hide published AND no click fired", cp11)

        # CP12: ElementsSession back to IDLE
        state_after_cancel = elements_session.state.name
        log.info("ElementsSession state after cancel: %s", state_after_cancel)
        _record(12, "ElementsSession state == IDLE after cancel path", state_after_cancel == "IDLE")

    finally:
        # ------------------------------------------------------------------
        # Restore monkeypatches
        # ------------------------------------------------------------------
        desktop.foreground_window = _orig_foreground_window  # type: ignore[assignment]
        desktop.monitor_rect = _orig_monitor_rect  # type: ignore[assignment]
        scanner.scan_window = _orig_scan_window  # type: ignore[assignment]
        clicker.click_point = _orig_click_point  # type: ignore[assignment]

        # Shut down pipeline thread
        if pipeline_thread is not None and pipeline_thread.is_alive():
            try:
                daemon._utt_q.put(None)
                pipeline_thread.join(timeout=3.0)
                if pipeline_thread.is_alive():
                    log.warning("pipeline thread did not exit within 3 s")
            except Exception:
                log.warning("error shutting down pipeline thread", exc_info=True)

        # Shutdown remaining executors
        with contextlib.suppress(Exception):
            daemon._wav_executor.shutdown(wait=False)
        with contextlib.suppress(Exception):
            daemon._dictation_executor.shutdown(wait=False)

        log.info("cleanup complete")

    # ------------------------------------------------------------------
    # Write JSON summary
    # ------------------------------------------------------------------
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
    json_path = OUT_DIR / "elements_mode_e2e.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("JSON summary: %s", json_path)

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("ELEMENTS MODE E2E — CHECKPOINT SUMMARY")
    print("=" * 70)
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
    print("=" * 70)
    print(f"Evidence dir: {OUT_DIR}")
    print(f"Log:          {LOG_PATH}")
    print(f"JSON:         {json_path}")
    print("=" * 70)
    if hard_fail == 0:
        print("RESULT: ALL HARD CHECKPOINTS PASSED")
    else:
        print(f"RESULT: {hard_fail} HARD CHECKPOINT(S) FAILED")
    print("=" * 70 + "\n")

    return 0 if hard_fail == 0 else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
