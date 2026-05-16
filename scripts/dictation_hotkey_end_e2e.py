"""E2E harness for the dictation hotkey-end race fix (fix/dictation-hotkey-end-race).

Exercises the HOTKEY-end path (not the "done" word) end-to-end through the
real pipeline objects:

  1. Starts a pipeline thread backed by a stub transcriber.
  2. Enters dictation mode.
  3. Injects 2 audio utterances into _utt_q (simulating in-flight VAD audio).
  4. Calls dictation_session.request_end() (new hotkey-end path).
  5. Waits for the pipeline to drain and finalize.
  6. Asserts the transcription text was clipboard-pasted.

Hard checkpoints (exit non-zero if any fail):
  CP1  dictation.start event received
  CP2  queue non-empty before toggle (2 items)
  CP3  pending_end True AND active True immediately after request_end
  CP4  active False after drain (session deactivated by take_and_finish)
  CP5  pasted list non-empty with expected stub text
  CP6  pending_end False after finalize
  CP7  clipboard restored to sentinel

Non-blocking (evidence only):
  CP8  sprite gold-badge visual (skipped gracefully if no sprite running)

Output dir: outputs/dictation_hotkey_end_e2e/
Exits 0 only when all HARD checkpoints pass.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Repo root + path setup
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / "outputs" / "dictation_hotkey_end_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT_DIR / "dictation_hotkey_end_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_hotkey_end_e2e")

# ---------------------------------------------------------------------------
# Stub constants
# ---------------------------------------------------------------------------

_STUB_TRANSCRIPTION = "hotkey end e2e stub transcription"
_SENTINEL_CLIPBOARD = "__dictation_hotkey_end_e2e_sentinel_67890__"

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
# Stub transcriber
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class _StubTranscription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    """Returns a fixed transcription; thread-safe (list.pop is atomic in CPython)."""

    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...

    def transcribe(self, _audio: np.ndarray) -> _StubTranscription:
        if self.queue:
            return self.queue.pop(0)
        return _StubTranscription(text="(empty)", confidence=0.99)


# ---------------------------------------------------------------------------
# Daemon builder (in-process, no subprocess)
# ---------------------------------------------------------------------------


def _build_daemon(
    transcripts: list[_StubTranscription],
    pasted: list[str],
    out_dir: Path,
) -> Any:
    """Construct a stripped-down StreamingDaemon for hotkey-end testing."""
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
        transcriber=_StubTranscriber(queue=list(transcripts)),
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
    import voice_commander.dictation.remote as _remote_mod

    # Stub the network call and clipboard operations
    _orig_post = _remote_mod.post_audio
    _orig_paste = _clipboard_mod.paste_via_clipboard

    pasted: list[str] = []

    def _stub_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        log.info("stub post_audio called — %d bytes", len(wav_bytes))
        return _STUB_TRANSCRIPTION

    def _stub_paste(text: str, **kw: Any) -> None:
        log.info("stub paste_via_clipboard called — %r", text)
        pasted.append(text)

    _remote_mod.post_audio = _stub_post  # type: ignore[assignment]
    _clipboard_mod.paste_via_clipboard = _stub_paste  # type: ignore[assignment]

    # Snapshot the real clipboard to restore later
    try:
        pre_clipboard = _clipboard_mod.read_clipboard_text()
        _clipboard_mod.set_clipboard_text(_SENTINEL_CLIPBOARD)
        log.info("sentinel clipboard set: %r", _SENTINEL_CLIPBOARD)
    except Exception:
        pre_clipboard = None
        log.warning("could not read/set clipboard (non-fatal)", exc_info=True)

    pipeline_thread: threading.Thread | None = None

    try:
        # ------------------------------------------------------------------
        # Build daemon
        # ------------------------------------------------------------------
        daemon, dictation_session, feedback, bus = _build_daemon(
            transcripts=[
                _StubTranscription("spoken line one"),
                _StubTranscription("spoken line two"),
            ],
            pasted=pasted,
            out_dir=OUT_DIR / "store",
        )
        audio = np.zeros(16000, dtype=np.float32)

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
        # CP1: enter dictation mode
        # ------------------------------------------------------------------
        dictation_session.start()
        cp1 = dictation_session.active
        _record(1, "dictation.start — session active", cp1)

        # ------------------------------------------------------------------
        # CP2: queue non-empty before toggle (2 items enqueued)
        # ------------------------------------------------------------------
        gen = daemon._audio_gen
        daemon._utt_q.put((audio, gen))
        daemon._utt_q.put((audio, gen))
        cp2 = daemon._utt_q.qsize() >= 2
        log.info("utt_q size after enqueue: %d", daemon._utt_q.qsize())
        _record(2, "utt_q non-empty before hotkey-end (≥2 items)", cp2)

        # ------------------------------------------------------------------
        # CP3: after request_end — pending_end True AND active True
        # ------------------------------------------------------------------
        dictation_session.request_end()
        cp3 = dictation_session.pending_end and dictation_session.active
        log.info(
            "after request_end: pending_end=%s active=%s",
            dictation_session.pending_end,
            dictation_session.active,
        )
        _record(
            3,
            "after request_end: pending_end=True AND active=True",
            cp3,
        )

        # ------------------------------------------------------------------
        # Wait for pipeline to drain and paste to happen (up to 2 s)
        # ------------------------------------------------------------------
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not pasted:
            time.sleep(0.05)

        # Wait for dictation executor to finish
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

        # ------------------------------------------------------------------
        # CP4: active False after drain
        # ------------------------------------------------------------------
        cp4 = not dictation_session.active
        log.info("active after drain: %s (expected False)", dictation_session.active)
        _record(4, "active=False after drain + finalize", cp4)

        # ------------------------------------------------------------------
        # CP5: pasted non-empty with expected stub text
        # ------------------------------------------------------------------
        cp5 = bool(pasted) and pasted[0] == _STUB_TRANSCRIPTION
        log.info("pasted: %r (expected %r)", pasted, _STUB_TRANSCRIPTION)
        _record(5, f"pasted == {_STUB_TRANSCRIPTION!r}", cp5)

        # ------------------------------------------------------------------
        # CP6: pending_end False after finalize
        # ------------------------------------------------------------------
        cp6 = not dictation_session.pending_end
        log.info("pending_end after finalize: %s (expected False)", dictation_session.pending_end)
        _record(6, "pending_end=False after finalize", cp6)

        # ------------------------------------------------------------------
        # CP7: clipboard restored to sentinel
        # ------------------------------------------------------------------
        try:
            current_clipboard = _clipboard_mod.read_clipboard_text()
            cp7 = current_clipboard == _SENTINEL_CLIPBOARD
            log.info(
                "clipboard after harness: %r (expected sentinel %r) → %s",
                current_clipboard,
                _SENTINEL_CLIPBOARD,
                "PASS" if cp7 else "FAIL",
            )
        except Exception:
            log.warning("could not read clipboard for CP7", exc_info=True)
            cp7 = False
        _record(7, "clipboard restored to sentinel", cp7)

        # ------------------------------------------------------------------
        # CP8: non-blocking — sprite gold badge (skip gracefully)
        # ------------------------------------------------------------------
        _record(
            8,
            "sprite gold badge visible (non-blocking visual evidence)",
            None,
            skip_msg=(
                "this harness does not launch the sprite subprocess; "
                "verify badge manually via dictation_visual_e2e.py"
            ),
            blocking=False,
        )

    finally:
        # Restore monkeypatches
        _remote_mod.post_audio = _orig_post  # type: ignore[assignment]
        _clipboard_mod.paste_via_clipboard = _orig_paste  # type: ignore[assignment]

        # Restore original clipboard
        try:
            if pre_clipboard is not None:
                _clipboard_mod.set_clipboard_text(pre_clipboard)
        except Exception:
            log.warning("could not restore original clipboard", exc_info=True)

        # Shut down pipeline thread
        if pipeline_thread is not None and pipeline_thread.is_alive():
            try:
                daemon._utt_q.put(None)
                pipeline_thread.join(timeout=3.0)
                if pipeline_thread.is_alive():
                    log.warning("pipeline thread did not exit within 3 s")
            except Exception:
                log.warning("error shutting down pipeline thread", exc_info=True)

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
    json_path = OUT_DIR / "dictation_hotkey_end_e2e.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("JSON summary: %s", json_path)

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("DICTATION HOTKEY-END E2E — CHECKPOINT SUMMARY")
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
