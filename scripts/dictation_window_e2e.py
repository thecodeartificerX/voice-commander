"""Human-validated E2E harness for streaming-window dictation (ADR 0095).

REQUIREMENTS — run this manually only:
  - A live upgraded ``/ws/transcribe`` proxy (segments + raw_transcript)
    reachable at the URL in ``config.toml`` ([dictation] ws_url).
  - A working microphone.
  - The daemon's Python environment (``uv run`` or activated venv).

This harness exercises the full streaming-window dictation lifecycle:
the growing DictationWindow fed by the StreamingRecorder frame tap,
LocalAgreement-2 prefix agreement, segment-timestamp audio trimming,
and raw_transcript finalize.

Hard checkpoints (exit non-zero if any fail):
  CP1  dictation.start event published
  CP2  dictation.end {reason:"done"} event published
  CP3  dictation.result event published (non-empty transcript)
  CP4  paste_via_clipboard called; pasted text non-empty
  CP5  pasted text contains NO stray '...' ellipsis (core regression fix)
  CP6  pasted text contains sentence-ending words from test paragraph
  CP7  transcript events monotonically non-shrinking (OI-3: committed prefix)
  CP8  session.active == False after finalize
  CP9  clipboard restored to sentinel value

Output dir: outputs/dictation_window_e2e/
Exits 0 only when all HARD checkpoints pass.

NOTE: This is NOT a pytest test. Run manually:
    python scripts/dictation_window_e2e.py
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

OUT_DIR = ROOT / "outputs" / "dictation_window_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT_DIR / "dictation_window_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_window_e2e")

# ---------------------------------------------------------------------------
# Test paragraph — operator speaks this with ~2s pauses between sentences.
# _EXPECTED_WORDS are sentence-ending words used for fuzzy CP6 match.
# ---------------------------------------------------------------------------

_EXPECTED_WORDS = ["short", "words", "test"]
_SENTINEL_CLIPBOARD = "__dictation_window_e2e_sentinel_12345__"

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


# ---------------------------------------------------------------------------
# Stub transcriber — satisfies the daemon's transcriber slot without loading
# faster-whisper.  The window path does NOT call transcribe() at all for
# dictation audio; the stub is only needed to unblock the pipeline thread
# from waiting for the transcriber_ready event.
# ---------------------------------------------------------------------------


@dataclass
class _StubTranscription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    """Returns a fixed transcription sequence. Thread-safe (list.pop is atomic in CPython)."""

    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...

    def transcribe(self, _audio: np.ndarray) -> _StubTranscription:  # type: ignore[return]
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
# Daemon + recorder builder
# ---------------------------------------------------------------------------


def _build_daemon_and_recorder(
    cfg: Any,
    out_dir: Path,
) -> tuple[Any, Any, Any, Any, Any]:
    """Construct a stripped-down StreamingDaemon with a real StreamingRecorder.

    The real recorder is needed so the per-frame tap registered by
    DictationSession.start() receives live 16 kHz frames from the microphone
    and feeds them into the growing DictationWindow (ADR 0095).

    Returns (daemon, recorder, dictation_session, feedback, bus).
    """
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.event_bus import EventBus
    from voice_commander.feedback import CapturingFeedbackSink
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry
    from voice_commander.streaming_recorder import StreamingRecorder
    from voice_commander.vad_gate import VADGate
    from voice_commander.vad_onnx import load_silero_vad
    from voice_commander.verb_router import VerbRouter, build_default_rules

    reset_global_registry()
    reset_global_picker_registry()

    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)

    # Build DictationSession with window params from config.
    dictation_session = DictationSession(
        bus=bus,
        ws_url=cfg.dictation.ws_url,
        language=cfg.dictation.language,
        idle_timeout_s=float(cfg.dictation.idle_timeout_seconds),
        end_word=cfg.dictation.end_word,
        cancel_word=cfg.dictation.cancel_word,
        window_step_ms=cfg.dictation.window_step_ms,
        window_cap_ms=cfg.dictation.window_cap_ms,
    )

    # Build the minimal daemon (recorder=None initially; wired below).
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

    # Build the real VAD model + gate (mirrors daemon.py build_daemon).
    log.info("Loading Silero VAD ONNX model...")
    vad_model = load_silero_vad(onnx=True)
    vad_gate = VADGate(
        model=vad_model,
        threshold=cfg.vad.threshold,
        min_speech_ms=cfg.vad.min_speech_duration_ms,
        min_silence_ms=cfg.vad.min_silence_duration_ms,
        speech_pad_ms=cfg.vad.speech_pad_ms,
        pre_roll_ms=cfg.vad.pre_roll_ms,
        max_utterance_ms=cfg.vad.max_utterance_ms,
    )

    # Build the real StreamingRecorder with the configured audio device.
    recorder = StreamingRecorder(
        device=cfg.audio.device if cfg.audio.device >= 0 else None,
        channels=cfg.audio.channels,
        vad_gate=vad_gate,
        utterance_sink=daemon._on_utterance,
        device_name=cfg.audio.device_name,
    )

    # Wire recorder into the daemon and session (mirrors daemon.py lines 1562–1573).
    daemon._recorder = recorder
    dictation_session.set_recorder(recorder)

    return daemon, recorder, dictation_session, feedback, bus


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------


def run() -> int:
    """Run all checkpoints. Returns 0 if all hard checkpoints pass."""
    import voice_commander.dictation.clipboard as _clipboard_mod
    from voice_commander.config import Config

    # ------------------------------------------------------------------
    # Load config to get the production ws_url, window params, vad params.
    # ------------------------------------------------------------------
    config_path = ROOT / "config.toml"
    if config_path.exists():
        cfg = Config.load(config_path)
        log.info(
            "Loaded config: ws_url=%s end_word=%r window_step_ms=%d window_cap_ms=%d",
            cfg.dictation.ws_url,
            cfg.dictation.end_word,
            cfg.dictation.window_step_ms,
            cfg.dictation.window_cap_ms,
        )
    else:
        log.error("config.toml not found at %s — cannot proceed", config_path)
        return 1

    end_word = cfg.dictation.end_word

    # ------------------------------------------------------------------
    # Operator instructions
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("STREAMING-WINDOW DICTATION E2E HARNESS (ADR 0095)")
    print("=" * 70)
    print(f"WebSocket URL    : {cfg.dictation.ws_url}")
    print(f"End word         : {end_word!r}")
    print(f"window_step_ms   : {cfg.dictation.window_step_ms}")
    print(f"window_cap_ms    : {cfg.dictation.window_cap_ms}")
    print()
    print("REQUIREMENTS:")
    print("  1. The /ws/transcribe proxy (segments + raw_transcript) MUST be running.")
    print("  2. A working microphone must be connected.")
    print()
    print("WHAT YOU WILL DO:")
    print("  When prompted, dictate this paragraph WITH ~2s PAUSES between sentences:")
    print('    "The first sentence is short.  [pause ~2s]')
    print('     The second sentence has a few more words in it.  [pause ~2s]')
    print('     And the third one finishes the test."')
    print(f'  Then say "{end_word}" to end dictation.')
    print()
    print("  Key words the harness looks for: short, words, test")
    print("=" * 70)

    input("\nPress ENTER when the server is running and you are ready...")
    print()

    # ------------------------------------------------------------------
    # Monkey-patch paste_via_clipboard to capture what would be pasted.
    # ------------------------------------------------------------------
    pasted: list[str] = []
    _orig_paste = _clipboard_mod.paste_via_clipboard

    def _stub_paste(text: str, **kw: Any) -> None:
        log.info("paste_via_clipboard intercepted — %r", text[:200])
        pasted.append(text)

    _clipboard_mod.paste_via_clipboard = _stub_paste  # type: ignore[assignment]

    # Snapshot the real clipboard and write the sentinel so CP9 can verify
    # the harness does not corrupt the user's actual clipboard.
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
    recorder: Any = None

    try:
        # ------------------------------------------------------------------
        # Build daemon + real recorder wired to DictationSession.
        # ------------------------------------------------------------------
        daemon, recorder, dictation_session, feedback, bus = _build_daemon_and_recorder(
            cfg=cfg,
            out_dir=OUT_DIR / "store",
        )

        # Audio self-test: verify the microphone opens before we proceed.
        log.info("Running audio device self-test...")
        st = recorder.self_test()
        if not st.ok:
            log.error(
                "Audio self-test FAILED — device=%r error=%r; cannot proceed",
                cfg.audio.device_name or "(default)",
                st.error,
            )
            return 1
        log.info(
            "Audio self-test OK: device_index=%s host_api=%r rate=%d",
            st.device_index, st.host_api, st.native_rate,
        )

        # Subscribe to all events BEFORE starting the pipeline so no events
        # are lost in the gap (EventBus.subscribe is thread-safe).
        event_q = bus.subscribe()

        # Start event collector thread.
        event_collector_thread = threading.Thread(
            target=_collect_events,
            args=(event_q, collected_events, stop_collector),
            name="vc-event-collector-e2e",
            daemon=True,
        )
        event_collector_thread.start()

        # ------------------------------------------------------------------
        # Start pipeline loop thread.
        # ------------------------------------------------------------------
        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop,
            name="vc-pipeline-e2e",
            daemon=True,
        )
        pipeline_thread.start()
        log.info("pipeline thread started")

        # ------------------------------------------------------------------
        # Simulate on_dictation_toggle() with no existing session.
        # Because _session_active is False, on_dictation_toggle() calls
        # _open_voice_session() (which opens the recorder and starts the VAD
        # worker), then starts dictation immediately.
        # ------------------------------------------------------------------
        log.info("calling on_dictation_toggle() to open session and start dictation")
        daemon.on_dictation_toggle()

        # Brief pause to let the WS thread start and the start event publish.
        time.sleep(0.5)

        # ------------------------------------------------------------------
        # CP1: dictation.start event received.
        # ------------------------------------------------------------------
        start_events = [e for e in collected_events if e.type == "dictation.start"]
        cp1 = bool(start_events)
        log.info("dictation.start events received: %d", len(start_events))
        _record(1, "dictation.start event published", cp1)

        if not cp1:
            log.error("CP1 failed — dictation did not start. Aborting.")
            return _summarize_and_exit()

        # ------------------------------------------------------------------
        # Operator prompt: speak the paragraph, then say the end word.
        # ------------------------------------------------------------------
        print()
        print("-" * 70)
        print(">>> SPEAK NOW — dictate the paragraph, then say the end word <<<")
        print()
        print('  "The first sentence is short.  [pause ~2s]')
        print('   The second sentence has a few more words in it.  [pause ~2s]')
        print('   And the third one finishes the test."')
        print(f'\n  Then say: "{end_word}"')
        print("-" * 70)
        input("\nPress ENTER after you have finished speaking (including the end word)...")
        print()

        # ------------------------------------------------------------------
        # The operator has spoken the end word aloud.  The real VAD picked it
        # up and fed it through the pipeline as a completed utterance.
        # DictationSession.handle_utterance() classified it as "end", so the
        # pipeline thread submitted _end_owned_session_if_needed and
        # _finalize_dictation to the _dictation_executor.
        #
        # However — in case the VAD did not pick up the end word (ambient
        # noise, mic issue), we also inject a synthetic end-word utterance via
        # the stub transcriber + _utt_q, matching the fallback pattern from
        # dictation_streaming_e2e.py.  This is safe because handle_utterance()
        # is idempotent on a closed session (returns "buffered" silently).
        # ------------------------------------------------------------------
        if dictation_session.active:
            log.info(
                "session still active after operator press — injecting synthetic "
                "end-word utterance as fallback"
            )
            daemon._transcriber.queue.append(  # type: ignore[attr-defined]
                _StubTranscription(text=end_word, confidence=0.99)
            )
            daemon._utt_q.put((np.zeros(16000, dtype=np.float32), daemon._audio_gen))

        # ------------------------------------------------------------------
        # Wait for the dictation executor to finish (finish() joins the WS
        # asyncio thread — the real blocking step against the live server).
        # ------------------------------------------------------------------
        print()
        print("Waiting for the streaming server to return the transcript...")
        print("(This may take up to 60 seconds if the server is slow.)")
        print()

        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

        # Give the event collector a moment to drain any late events.
        time.sleep(0.5)
        stop_collector.set()
        if event_collector_thread is not None:
            event_collector_thread.join(timeout=2.0)

        # ------------------------------------------------------------------
        # CP2: dictation.end {reason:"done"} received.
        # ------------------------------------------------------------------
        end_events = [
            e for e in collected_events
            if e.type == "dictation.end" and e.data.get("reason") == "done"
        ]
        cp2 = bool(end_events)
        log.info("dictation.end{reason:done} events: %d", len(end_events))
        _record(2, 'dictation.end {reason:"done"} event published', cp2)

        # ------------------------------------------------------------------
        # CP3: dictation.result event published (non-empty transcript).
        # ------------------------------------------------------------------
        result_events = [e for e in collected_events if e.type == "dictation.result"]
        result_text = result_events[0].data.get("text", "") if result_events else ""
        cp3 = bool(result_events) and bool(result_text)
        log.info("dictation.result events: %d, text=%r", len(result_events), result_text)
        _record(3, "dictation.result event with non-empty text", cp3)

        # ------------------------------------------------------------------
        # CP4: paste_via_clipboard was called.
        # ------------------------------------------------------------------
        cp4 = bool(pasted)
        log.info("pasted list: %r", pasted)
        _record(4, "paste_via_clipboard was called (pasted list non-empty)", cp4)

        # ------------------------------------------------------------------
        # CP5: pasted text is non-empty, AND contains no '...' ellipsis.
        # The ellipsis regression was the core bug addressed by ADR 0095 —
        # LocalAgreement-1 left gaps between committed segments which the
        # old code rendered as '...'.  LA-2 prefix agreement fixes this.
        # ------------------------------------------------------------------
        pasted_text = pasted[0] if pasted else ""
        log.info("pasted text: %r", pasted_text)

        cp5 = bool(pasted_text) and "..." not in pasted_text
        _record(
            5,
            "pasted text is non-empty AND contains no '...' ellipsis (regression CP)",
            cp5,
        )

        # ------------------------------------------------------------------
        # CP6: sentence-ending words present (fuzzy substring match).
        # At least 2 of the 3 sentinel words must appear (Whisper
        # transcription is non-deterministic; one miss is tolerated).
        # ------------------------------------------------------------------
        transcript_to_check = (result_text or pasted_text).lower()
        found = [w for w in _EXPECTED_WORDS if w in transcript_to_check]
        cp6 = len(found) >= 2
        log.info(
            "sentence words found: %s (need >= 2 of %s) → %s",
            found, _EXPECTED_WORDS, "PASS" if cp6 else "FAIL",
        )
        _record(
            6,
            f"sentence words found: {found} (need >= 2 of {_EXPECTED_WORDS})",
            cp6,
        )

        # ------------------------------------------------------------------
        # CP7: transcript events are monotonically non-shrinking.
        # Each transcript event text must be a prefix (or equal) of the next
        # — confirms the OI-3 committed-prefix invariant from LocalAgreement-2.
        # A single-element or empty sequence trivially passes.
        # ------------------------------------------------------------------
        transcripts = [
            e.data.get("text", "")
            for e in collected_events
            if e.type == "transcript"
        ]
        log.info("transcript events (%d): %r", len(transcripts), transcripts)
        cp7: bool | None = None
        if not transcripts:
            _record(
                7,
                "transcript events monotonically non-shrinking (OI-3)",
                None,
                skip_msg="no transcript events collected",
                blocking=True,
            )
        else:
            cp7 = True
            for i in range(1, len(transcripts)):
                prev, curr = transcripts[i - 1], transcripts[i]
                # OI-3: each successive text must be a prefix-or-equal of the
                # next (i.e. curr starts with prev).  Catches both shrinks and
                # divergences (e.g. prev="the cat", curr="a dog").
                if prev and not curr.startswith(prev):
                    cp7 = False
                    log.error(
                        "transcript[%d] not a forward extension: %r → %r",
                        i, prev, curr,
                    )
                    break
            _record(
                7,
                "transcript events monotonically non-shrinking (OI-3)",
                cp7,
            )

        # ------------------------------------------------------------------
        # CP8: session.active == False after finalize.
        # ------------------------------------------------------------------
        cp8 = not dictation_session.active
        log.info(
            "dictation_session.active after finalize: %s (expected False)",
            dictation_session.active,
        )
        _record(8, "dictation_session.active == False after finalize", cp8)

        # ------------------------------------------------------------------
        # CP9: clipboard still holds the sentinel (harness did not corrupt it).
        # ------------------------------------------------------------------
        try:
            current_clipboard = _clipboard_mod.read_clipboard_text()
            cp9 = current_clipboard == _SENTINEL_CLIPBOARD
            log.info(
                "clipboard after harness: %r (expected sentinel %r) → %s",
                current_clipboard,
                _SENTINEL_CLIPBOARD,
                "PASS" if cp9 else "FAIL",
            )
        except Exception:
            log.warning("could not read clipboard for CP9", exc_info=True)
            cp9 = False
        _record(9, "clipboard still holds sentinel (not overwritten by harness)", cp9)

        # ------------------------------------------------------------------
        # Log all collected events for evidence.
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
        log.info(
            "Events evidence written: %s (%d events)",
            events_log_path,
            len(collected_events),
        )

        # Save pasted text + transcripts for evidence.
        evidence_path = OUT_DIR / "evidence.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "pasted": pasted,
                    "transcripts": transcripts,
                    "result_text": result_text,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log.info("Evidence written: %s", evidence_path)

    finally:
        # Restore monkeypatches.
        _clipboard_mod.paste_via_clipboard = _orig_paste  # type: ignore[assignment]

        # Restore original clipboard.
        try:
            if pre_clipboard is not None:
                _clipboard_mod.set_clipboard_text(pre_clipboard)
        except Exception:
            log.warning("could not restore original clipboard", exc_info=True)

        # Unsubscribe from the bus.
        if bus is not None and event_q is not None:
            try:
                bus.unsubscribe(event_q)
            except Exception:
                log.warning("could not unsubscribe from event bus", exc_info=True)

        # Shut down event collector.
        stop_collector.set()
        if event_collector_thread is not None and event_collector_thread.is_alive():
            event_collector_thread.join(timeout=2.0)

        # Close the recorder session (idempotent if already closed by daemon).
        if recorder is not None:
            try:
                recorder.close_session()
            except Exception:
                log.warning("could not close recorder session", exc_info=True)

        # Shut down pipeline thread.
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
    json_path = OUT_DIR / "dictation_window_e2e.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("JSON summary: %s", json_path)

    print("\n" + "=" * 70)
    print("STREAMING-WINDOW DICTATION E2E — CHECKPOINT SUMMARY")
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
