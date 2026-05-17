"""Render-smoke harness for ADR 0089 — dictation hotkey-end sentinel + spoken cancel.

Mandatory per docs/agents/visual-e2e-testing.md Rule 2 (stage the harness).
This script is Phase 1 (in-process, no subprocess). Proves:
  - Sentinel wakes the pipeline thread within 2 s with no trailing utterance.
  - Spoken cancel via _process_utterance() produces no POST, no paste, and emits
    dictation.end {reason:'cancel'}.
  - StateMachine.cancelled_cue is set after dictation.end {reason:'cancel'}.

Run BEFORE scripts/dictation_hotkey_cancel_e2e.py. Exits 0 on full PASS.

Outputs:
  outputs/dictation_smoke.log  — full validation log
  outputs/dictation_smoke.json — assertion summary (machine-readable)
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT / "dictation_smoke.log"
JSON_PATH = OUT / "dictation_smoke.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_smoke")


# ---------------------------------------------------------------------------
# Phase A — Sentinel wake: hotkey-end finalizes without trailing utterance
# ---------------------------------------------------------------------------


def phase_a(tmp_path: Path) -> bool:
    log.info("=== PHASE A: hotkey-end sentinel wake ===")
    import numpy as np

    finalized = threading.Event()

    import voice_commander.dictation.clipboard as _clipboard
    import voice_commander.dictation.remote as _remote

    original_post = _remote.post_audio
    original_paste = _clipboard.paste_via_clipboard

    _remote.post_audio = lambda *a, **kw: "SENTINEL TEXT"
    _clipboard.paste_via_clipboard = lambda *a, **kw: finalized.set()

    ok = False
    daemon = None
    pipeline_thread = None
    try:
        @dataclass
        class _T:
            text: str
            confidence: float = 0.95
            no_speech_prob: float = 0.05

        class _SentinelStub:
            def __init__(self) -> None:
                self._result = _T("some dictated words")

            def load(self) -> None: ...
            def unload(self) -> None: ...

            def transcribe(self, _: object) -> _T:
                return self._result

        from voice_commander.daemon import StreamingDaemon
        from voice_commander.dictation.session import DictationSession
        from voice_commander.dispatcher import Dispatcher
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

        daemon = StreamingDaemon(
            feedback=feedback,
            recorder=None,
            transcriber=_SentinelStub(),
            dispatcher=dispatcher,
            verb_router=verb_router,
            registry=registry,
            event_bus=bus,
            dictation_session=dictation_session,
            output_dir=str(tmp_path),
        )
        daemon._transcriber_ready.set()

        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop, name="vc-pipeline-smoke-a", daemon=True
        )
        daemon._session_active = True
        pipeline_thread.start()

        dictation_session.start()
        audio = np.zeros(16000, dtype=np.float32)
        daemon._utt_q.put((audio, daemon._audio_gen))

        # Give the pipeline thread time to dequeue and transcribe the utterance,
        # then re-enter blocking get().
        time.sleep(0.3)

        log.info("firing hotkey-end with no trailing utterance...")
        t0 = time.monotonic()
        daemon.on_dictation_toggle()

        if not finalized.wait(timeout=3.0):
            log.error(
                "FAIL: dictation not finalized within 3 s — sentinel wake not working"
            )
            return False

        elapsed_ms = (time.monotonic() - t0) * 1000
        log.info("PASS: finalized in %.0f ms", elapsed_ms)
        ok = True
        return True

    finally:
        _remote.post_audio = original_post
        _clipboard.paste_via_clipboard = original_paste
        if daemon is not None:
            try:
                daemon._utt_q.put(None)
            except Exception:
                pass
            if pipeline_thread is not None:
                pipeline_thread.join(timeout=3.0)
            daemon._dictation_executor.shutdown(wait=False)
            daemon._wav_executor.shutdown(wait=False)
        log.info("PHASE A %s", "PASS" if ok else "FAIL")


# ---------------------------------------------------------------------------
# Phase B — Spoken cancel: no paste, SSE reason="cancel"
# ---------------------------------------------------------------------------


def phase_b(tmp_path: Path) -> bool:
    log.info("=== PHASE B: spoken cancel — no paste, SSE reason='cancel' ===")
    import numpy as np

    posted: list[str] = []
    pasted: list[str] = []

    import voice_commander.dictation.clipboard as _clipboard
    import voice_commander.dictation.remote as _remote

    original_post = _remote.post_audio
    original_paste = _clipboard.paste_via_clipboard

    _remote.post_audio = lambda *a, **kw: (posted.append("posted"), "X")[1]
    _clipboard.paste_via_clipboard = lambda *a, **kw: pasted.append("pasted")

    ok = False
    daemon = None
    try:
        @dataclass
        class _T:
            text: str
            confidence: float = 0.95
            no_speech_prob: float = 0.05

        class _StubTranscriber:
            def __init__(self, q: list[_T]) -> None:
                self._q = q

            def load(self) -> None: ...
            def unload(self) -> None: ...

            def transcribe(self, _: object) -> _T:
                return self._q.pop(0)

        from voice_commander.daemon import StreamingDaemon
        from voice_commander.dictation.session import DictationSession
        from voice_commander.dispatcher import Dispatcher
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
        dictation_session = DictationSession(bus=bus, end_word="done", cancel_word="cancel")

        transcripts = [_T("hello there"), _T("cancel")]
        daemon = StreamingDaemon(
            feedback=feedback,
            recorder=None,
            transcriber=_StubTranscriber(transcripts),
            dispatcher=dispatcher,
            verb_router=verb_router,
            registry=registry,
            event_bus=bus,
            dictation_session=dictation_session,
            output_dir=str(tmp_path),
        )
        daemon._transcriber_ready.set()

        # Subscribe to the event bus BEFORE driving utterances so we capture all events.
        event_q = bus.subscribe()

        audio = np.zeros(8000, dtype=np.float32)
        dictation_session.start()

        # Drive through the real _process_utterance dispatch path (same as integration tests).
        daemon._process_utterance(audio)  # "hello there" → buffered
        daemon._process_utterance(audio)  # "cancel" → cancel() via _process_utterance

        # Finalize executor — must be a no-op (nothing submitted)
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

        if posted:
            log.error("FAIL: post_audio was called — must NOT be: %s", posted)
            return False
        log.info("PASS: post_audio not called")

        if pasted:
            log.error("FAIL: clipboard was pasted — must NOT be: %s", pasted)
            return False
        log.info("PASS: clipboard not pasted")

        # Drain event queue and check for dictation.end with reason="cancel"
        events: list[tuple[str, dict]] = []
        while not event_q.empty():
            ev = event_q.get_nowait()
            events.append((ev.type, ev.data))

        cancel_events = [
            (et, d) for (et, d) in events
            if et == "dictation.end" and d.get("reason") == "cancel"
        ]
        if not cancel_events:
            log.error(
                "FAIL: dictation.end {reason:'cancel'} not emitted; events=%s", events
            )
            return False
        log.info("PASS: dictation.end reason='cancel' emitted: %s", cancel_events[0])

        # Extra assertion: session must be inactive
        if dictation_session.active:
            log.error("FAIL: dictation_session.active is still True after cancel")
            return False
        log.info("PASS: dictation_session.active=False")

        ok = True
        return True

    finally:
        _remote.post_audio = original_post
        _clipboard.paste_via_clipboard = original_paste
        if daemon is not None:
            daemon._dictation_executor.shutdown(wait=False)
            daemon._wav_executor.shutdown(wait=False)
        log.info("PHASE B %s", "PASS" if ok else "FAIL")


# ---------------------------------------------------------------------------
# Phase C — Sprite state machine reads cancel reason
# ---------------------------------------------------------------------------


def phase_c() -> bool:
    log.info("=== PHASE C: sprite state machine cancel cue ===")
    ok = False
    try:
        from voice_sprite.state_machine import StateMachine

        # Normal cancel: dictation.end {reason:"cancel"} → cancelled_cue=True
        sm = StateMachine()
        sm.on_event("dictation.start", {})
        sm.on_event("dictation.end", {"reason": "cancel"})

        if not sm.cancelled_cue:
            log.error("FAIL: cancelled_cue not True after reason='cancel'")
            return False
        log.info("PASS: cancelled_cue=True after reason='cancel'")

        if sm.dictating:
            log.error("FAIL: dictating should be False after dictation.end")
            return False
        log.info("PASS: dictating=False")

        # Normal end: dictation.end {reason:"done"} → cancelled_cue=False
        sm2 = StateMachine()
        sm2.on_event("dictation.start", {})
        sm2.on_event("dictation.end", {"reason": "done"})
        if sm2.cancelled_cue:
            log.error("FAIL: cancelled_cue should be False after reason='done'")
            return False
        log.info("PASS: cancelled_cue=False after reason='done'")

        # Missing reason key — must not KeyError (defensive access)
        sm3 = StateMachine()
        sm3.on_event("dictation.start", {})
        try:
            sm3.on_event("dictation.end", {})
        except KeyError as e:
            log.error("FAIL: KeyError on missing 'reason' key: %s", e)
            return False
        log.info("PASS: no KeyError on missing 'reason' key")

        # New session clears prior cancelled_cue
        sm4 = StateMachine()
        sm4.on_event("dictation.start", {})
        sm4.on_event("dictation.end", {"reason": "cancel"})
        assert sm4.cancelled_cue, "sanity: must be True before new start"
        sm4.on_event("dictation.start", {})  # new session — clears cue
        if sm4.cancelled_cue:
            log.error("FAIL: cancelled_cue not cleared on new dictation.start")
            return False
        log.info("PASS: cancelled_cue cleared on new dictation.start")

        ok = True
        return True
    finally:
        log.info("PHASE C %s", "PASS" if ok else "FAIL")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    import tempfile

    results: dict[str, bool] = {}

    with tempfile.TemporaryDirectory() as tmp:
        results["phase_a_sentinel_wake"] = phase_a(Path(tmp))

    with tempfile.TemporaryDirectory() as tmp:
        results["phase_b_spoken_cancel"] = phase_b(Path(tmp))

    results["phase_c_sprite_cancel_cue"] = phase_c()

    all_pass = all(results.values())

    log.info(
        "=== SUMMARY: %s ===",
        " | ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items()),
    )
    JSON_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info("Evidence written to %s", LOG_PATH)
    log.info("Assertion summary: %s", JSON_PATH)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
