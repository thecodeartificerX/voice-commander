"""Human-validated E2E harness for the streaming dictation path (ADR 0092).

REQUIREMENTS — run this manually only:
  - A live ``/ws/transcribe`` WebSocket server reachable at the URL in
    ``config.toml`` ([dictation] ws_url, default ws://192.168.4.200:8765/ws/transcribe).
  - A working microphone.
  - The daemon's Python environment (``uv run`` or activated venv).

This harness exercises the full streaming dictation lifecycle through the real
pipeline objects:

  1. Loads ``config.toml`` to read the production ``ws_url``.
  2. Builds a stripped-down StreamingDaemon (stub transcriber, no recorder,
     no HotkeyController) wired to the real WebSocket URL.
  3. Starts the pipeline loop thread.
  4. Simulates a Right Ctrl press via ``on_dictation_toggle()`` (opens an
     owned session and enters dictation).
  5. Prompts the operator to speak a known sentence aloud.
  6. Waits for the session to open, then simulates the end-word path by
     calling ``handle_utterance`` with the configured ``end_word`` to
     terminate dictation.
  7. Captures evidence: the sequence of EventBus events (``dictation.start``,
     ``dictation.end``, ``dictation.result``, ``transcript``), the pasted
     transcript text, and the daemon log.
  8. Asserts on that evidence (hard checkpoints) and prints a PASS/FAIL
     summary.

Hard checkpoints (exit non-zero if any fail):
  CP1  dictation.start event published after on_dictation_toggle()
  CP2  dictation.end {reason:"done"} event published after end-word utterance
  CP3  dictation.result event published (non-empty transcript produced)
  CP4  paste_via_clipboard was called (pasted list non-empty)
  CP5  pasted text is non-empty string
  CP6  transcript text from dictation.result matches operator-confirmed sentence
       (fuzzy substring match — Whisper is not deterministic)
  CP7  session.active == False after finalize (session deactivated)
  CP8  clipboard restored to sentinel value after harness

Non-blocking (evidence only):
  CP9  sprite gold-badge visual (skipped gracefully — harness does not launch sprite)

Output dir: outputs/dictation_streaming_e2e/
Exits 0 only when all HARD checkpoints pass.

NOTE: This is NOT a pytest test. Run it manually:
    python scripts/dictation_streaming_e2e.py
"""

from __future__ import annotations

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

OUT_DIR = ROOT / "outputs" / "dictation_streaming_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT_DIR / "dictation_streaming_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_streaming_e2e")

# ---------------------------------------------------------------------------
# Operator-known test sentence (substring match used in CP6)
# ---------------------------------------------------------------------------

_EXPECTED_SENTENCE = "the quick brown fox"
_SENTINEL_CLIPBOARD = "__dictation_streaming_e2e_sentinel_12345__"

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
# Stub transcriber — returns a deterministic transcript
# ---------------------------------------------------------------------------


@dataclass
class _StubTranscription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    """Returns a fixed transcription sequence.  Thread-safe (list.pop is atomic in CPython)."""

    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...

    def transcribe(self, _audio: np.ndarray) -> _StubTranscription:
        if self.queue:
            return self.queue.pop(0)
        return _StubTranscription(text="(empty)", confidence=0.99)


# ---------------------------------------------------------------------------
# Event collector — drains the EventBus subscriber queue into a list
# ---------------------------------------------------------------------------


def _collect_events(
    bus_queue: "queue.Queue[Any]",
    collected: list[Any],
    stop_event: threading.Event,
) -> None:
    """Thread target: drain bus_queue into collected until stop_event is set."""
    while not stop_event.is_set():
        try:
            ev = bus_queue.get(timeout=0.1)
            collected.append(ev)
            log.debug("event collected: type=%s data=%s", ev.type, ev.data)
        except queue.Empty:
            pass


# ---------------------------------------------------------------------------
# Daemon builder (in-process, no subprocess)
# ---------------------------------------------------------------------------


def _build_daemon(
    ws_url: str,
    end_word: str,
    pasted: list[str],
    out_dir: Path,
) -> tuple[Any, Any, Any, Any]:
    """Construct a stripped-down StreamingDaemon for streaming dictation testing.

    Returns (daemon, dictation_session, feedback, bus).
    """
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dictation.vocab import Vocabulary
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

    dictation_session = DictationSession(
        bus=bus,
        ws_url=ws_url,
        end_word=end_word,
        cancel_word="cancel",
    )

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=[]),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        output_dir=str(out_dir),
    )
    daemon._transcriber_ready.set()
    daemon._session_active = True  # simulate an open voice session
    return daemon, dictation_session, feedback, bus


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------


def run() -> int:
    """Run all checkpoints. Returns 0 if all hard checkpoints pass."""
    import voice_commander.dictation.clipboard as _clipboard_mod
    from voice_commander.config import Config
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dictation.vocab import Vocabulary

    # ------------------------------------------------------------------
    # Load config to get the production ws_url and end_word
    # ------------------------------------------------------------------
    config_path = ROOT / "config.toml"
    if config_path.exists():
        cfg = Config.load(config_path)
        ws_url = cfg.dictation.ws_url
        end_word = cfg.dictation.end_word
        log.info("Loaded config: ws_url=%s end_word=%r", ws_url, end_word)
    else:
        # Fallback to defaults if config.toml is not present
        ws_url = "ws://192.168.4.200:8765/ws/transcribe"
        end_word = "done"
        log.warning("config.toml not found at %s; using defaults", config_path)

    # ------------------------------------------------------------------
    # Operator instructions
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("STREAMING DICTATION E2E HARNESS — OPERATOR SETUP")
    print("=" * 70)
    print(f"WebSocket URL : {ws_url}")
    print(f"End word      : {end_word!r}")
    print(f"Expected text : {_EXPECTED_SENTENCE!r} (substring match)")
    print()
    print("REQUIREMENTS:")
    print("  1. The /ws/transcribe server at the URL above MUST be running.")
    print("  2. A working microphone must be connected.")
    print()
    print("WHAT YOU WILL DO:")
    print(f'  a. When prompted, speak: "{_EXPECTED_SENTENCE}"')
    print(f'  b. Then say "{end_word}" to end dictation.')
    print()
    input("Press ENTER when the server is running and you are ready...")
    print()

    # ------------------------------------------------------------------
    # Monkey-patch paste_via_clipboard to capture what would be pasted
    # (avoids polluting the real clipboard with test text mid-run)
    # ------------------------------------------------------------------
    pasted: list[str] = []
    _orig_paste = _clipboard_mod.paste_via_clipboard

    def _stub_paste(text: str, **kw: Any) -> None:
        log.info("paste_via_clipboard intercepted — %r", text)
        pasted.append(text)

    _clipboard_mod.paste_via_clipboard = _stub_paste  # type: ignore[assignment]

    # Snapshot the real clipboard and set a sentinel so CP8 can verify
    # restoration without corrupting the user's actual clipboard.
    try:
        pre_clipboard = _clipboard_mod.read_clipboard_text()
        _clipboard_mod.set_clipboard_text(_SENTINEL_CLIPBOARD)
        log.info("sentinel clipboard set: %r", _SENTINEL_CLIPBOARD)
    except Exception:
        pre_clipboard = None
        log.warning("could not read/set clipboard (non-fatal)", exc_info=True)

    pipeline_thread: threading.Thread | None = None
    event_collector_thread: threading.Thread | None = None
    collected_events: list[Any] = []
    stop_collector = threading.Event()
    event_q: Any = None
    bus: Any = None

    try:
        # ------------------------------------------------------------------
        # Build daemon wired to the real ws_url
        # ------------------------------------------------------------------
        daemon, dictation_session, feedback, bus = _build_daemon(
            ws_url=ws_url,
            end_word=end_word,
            pasted=pasted,
            out_dir=OUT_DIR / "store",
        )

        # Subscribe to all events BEFORE starting the pipeline so no events
        # are lost in the gap (EventBus.subscribe is thread-safe).
        event_q = bus.subscribe()

        # Start event collector thread
        event_collector_thread = threading.Thread(
            target=_collect_events,
            args=(event_q, collected_events, stop_collector),
            name="vc-event-collector-e2e",
            daemon=True,
        )
        event_collector_thread.start()

        # ------------------------------------------------------------------
        # Start pipeline loop thread
        # ------------------------------------------------------------------
        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop,
            name="vc-pipeline-e2e",
            daemon=True,
        )
        pipeline_thread.start()
        log.info("pipeline thread started")

        # ------------------------------------------------------------------
        # Simulate on_dictation_toggle() — enters dictation.
        # Because _session_active=True, this calls dictation_session.start()
        # directly (the existing-session path in on_dictation_toggle).
        # ------------------------------------------------------------------
        log.info("calling on_dictation_toggle() to start dictation")
        daemon.on_dictation_toggle()

        # Brief pause to let the WS thread start and the start event publish
        time.sleep(0.3)

        # ------------------------------------------------------------------
        # CP1: dictation.start event received
        # ------------------------------------------------------------------
        start_events = [e for e in collected_events if e.type == "dictation.start"]
        cp1 = bool(start_events)
        log.info("dictation.start events received: %d", len(start_events))
        _record(1, "dictation.start event published", cp1)

        if not cp1:
            log.error("CP1 failed — dictation did not start. Aborting.")
            return _summarize_and_exit()

        # ------------------------------------------------------------------
        # Operator prompt: speak the expected sentence
        # ------------------------------------------------------------------
        print()
        print("-" * 70)
        print(f'>>> SPEAK NOW: "{_EXPECTED_SENTENCE}"')
        print(f'>>> Then say "{end_word}" to end dictation.')
        print("-" * 70)
        input("Press ENTER after you have finished speaking (including the end word)...")
        print()

        # ------------------------------------------------------------------
        # Simulate the end-word utterance on the pipeline: inject a bare
        # ndarray that the pipeline will feed to handle_utterance via
        # _process_utterance. We inject (audio, gen) tuple format that
        # _pipeline_loop expects.
        # ------------------------------------------------------------------
        audio = np.zeros(16000, dtype=np.float32)
        gen = daemon._audio_gen

        # First, we need the pipeline to process the end-word. We feed it
        # directly via the stub transcriber queue so _process_utterance's
        # transcriber.transcribe() call returns the end_word text, which
        # handle_utterance will classify as "end".
        # Inject the end-word utterance: set stub to return end_word text,
        # then enqueue audio so the pipeline processes it.
        daemon._transcriber.queue.append(  # type: ignore[attr-defined]
            _StubTranscription(text=end_word, confidence=0.99)
        )
        daemon._utt_q.put((audio, gen))

        log.info("end-word utterance injected into pipeline queue")

        # ------------------------------------------------------------------
        # Wait for the dictation executor to finish (finish() joins the
        # WS asyncio thread — this is the real blocking step against the
        # live server).
        # ------------------------------------------------------------------
        print()
        print("Waiting for the streaming server to return the transcript...")
        print("(This may take up to 60 seconds if the server is slow.)")
        print()

        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

        # Give event collector a moment to drain any late events
        time.sleep(0.5)
        stop_collector.set()
        if event_collector_thread is not None:
            event_collector_thread.join(timeout=2.0)

        # ------------------------------------------------------------------
        # CP2: dictation.end {reason:"done"} received
        # ------------------------------------------------------------------
        end_events = [
            e for e in collected_events
            if e.type == "dictation.end" and e.data.get("reason") == "done"
        ]
        cp2 = bool(end_events)
        log.info(
            "dictation.end{reason:done} events: %d",
            len(end_events),
        )
        _record(2, 'dictation.end {reason:"done"} event published', cp2)

        # ------------------------------------------------------------------
        # CP3: dictation.result event published (non-empty transcript)
        # ------------------------------------------------------------------
        result_events = [e for e in collected_events if e.type == "dictation.result"]
        result_text = result_events[0].data.get("text", "") if result_events else ""
        cp3 = bool(result_events) and bool(result_text)
        log.info("dictation.result events: %d, text=%r", len(result_events), result_text)
        _record(3, "dictation.result event with non-empty text", cp3)

        # ------------------------------------------------------------------
        # CP4: paste_via_clipboard was called
        # ------------------------------------------------------------------
        cp4 = bool(pasted)
        log.info("pasted list: %r", pasted)
        _record(4, "paste_via_clipboard was called (pasted list non-empty)", cp4)

        # ------------------------------------------------------------------
        # CP5: pasted text is non-empty
        # ------------------------------------------------------------------
        pasted_text = pasted[0] if pasted else ""
        cp5 = bool(pasted_text)
        log.info("pasted text: %r", pasted_text)
        _record(5, "pasted text is non-empty", cp5)

        # ------------------------------------------------------------------
        # CP6: transcript matches operator-confirmed sentence (fuzzy/substring)
        # ------------------------------------------------------------------
        # Primary source is result_text (from dictation.result event);
        # fall back to pasted_text in case event ordering differs.
        transcript_to_check = result_text or pasted_text
        operator_confirmed = _EXPECTED_SENTENCE.lower()

        # Accept if any significant word from the expected sentence appears
        expected_words = operator_confirmed.split()
        matched_words = sum(
            1 for w in expected_words
            if w in transcript_to_check.lower()
        )
        # Pass if at least half the expected words appear (Whisper ≠ deterministic)
        match_threshold = max(1, len(expected_words) // 2)
        cp6 = matched_words >= match_threshold
        log.info(
            "transcript match: %d/%d expected words found in %r (threshold=%d) → %s",
            matched_words,
            len(expected_words),
            transcript_to_check,
            match_threshold,
            "PASS" if cp6 else "FAIL",
        )
        _record(
            6,
            f"transcript contains ≥{match_threshold}/{len(expected_words)} expected words",
            cp6,
        )

        # ------------------------------------------------------------------
        # CP7: session.active == False after finalize
        # ------------------------------------------------------------------
        cp7 = not dictation_session.active
        log.info("dictation_session.active after finalize: %s (expected False)", dictation_session.active)
        _record(7, "dictation_session.active == False after finalize", cp7)

        # ------------------------------------------------------------------
        # CP8: clipboard restored to sentinel
        # ------------------------------------------------------------------
        try:
            current_clipboard = _clipboard_mod.read_clipboard_text()
            cp8 = current_clipboard == _SENTINEL_CLIPBOARD
            log.info(
                "clipboard after harness: %r (expected sentinel %r) → %s",
                current_clipboard,
                _SENTINEL_CLIPBOARD,
                "PASS" if cp8 else "FAIL",
            )
        except Exception:
            log.warning("could not read clipboard for CP8", exc_info=True)
            cp8 = False
        _record(8, "clipboard still holds sentinel (not overwritten by harness)", cp8)

        # ------------------------------------------------------------------
        # CP9: sprite gold badge — non-blocking, skip gracefully
        # ------------------------------------------------------------------
        _record(
            9,
            "sprite gold-badge visible (non-blocking visual evidence)",
            None,
            skip_msg=(
                "this harness does not launch the sprite subprocess; "
                "verify gold badge manually by running with the sprite active"
            ),
            blocking=False,
        )

        # ------------------------------------------------------------------
        # Log all collected events for evidence
        # ------------------------------------------------------------------
        events_log_path = OUT_DIR / "events.json"
        events_log_path.write_text(
            json.dumps(
                [
                    {"type": e.type, "data": e.data, "ts": e.ts, "id": e.id}
                    for e in collected_events
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        log.info("Events evidence written: %s (%d events)", events_log_path, len(collected_events))

    finally:
        # Restore monkeypatches
        _clipboard_mod.paste_via_clipboard = _orig_paste  # type: ignore[assignment]

        # Restore original clipboard
        try:
            if pre_clipboard is not None:
                _clipboard_mod.set_clipboard_text(pre_clipboard)
        except Exception:
            log.warning("could not restore original clipboard", exc_info=True)

        # Unsubscribe from the bus
        if bus is not None and event_q is not None:
            try:
                bus.unsubscribe(event_q)
            except Exception:
                log.warning("could not unsubscribe from event bus", exc_info=True)

        # Shut down event collector
        stop_collector.set()
        if event_collector_thread is not None and event_collector_thread.is_alive():
            event_collector_thread.join(timeout=2.0)

        # Shut down pipeline thread
        if pipeline_thread is not None and pipeline_thread.is_alive():
            try:
                daemon._utt_q.put(None)
                pipeline_thread.join(timeout=5.0)
                if pipeline_thread.is_alive():
                    log.warning("pipeline thread did not exit within 5 s")
            except Exception:
                log.warning("error shutting down pipeline thread", exc_info=True)

        log.info("cleanup complete")

    return _summarize_and_exit()


def _summarize_and_exit() -> int:
    """Write JSON summary, print checkpoint table, return exit code."""
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
    json_path = OUT_DIR / "dictation_streaming_e2e.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("JSON summary: %s", json_path)

    print("\n" + "=" * 70)
    print("STREAMING DICTATION E2E — CHECKPOINT SUMMARY")
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
    print(f"Evidence dir : {OUT_DIR}")
    print(f"Log          : {LOG_PATH}")
    print(f"JSON summary : {json_path}")
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
