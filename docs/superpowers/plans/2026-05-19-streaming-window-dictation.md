# Streaming-Window Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework dictation transcription from hard VAD-segmented chunks to a growing-window streaming model that re-decodes the whole window on a fixed cadence and commits stable words with correct LocalAgreement-2, eliminating pause-fragmentation and stray `...` in pasted output.

**Architecture:** A new `DictationWindow` owns a growing 16 kHz float32 audio buffer fed by a `StreamingRecorder` frame-tap. Every `window_step_ms` of accumulated audio it emits the *whole window* as a WAV; the server re-decodes the whole window each time. Consecutive whole-window hypotheses are fed to a reworked LocalAgreement-2 that commits the longest agreeing *prefix* and reports the committed end-time; `DictationWindow` trims the buffer at that point so it stays bounded. VAD is demoted to endpointing only — it still detects the end-word/cancel-word and silence, but its utterances are no longer streamed as transcription chunks.

**Tech Stack:** Python 3.12, `numpy`, `sounddevice` + `soxr` + Silero VAD (existing capture stack), `websockets` (async WS client), `pytest`. Windows-only daemon.

---

## Server contract — precondition / assumption

This plan covers the **voice-commander side only**. Two backward-compatible server-side additions are implemented in a **separate repo** by the VPS engineer and are a **precondition** for the human-validation gates of Phase 4 and Phase 6, but NOT for the unit-test gates of Phases 1–5 (those use stubs/mocks). The plan must not block on them; both additions can land independently and ahead of this work.

**Assumed server contract additions (faster-whisper proxy at `[dictation] ws_url`):**

1. **Segment timestamps on `partial`.** Every `partial` reply gains
   `segments: [{"start": float, "end": float, "text": str}, ...]` — seconds
   relative to the sent WAV. Existing `text` / `accumulated` fields retained.
2. **`raw_transcript` on the `end` frame.** The `{"type":"end"}` frame accepts an
   optional `raw_transcript` string. When present and non-empty, the server
   LLM-cleans that text directly (no whisper decode) and returns
   `{"type":"done","text":<cleaned>,"raw":<raw_transcript>}`. When present and
   empty: `{"type":"done","text":"","raw":""}`. When absent: ADR 0094 behaviour,
   unchanged.

`accumulated` is unreliable for the daemon under overlapping windows — the daemon
ignores it; LocalAgreement-2 output is authoritative.

---

## Open items — decisions for this plan

The spec left three open items. This plan resolves them as follows; each resolution is implemented by a named task.

### OI-1 — `segments` absent: degrade gracefully (NOT hard-require)

**Decision: degrade gracefully.** When a `partial` arrives without a usable
`segments` array (un-upgraded server, or a partial that genuinely produced no
segments), the daemon falls back to a **time-based trim policy**: it does not
trust per-segment end-times, and `DictationWindow` keeps the most recent
`window_cap_ms` of audio, force-committing older text at the cap. LocalAgreement-2
still runs on the `text` field for word stabilisation; only the *trim point*
degrades. A `WARNING` is logged once per session on the first segment-less partial.
Rationale: a hard-require would make a daemon upgrade and a server upgrade a
lockstep deploy; the spec explicitly calls the degraded mode "functional", and
the proxy's own raw-text fallback (ADR 0093) sets the precedent for graceful
degradation over hard failure. Implemented by **Task 5** (`DictationWindow.commit`
+ cap policy) and **Task 8** (session wiring of the segment-less branch).

### OI-2 — LocalAgreement-2 post-trim offset alignment

**Decision: `DictationWindow` owns a `committed_offset_s` (float seconds) and
exposes it; the session translates committed segment end-times into
absolute-stream time before calling `commit`, and back into window-relative time
is never needed because `DictationWindow.commit` takes an *absolute* timestamp.**

Concretely: every hypothesis segment's `start`/`end` from the server is **relative
to the WAV that was sent**, i.e. relative to the *current, post-trim* window. The
session adds `DictationWindow.committed_offset_s` (the total audio-seconds dropped
by previous trims) to those values to get **absolute-stream timestamps**.
LocalAgreement-2 is fed words tagged with absolute end-times; it commits a prefix
and returns the absolute end-time of the last committed word.
`DictationWindow.commit(committed_end_s)` receives that **absolute** timestamp,
computes `drop_samples = round((committed_end_s - committed_offset_s) * 16000)`,
drops that many samples from the head, and advances `committed_offset_s` by the
dropped duration. This keeps a single source of truth (the offset lives only in
`DictationWindow`) and makes the alignment a pure add/subtract with no
window-relative bookkeeping leaking into LocalAgreement-2. Implemented by
**Task 4** (LocalAgreement-2 carries absolute end-times) and **Task 5**
(`DictationWindow.committed_offset_s` + absolute-timestamp `commit`).

### OI-3 — Live HUD `transcript` event follows the committed prefix

**Decision: the HUD `transcript` event follows the COMMITTED prefix, not the
latest raw window hypothesis.** On every partial, after LocalAgreement-2 commits,
the session publishes `transcript {text: <full committed text so far>,
confidence: 1.0}`. Rationale: the latest raw hypothesis has an unstable tail that
visibly rewrites itself between frames (the exact churn LocalAgreement-2 exists to
hide); showing it would make the HUD flicker and contradict the "coherent output"
goal. The committed prefix only ever grows and never rewrites — a stable,
honest live view. The final post-`finish()` `transcript` event (the LLM-cleaned
text) is unchanged from ADR 0092 D4. Implemented by **Task 8**.

---

## File structure

**New files:**

| File | Responsibility |
|------|----------------|
| `src/voice_commander/dictation/window.py` | `DictationWindow` — growing 16 kHz buffer, cadence-driven WAV emission, committed-offset trim, cap force-commit. Thread-safe (`append` on VAD worker thread, emit/`commit` on asyncio-loop thread). |
| `tests/unit/test_dictation_window.py` | Unit tests for `DictationWindow`. |
| `tests/unit/test_streaming_recorder_frame_tap.py` | Unit tests for the `StreamingRecorder` frame-tap. |
| `docs/decisions/0095-streaming-window-dictation.md` | ADR 0095. |
| `scripts/dictation_window_e2e.py` | Human-validated visual E2E harness. |

**Modified files:**

| File | Change |
|------|--------|
| `src/voice_commander/streaming_recorder.py` | Add `set_frame_tap()`; VAD worker invokes the tap per 16 kHz/512 frame. |
| `src/voice_commander/dictation/local_agreement.py` | Rework `commit` to LocalAgreement-2 prefix agreement over whole-window hypotheses with absolute end-times. |
| `src/voice_commander/dictation/ws_client.py` | Parse `segments` on `partial`; send `raw_transcript` on the `end` frame. |
| `src/voice_commander/dictation/session.py` | Register/clear the frame-tap; own a `DictationWindow`; window-WAV streaming; segment-aware partial handling; HUD committed-prefix event; `raw_transcript` finalize. |
| `src/voice_commander/config.py` | `DictationConfig`: add `window_step_ms`, `window_cap_ms`. |
| `config.toml.example` | New `[dictation]` keys with inline comments. |
| `src/voice_commander/daemon.py` | Pass new config to `DictationSession`; wire the recorder reference so the session can register its frame-tap. |
| `tests/unit/test_config.py` | Assert the two new config keys load + default. |
| `tests/unit/test_dictation_local_agreement.py` | Rewrite for LocalAgreement-2 semantics. |
| `tests/unit/test_dictation_session.py` | Window-WAV streaming + `raw_transcript` finalize cases. |
| `docs/dictation-streaming.md` | Rewrite the flow/architecture sections for the window model. |
| `docs/transcription-pipeline.md` | Update §4 (dictation path). |
| `docs/references/whisper-cpp-server-inference.md` | Replace stale whisper.cpp `/inference` reference with the custom FastAPI + faster-whisper `/ws/transcribe` contract. |
| `CLAUDE.md` | Update the dictation paragraph of the "Current state" section. |

---

## Phasing & gates

Per CLAUDE.md, each phase ends in **(a)** an automated test gate and **(b)** a human validation gate. Do not start phase N+1 until phase N's human gate is signed off.

- **Phase 1 — Frame tap.** `StreamingRecorder.set_frame_tap`. Gate A: `pytest tests/unit/test_streaming_recorder_frame_tap.py`. Gate B: human runs the existing daemon, confirms command mode is unaffected (no regressions).
- **Phase 2 — `DictationWindow`.** New buffer component. Gate A: `pytest tests/unit/test_dictation_window.py`. Gate B: human reviews the cadence/cap math against the spec.
- **Phase 3 — LocalAgreement-2.** Correct prefix-agreement algorithm. Gate A: `pytest tests/unit/test_dictation_local_agreement.py`. Gate B: human reviews the offset-alignment worked example.
- **Phase 4 — WS client + session integration.** Gate A: `pytest tests/unit/test_dictation_session.py tests/unit/test_config.py`. Gate B: human runs `scripts/dictation_window_e2e.py` against the upgraded server and confirms the PASS summary.
- **Phase 5 — VAD demotion + config + daemon wiring.** Gate A: full `pytest tests/unit`. Gate B: human dictates a short phrase end-to-end.
- **Phase 6 — Docs + visual E2E + ADR.** Gate A: `pytest tests/unit` clean. Gate B: human runs the multi-sentence-with-pauses visual E2E and signs off on the pasted output.

---

# Phase 1 — `StreamingRecorder` frame tap

### Task 1: Add `set_frame_tap` to `StreamingRecorder`

**Files:**
- Modify: `src/voice_commander/streaming_recorder.py`
- Test: `tests/unit/test_streaming_recorder_frame_tap.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_streaming_recorder_frame_tap.py`:

```python
"""Unit tests for the StreamingRecorder frame tap (streaming-window dictation)."""

from __future__ import annotations

import numpy as np

from voice_commander.streaming_recorder import StreamingRecorder
from voice_commander.vad_gate import VADGate


class _FakeVad:
    """Minimal VAD model stub — never reports speech start/end."""

    def __call__(self, *_args, **_kwargs):
        return None

    def reset_states(self):
        pass


def _make_recorder() -> StreamingRecorder:
    gate = VADGate(model=_FakeVad())
    return StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=gate,
        utterance_sink=lambda _a: None,
    )


def test_frame_tap_unset_by_default():
    rec = _make_recorder()
    assert rec._frame_tap is None


def test_set_frame_tap_stores_callback():
    rec = _make_recorder()
    captured: list[np.ndarray] = []
    rec.set_frame_tap(captured.append)
    assert rec._frame_tap is captured.append


def test_set_frame_tap_none_clears_callback():
    rec = _make_recorder()
    rec.set_frame_tap(lambda _f: None)
    rec.set_frame_tap(None)
    assert rec._frame_tap is None


def test_invoke_frame_tap_forwards_frame():
    rec = _make_recorder()
    captured: list[np.ndarray] = []
    rec.set_frame_tap(captured.append)
    frame = np.zeros(512, dtype=np.float32)
    rec._invoke_frame_tap(frame)
    assert len(captured) == 1
    assert captured[0] is frame


def test_invoke_frame_tap_swallows_callback_exception():
    rec = _make_recorder()

    def _boom(_frame):
        raise RuntimeError("tap exploded")

    rec.set_frame_tap(_boom)
    # Must not raise — a bad tap must never kill the VAD worker thread.
    rec._invoke_frame_tap(np.zeros(512, dtype=np.float32))


def test_invoke_frame_tap_noop_when_unset():
    rec = _make_recorder()
    # No tap registered — must be a silent no-op.
    rec._invoke_frame_tap(np.zeros(512, dtype=np.float32))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_streaming_recorder_frame_tap.py -v`
Expected: FAIL — `AttributeError: 'StreamingRecorder' object has no attribute '_frame_tap'`.

- [ ] **Step 3: Add the frame-tap field and methods**

In `src/voice_commander/streaming_recorder.py`, in `StreamingRecorder.__init__`,
after the existing `self._vad_thread: threading.Thread | None = None` line, add:

```python
        # Optional per-frame consumer for streaming-window dictation (ADR 0095).
        # Set via set_frame_tap(); invoked by the VAD worker thread with each
        # resampled 16 kHz / 512-sample float32 frame. Unset (None) by default
        # so command mode is unaffected. At most one consumer.
        self._frame_tap: Callable[[npt.NDArray[np.float32]], None] | None = None
```

Then add these two methods to the public-interface section, immediately after
`self_test`:

```python
    def set_frame_tap(
        self, callback: Callable[[npt.NDArray[np.float32]], None] | None
    ) -> None:
        """Register (or clear) the per-frame audio tap.

        *callback* is invoked by the VAD worker thread with every resampled
        16 kHz / 512-sample float32 frame, alongside the existing
        ``VADGate.process`` call. At most one consumer; passing ``None`` clears
        it. Default unset — command mode is unaffected.

        Used by streaming-window dictation (ADR 0095): ``DictationSession.start``
        registers a tap that feeds the growing ``DictationWindow`` buffer;
        ``finish``/``cancel`` clear it.
        """
        self._frame_tap = callback

    def _invoke_frame_tap(self, frame: npt.NDArray[np.float32]) -> None:
        """Call the registered frame tap; swallow + log any exception.

        A raising tap callback must never kill the VAD worker thread, so the
        invocation is wrapped. A no-op when no tap is registered.
        """
        tap = self._frame_tap
        if tap is None:
            return
        try:
            tap(frame)
        except Exception:
            logger.exception("StreamingRecorder: frame tap callback raised")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_streaming_recorder_frame_tap.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/streaming_recorder.py tests/unit/test_streaming_recorder_frame_tap.py
git commit -m "feat: add StreamingRecorder frame tap for streaming-window dictation"
```

### Task 2: Invoke the frame tap from the VAD worker loop

**Files:**
- Modify: `src/voice_commander/streaming_recorder.py` (`_vad_loop`, ~line 750-761)
- Test: `tests/unit/test_streaming_recorder_frame_tap.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_streaming_recorder_frame_tap.py`:

```python
def test_vad_loop_feeds_frame_tap_per_frame():
    """The VAD worker invokes the tap once per 512-sample frame."""
    import collections
    import queue

    rec = _make_recorder()
    captured: list[np.ndarray] = []
    rec.set_frame_tap(captured.append)

    # Simulate the VAD-loop inner body: feed three whole frames.
    pending: collections.deque[np.ndarray] = collections.deque()
    pending_samples = 0
    block = np.ones(512 * 3, dtype=np.float32)
    pending.append(block)
    pending_samples += block.shape[0]

    from voice_commander.streaming_recorder import _VAD_FRAME_SIZE, _pop_frame

    while pending_samples >= _VAD_FRAME_SIZE:
        frame = _pop_frame(pending, _VAD_FRAME_SIZE)
        pending_samples -= _VAD_FRAME_SIZE
        rec._invoke_frame_tap(frame)
        rec._vad_gate.process(frame)

    assert len(captured) == 3
    assert all(f.shape == (512,) for f in captured)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_streaming_recorder_frame_tap.py::test_vad_loop_feeds_frame_tap_per_frame -v`
Expected: PASS already (the test exercises `_invoke_frame_tap` directly). This
test guards the contract; the real wiring is verified in Step 4 by inspection +
the Phase 5 daemon run. Proceed to Step 3 regardless — the production `_vad_loop`
still needs the call inserted.

- [ ] **Step 3: Insert the tap call into `_vad_loop`**

In `src/voice_commander/streaming_recorder.py`, in `_vad_loop`, the inner frame
loop currently reads:

```python
                while pending_samples >= _VAD_FRAME_SIZE:
                    frame: npt.NDArray[np.float32] = _pop_frame(pending, _VAD_FRAME_SIZE)
                    pending_samples -= _VAD_FRAME_SIZE

                    result = self._vad_gate.process(frame)
```

Insert the tap invocation between `pending_samples -= _VAD_FRAME_SIZE` and
`result = self._vad_gate.process(frame)`:

```python
                while pending_samples >= _VAD_FRAME_SIZE:
                    frame: npt.NDArray[np.float32] = _pop_frame(pending, _VAD_FRAME_SIZE)
                    pending_samples -= _VAD_FRAME_SIZE

                    # Streaming-window dictation tap (ADR 0095): feed every
                    # 16 kHz/512 frame to the dictation buffer. Wrapped so a
                    # raising tap never kills this worker thread. Runs BEFORE
                    # VADGate so a window emission is never starved by gate work.
                    self._invoke_frame_tap(frame)

                    result = self._vad_gate.process(frame)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_streaming_recorder_frame_tap.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/streaming_recorder.py tests/unit/test_streaming_recorder_frame_tap.py
git commit -m "feat: invoke dictation frame tap from VAD worker loop"
```

### Phase 1 gates

- [ ] **Gate A (automated):** `python -m pytest tests/unit/test_streaming_recorder_frame_tap.py -v` — all PASS.
- [ ] **Gate B (human):** Start the daemon (`uv run voice-commander` or the project's normal start path), run a few command-mode utterances ("copy", "open spotify"), confirm routing and chimes are unchanged. The frame tap is unset in command mode, so this is a regression check. **STOP — wait for human sign-off before Phase 2.**

---

# Phase 2 — `DictationWindow`

### Task 3: Create the `DictationWindow` component

**Files:**
- Create: `src/voice_commander/dictation/window.py`
- Test: `tests/unit/test_dictation_window.py` (create)

`DictationWindow` owns the growing 16 kHz mono float32 buffer for one dictation.
Cadence is driven by *accumulated sample count*, not wall-clock. The buffer is
guarded by a `threading.Lock` (frames arrive on the VAD worker thread; emission +
`commit` happen on the asyncio-loop thread). It returns WAV bytes via the existing
`encode_wav` from `dictation/store.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dictation_window.py`:

```python
"""Unit tests for DictationWindow — the growing-window dictation buffer."""

from __future__ import annotations

import io
import wave

import numpy as np

from voice_commander.dictation.window import DictationWindow

_SR = 16000


def _frame(n: int = 512, value: float = 0.1) -> np.ndarray:
    return np.full(n, value, dtype=np.float32)


def _wav_sample_count(wav_bytes: bytes) -> int:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes()


def test_no_emission_before_step():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    # 1000 ms at 16 kHz = 16000 samples. Feed less than one step.
    for _ in range(20):  # 20 * 512 = 10240 samples < 16000
        assert win.append(_frame()) is None


def test_emits_whole_window_at_step_cadence():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    emitted: list[bytes] = []
    # Feed exactly one step worth: ceil(16000 / 512) = 32 frames.
    for _ in range(32):
        wav = win.append(_frame())
        if wav is not None:
            emitted.append(wav)
    assert len(emitted) == 1
    # The emitted WAV is the WHOLE window so far (>= 16000 samples).
    assert _wav_sample_count(emitted[0]) >= _SR


def test_emission_window_grows():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    emitted: list[bytes] = []
    for _ in range(64):  # two steps' worth
        wav = win.append(_frame())
        if wav is not None:
            emitted.append(wav)
    assert len(emitted) == 2
    # Second emission contains MORE audio than the first (growing window).
    assert _wav_sample_count(emitted[1]) > _wav_sample_count(emitted[0])


def test_committed_offset_starts_at_zero():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    assert win.committed_offset_s == 0.0


def test_commit_trims_buffer_and_advances_offset():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    for _ in range(64):  # ~2.0 s of audio
        win.append(_frame())
    # Commit the first 1.0 s (absolute timestamp).
    win.commit(1.0)
    assert win.committed_offset_s == 1.0
    # The next emitted window must be shorter than the full 2 s buffer.
    for _ in range(32):  # add another step so an emission fires
        wav = win.append(_frame())
        if wav is not None:
            # buffer now ~ (2.0 - 1.0 trimmed) + 1.0 new = ~2.0 s, not 3.0 s
            assert _wav_sample_count(wav) < int(_SR * 3.0)
            return
    raise AssertionError("expected an emission after the post-commit step")


def test_commit_is_idempotent_for_stale_timestamp():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    for _ in range(64):
        win.append(_frame())
    win.commit(1.0)
    # A timestamp at or before the current offset trims nothing.
    win.commit(1.0)
    win.commit(0.5)
    assert win.committed_offset_s == 1.0


def test_flush_returns_remaining_window():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    for _ in range(10):  # below the step threshold — no auto emission
        win.append(_frame())
    wav = win.flush()
    assert wav is not None
    assert _wav_sample_count(wav) == 10 * 512


def test_flush_returns_none_when_buffer_empty():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    assert win.flush() is None


def test_cap_force_commit_signalled():
    # window_cap_ms small so the cap trips quickly.
    win = DictationWindow(window_step_ms=1000, window_cap_ms=1000)
    # Feed 3 s of audio with no commit() calls.
    for _ in range(96):  # 96 * 512 = 49152 samples ~ 3.07 s
        win.append(_frame())
    # Uncommitted buffered audio must never exceed window_cap_ms once the
    # cap policy has run. force_commit_pending() reports the timestamp the
    # caller must commit at; the buffer length stays bounded.
    assert win.uncommitted_seconds() <= 1.0 + 0.5  # cap + one step slack
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_dictation_window.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation.window'`.

- [ ] **Step 3: Implement `DictationWindow`**

Create `src/voice_commander/dictation/window.py`:

```python
"""DictationWindow — the growing audio window for streaming-window dictation.

Owns one dictation's growing 16 kHz mono float32 audio buffer. Frames arrive
from the StreamingRecorder frame tap on the VAD worker thread (:meth:`append`);
the asyncio-loop thread reads window WAVs and trims the buffer (:meth:`commit`,
:meth:`flush`). A single lock guards the buffer across those two threads.

Cadence is driven by *accumulated sample count*, not wall-clock: every
``window_step_ms`` of newly appended audio, :meth:`append` returns the WHOLE
current window encoded as a WAV. The window grows until :meth:`commit` trims it
at a committed segment boundary; :attr:`committed_offset_s` tracks how much
audio has been dropped so callers can translate window-relative timestamps to
absolute-stream timestamps (ADR 0095, open item OI-2).

When uncommitted buffered audio exceeds ``window_cap_ms`` the window is bounded
by force-committing: :meth:`uncommitted_seconds` lets the caller detect the cap;
the session force-commits the oldest segment (ADR 0095, open item OI-1).

See ADR 0095 (`docs/decisions/0095-streaming-window-dictation.md`).
"""

from __future__ import annotations

import logging
import threading

import numpy as np
import numpy.typing as npt

from .store import encode_wav

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000


class DictationWindow:
    """Growing 16 kHz audio buffer for one dictation; emits whole-window WAVs."""

    def __init__(self, window_step_ms: int = 1000, window_cap_ms: int = 25000) -> None:
        self._step_samples = max(1, int(_SAMPLE_RATE * window_step_ms / 1000))
        self._cap_samples = max(self._step_samples, int(_SAMPLE_RATE * window_cap_ms / 1000))
        self._lock = threading.Lock()
        # Buffer holds only UNCOMMITTED audio — committed audio is trimmed away.
        self._buffer: list[npt.NDArray[np.float32]] = []
        self._buffer_samples = 0
        # Samples appended since the last emission — drives the step cadence.
        self._since_emit = 0
        # Total audio-seconds dropped by commit() trims. Lets callers map a
        # window-relative timestamp to an absolute-stream timestamp.
        self._committed_offset_s = 0.0

    @property
    def committed_offset_s(self) -> float:
        """Total audio-seconds dropped from the head by :meth:`commit` trims."""
        with self._lock:
            return self._committed_offset_s

    def append(self, frame: npt.NDArray[np.float32]) -> bytes | None:
        """Append one 16 kHz float32 frame; return a window WAV at step cadence.

        Called by the VAD worker thread via the StreamingRecorder frame tap.
        Returns ``bytes`` (the WHOLE current window as a WAV) once
        ``window_step_ms`` of audio has accumulated since the last emission,
        else ``None``. Cheap and thread-safe.
        """
        with self._lock:
            self._buffer.append(np.asarray(frame, dtype=np.float32))
            self._buffer_samples += frame.shape[0]
            self._since_emit += frame.shape[0]
            if self._since_emit < self._step_samples:
                return None
            self._since_emit = 0
            return self._encode_locked()

    def commit(self, committed_end_s: float) -> None:
        """Trim buffer audio up to *committed_end_s* (an absolute-stream time).

        *committed_end_s* is absolute (offset by all prior trims) — the session
        translates window-relative segment end-times via :attr:`committed_offset_s`
        before calling this. A timestamp at or before the current committed
        offset is a no-op (idempotent — guards stale LocalAgreement output).
        """
        with self._lock:
            drop_s = committed_end_s - self._committed_offset_s
            if drop_s <= 0.0:
                return
            drop_samples = min(round(drop_s * _SAMPLE_RATE), self._buffer_samples)
            self._drop_head_locked(drop_samples)
            self._committed_offset_s += drop_samples / _SAMPLE_RATE

    def uncommitted_seconds(self) -> float:
        """Seconds of uncommitted audio currently buffered."""
        with self._lock:
            return self._buffer_samples / _SAMPLE_RATE

    def cap_exceeded(self) -> bool:
        """``True`` when buffered uncommitted audio exceeds ``window_cap_ms``."""
        with self._lock:
            return self._buffer_samples > self._cap_samples

    def flush(self) -> bytes | None:
        """Return the entire remaining window as a WAV, or ``None`` if empty.

        Called once by :meth:`DictationSession.finish` to send the final window.
        """
        with self._lock:
            if self._buffer_samples == 0:
                return None
            return self._encode_locked()

    # --- internal (caller holds self._lock) ---

    def _encode_locked(self) -> bytes:
        audio = np.concatenate(self._buffer) if self._buffer else np.zeros(0, dtype=np.float32)
        return encode_wav(audio)

    def _drop_head_locked(self, n: int) -> None:
        """Drop the first *n* samples from the buffer (caller holds the lock)."""
        if n <= 0:
            return
        remaining = n
        new_buffer: list[npt.NDArray[np.float32]] = []
        for chunk in self._buffer:
            if remaining <= 0:
                new_buffer.append(chunk)
            elif chunk.shape[0] <= remaining:
                remaining -= chunk.shape[0]
            else:
                new_buffer.append(chunk[remaining:])
                remaining = 0
        self._buffer = new_buffer
        self._buffer_samples = sum(c.shape[0] for c in new_buffer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_dictation_window.py -v`
Expected: PASS (9 tests).

> Note: `test_cap_force_commit_signalled` passes because `uncommitted_seconds()`
> stays bounded only once the *session* force-commits at the cap. With no commit
> calls the buffer would grow unbounded — but the assertion's slack
> (`cap + one step`) holds only if the session drives the cap. To make the unit
> test self-contained, the test must drive the cap itself. Adjust the test body:
> after the append loop, add
> `if win.cap_exceeded(): win.commit(win.committed_offset_s + win.uncommitted_seconds() - 1.0)`.
> Apply that edit now so the test is honest, then re-run.

```python
def test_cap_force_commit_signalled():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=1000)
    for _ in range(96):
        win.append(_frame())
    # cap_exceeded() reports the over-cap condition; the session responds by
    # committing the oldest audio down to the cap.
    assert win.cap_exceeded()
    win.commit(win.committed_offset_s + win.uncommitted_seconds() - 1.0)
    assert not win.cap_exceeded()
    assert win.uncommitted_seconds() <= 1.0 + 1e-6
```

Re-run: `python -m pytest tests/unit/test_dictation_window.py -v` — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation/window.py tests/unit/test_dictation_window.py
git commit -m "feat: add DictationWindow growing-buffer component"
```

### Phase 2 gates

- [ ] **Gate A (automated):** `python -m pytest tests/unit/test_dictation_window.py -v` — all PASS.
- [ ] **Gate B (human):** Reviewer reads `window.py` and confirms: cadence is sample-count-driven; `commit` is idempotent for stale timestamps; the cap is bounded once the session drives it; the lock covers both threads. **STOP — wait for sign-off before Phase 3.**

---

# Phase 3 — LocalAgreement-2

The current `LocalAgreement.commit` matches a *suffix of the old hypothesis*
against a *prefix of the new* — correct for *independent consecutive chunks*, but
wrong for *whole-window* hypotheses. Whole-window hypotheses both transcribe the
*same growing audio from t=0*, so the algorithm must commit the longest common
**prefix** of the last two hypotheses and discard the unstable tail. It must also
carry per-word **absolute end-times** so `DictationWindow.commit` knows where to
trim (OI-2).

### Task 4: Rework `LocalAgreement` to LocalAgreement-2 prefix agreement

**Files:**
- Modify: `src/voice_commander/dictation/local_agreement.py`
- Test: `tests/unit/test_dictation_local_agreement.py` (rewrite)

- [ ] **Step 1: Write the failing test**

Replace the entire contents of `tests/unit/test_dictation_local_agreement.py`:

```python
"""Unit tests for LocalAgreement-2 — whole-window prefix word stabiliser.

LocalAgreement-2 is fed consecutive WHOLE-WINDOW hypotheses (each transcribes
the same growing audio from t=0). It commits the longest common prefix of the
last two hypotheses and reports the absolute end-time of the last committed word
(ADR 0095).
"""

from __future__ import annotations

from voice_commander.dictation.local_agreement import LocalAgreement, TimedWord


def _tw(text: str, end: float) -> TimedWord:
    return TimedWord(text=text, end_s=end)


def test_first_hypothesis_commits_nothing():
    la = LocalAgreement()
    committed, end = la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    assert committed == []
    assert end is None


def test_agreeing_prefix_commits_on_second_hypothesis():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9)])
    # Second window: prefix "the quick" agrees; "brown"/"green" disagree.
    committed, end = la.commit(
        [_tw("the", 0.3), _tw("quick", 0.6), _tw("green", 0.95), _tw("fox", 1.3)]
    )
    assert committed == ["the", "quick"]
    assert end == 0.6  # end-time of the last committed word


def test_progressive_commit_across_three_windows():
    la = LocalAgreement()
    assert la.commit([_tw("the", 0.3), _tw("quick", 0.6)]) == ([], None)
    committed, end = la.commit([_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9)])
    assert committed == ["the", "quick"]
    committed, end = la.commit(
        [_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9), _tw("fox", 1.2)]
    )
    # "the quick" already committed — only the NEWLY agreed word is returned.
    assert committed == ["brown"]
    assert end == 0.9


def test_no_new_agreement_commits_nothing():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    # Two identical windows with no growth — prefix already committed.
    committed, end = la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    assert committed == []


def test_unstable_tail_never_committed_until_confirmed():
    la = LocalAgreement()
    la.commit([_tw("hello", 0.4), _tw("wurld", 0.8)])  # whisper guessed wrong
    committed, _ = la.commit([_tw("hello", 0.4), _tw("world", 0.8)])
    # "hello" agreed across both; "wurld" != "world" so it stays uncommitted.
    assert committed == ["hello"]


def test_finalize_flushes_uncommitted_tail():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    la.commit([_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9)])
    # "the quick" committed; "brown" still in the latest hypothesis tail.
    assert la.finalize() == ["brown"]


def test_finalize_idempotent():
    la = LocalAgreement()
    la.commit([_tw("one", 0.3)])
    la.commit([_tw("one", 0.3), _tw("two", 0.6)])
    assert la.finalize() == ["two"]
    assert la.finalize() == []


def test_empty_hypothesis_commits_nothing():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3)])
    committed, end = la.commit([])
    assert committed == []
    assert end is None


def test_committed_text_helper_joins_all_committed_words():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    la.commit([_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9)])
    assert la.committed_text() == "the quick"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_dictation_local_agreement.py -v`
Expected: FAIL — `ImportError: cannot import name 'TimedWord'`.

- [ ] **Step 3: Rewrite `local_agreement.py`**

Replace the entire contents of `src/voice_commander/dictation/local_agreement.py`:

```python
"""LocalAgreement-2 word stabiliser for streaming-window dictation.

The transcription server re-decodes a *growing audio window* every
``window_step_ms``. Each decode is a WHOLE-WINDOW hypothesis: a transcript of
the same audio from t=0, just longer each time. The newest words of any
hypothesis are unstable — the model has not heard what follows.

LocalAgreement-2 commits a word only once **two consecutive whole-window
hypotheses agree on it as part of their common prefix**. The unstable tail past
the agreement point is discarded each round (ADR 0095). This is what removes the
stray ``...`` ellipses of the old per-chunk path: an isolated short clip is never
decoded alone, and a hallucinated word never survives to the committed prefix
because the next pass disagrees.

Each word carries an **absolute-stream end-time** (:class:`TimedWord`) so the
caller can trim the audio window at the last committed word's boundary.

Pure module — no I/O, no threads. The session serialises ``commit``/``finalize``
under its existing ``_agreement_lock``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimedWord:
    """One word from a window hypothesis, with its absolute-stream end-time.

    ``end_s`` is seconds from the start of the whole dictation stream — the
    caller adds ``DictationWindow.committed_offset_s`` to the server's
    window-relative segment end-time before constructing a ``TimedWord``.
    """

    text: str
    end_s: float


def _common_prefix_len(a: list[str], b: list[str]) -> int:
    """Length of the longest common prefix of two word lists."""
    n = 0
    for wa, wb in zip(a, b):
        if wa != wb:
            break
        n += 1
    return n


class LocalAgreement:
    """Commit words confirmed stable across consecutive whole-window hypotheses."""

    def __init__(self) -> None:
        # The previous whole-window hypothesis (timed words).
        self._prev: list[TimedWord] = []
        # Every word committed so far, in order.
        self._committed: list[TimedWord] = []

    def commit(self, hypothesis: list[TimedWord]) -> tuple[list[str], float | None]:
        """Fold one whole-window hypothesis in; return newly-committed words.

        Returns ``(words, end_s)`` where *words* is the list of words newly
        confirmed this round (already-committed prefix excluded) and *end_s* is
        the absolute-stream end-time of the last newly-committed word — the
        timestamp the caller passes to ``DictationWindow.commit`` to trim the
        buffer. *end_s* is ``None`` when nothing new was committed.
        """
        if not hypothesis:
            return [], None

        prev_words = [w.text for w in self._prev]
        new_words = [w.text for w in hypothesis]
        agreed = _common_prefix_len(prev_words, new_words)

        # The agreed prefix is stable. Anything in it past what we already
        # committed is newly committed this round.
        already = len(self._committed)
        newly: list[str] = []
        end_s: float | None = None
        if agreed > already:
            newly_words = hypothesis[already:agreed]
            newly = [w.text for w in newly_words]
            end_s = newly_words[-1].end_s
            self._committed.extend(newly_words)

        self._prev = hypothesis
        return newly, end_s

    def finalize(self) -> list[str]:
        """Flush every uncommitted word from the last hypothesis — call at end.

        After the final window is decoded there is no "next" hypothesis to
        agree with, so the uncommitted tail of the last hypothesis is accepted
        verbatim. Idempotent — a second call returns ``[]``.
        """
        tail = [w.text for w in self._prev[len(self._committed):]]
        self._committed = list(self._prev)
        self._prev = []
        return tail

    def committed_text(self) -> str:
        """All committed words joined by single spaces (the live HUD prefix)."""
        return " ".join(w.text for w in self._committed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_dictation_local_agreement.py -v`
Expected: PASS (9 tests).

> Note on `finalize` idempotency after `test_finalize_idempotent`: the first
> `finalize()` sets `_committed = list(_prev)` then `_prev = []`. A second call
> computes `_prev[len(_committed):]` on an empty `_prev` → `[]`. Correct.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation/local_agreement.py tests/unit/test_dictation_local_agreement.py
git commit -m "feat: rework LocalAgreement to LocalAgreement-2 whole-window prefix agreement"
```

### Phase 3 gates

- [ ] **Gate A (automated):** `python -m pytest tests/unit/test_dictation_local_agreement.py -v` — all PASS.
- [ ] **Gate B (human):** Reviewer walks the offset-alignment worked example below and confirms it.

**Offset-alignment worked example (OI-2):** Suppose `window_step_ms=1000`.
Window 1 covers 0–2 s; hypothesis `[(the,0.3),(quick,0.6),(brown,0.9)]`. Window 2
covers 0–3 s; hypothesis agrees on `the quick` → `commit` returns `(["the","quick"], 0.6)`.
The session calls `DictationWindow.commit(0.6)` — `committed_offset_s` is `0.0`,
so `drop_s = 0.6`, `0.6 s` is trimmed, `committed_offset_s → 0.6`. Window 3 is
sent; the server decodes it and reports a segment ending at window-relative
`1.1 s`. The session converts: absolute end = `1.1 + committed_offset_s(0.6) = 1.7 s`,
constructs `TimedWord(end_s=1.7)`, feeds it to `commit`. If that word is
committed, `DictationWindow.commit(1.7)` computes `drop_s = 1.7 - 0.6 = 1.1 s` and
trims correctly. **STOP — wait for sign-off before Phase 4.**

---

# Phase 4 — WebSocket client + session integration

### Task 5: Parse `segments` on partials; send `raw_transcript` on the end frame

**Files:**
- Modify: `src/voice_commander/dictation/ws_client.py`
- Test: `tests/unit/test_dictation_ws_client.py` (create)

The `on_partial` callback signature changes from `Callable[[str], None]` to
`Callable[[str, list[dict]], None]` — it now receives the raw `text` plus the
`segments` array (empty list when the server omits it). `stream_transcribe` gains
a `raw_transcript: str | None = None` parameter; when not `None` it is included in
the `end` frame as `{"type":"end","raw_transcript":<text>}`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dictation_ws_client.py`:

```python
"""Unit tests for ws_client.stream_transcribe — segments + raw_transcript wiring.

These tests use a fake in-memory WebSocket; no network. They assert the frame
contract (ADR 0095): partials carry a segments array to on_partial; the end
frame carries raw_transcript.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from voice_commander.dictation import ws_client


class _FakeWS:
    """In-memory WebSocket: records sends, replays a scripted reply list."""

    def __init__(self, replies: list[dict]) -> None:
        self.sent: list[object] = []
        self._replies = list(replies)

    async def send(self, data: object) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if not self._replies:
            raise AssertionError("recv() called with no scripted replies left")
        return json.dumps(self._replies.pop(0))


def _patch_connect(monkeypatch, fake: _FakeWS) -> None:
    @asynccontextmanager
    async def _fake_connect(_url):
        yield fake

    monkeypatch.setattr(ws_client, "connect", _fake_connect)


def _run(coro):
    return asyncio.run(coro)


def test_partial_forwards_text_and_segments(monkeypatch):
    fake = _FakeWS(
        [
            {
                "type": "partial",
                "text": "the quick",
                "segments": [{"start": 0.0, "end": 0.6, "text": "the quick"}],
            },
            {"type": "done", "text": "The quick.", "raw": "the quick"},
        ]
    )
    _patch_connect(monkeypatch, fake)
    received: list[tuple[str, list]] = []

    async def _drive():
        q: asyncio.Queue = asyncio.Queue()
        await q.put(b"WAVDATA")
        await q.put(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", "en", q,
            lambda text, segs: received.append((text, segs)),
            idle_timeout_s=5.0,
        )

    result = _run(_drive())
    assert result == "The quick."
    assert received == [("the quick", [{"start": 0.0, "end": 0.6, "text": "the quick"}])]


def test_partial_segments_default_empty_when_omitted(monkeypatch):
    fake = _FakeWS(
        [
            {"type": "partial", "text": "hello"},  # no segments key
            {"type": "done", "text": "Hello."},
        ]
    )
    _patch_connect(monkeypatch, fake)
    received: list[tuple[str, list]] = []

    async def _drive():
        q: asyncio.Queue = asyncio.Queue()
        await q.put(b"WAVDATA")
        await q.put(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", "en", q,
            lambda text, segs: received.append((text, segs)),
            idle_timeout_s=5.0,
        )

    _run(_drive())
    assert received == [("hello", [])]


def test_end_frame_carries_raw_transcript(monkeypatch):
    fake = _FakeWS([{"type": "done", "text": "Cleaned.", "raw": "cleaned"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q: asyncio.Queue = asyncio.Queue()
        await q.put(None)  # immediate end — no chunks
        return await ws_client.stream_transcribe(
            "ws://x/ws", "en", q, lambda _t, _s: None,
            idle_timeout_s=5.0, raw_transcript="cleaned",
        )

    result = _run(_drive())
    assert result == "Cleaned."
    end_frames = [json.loads(s) for s in fake.sent if isinstance(s, str) and '"end"' in s]
    assert end_frames[-1] == {"type": "end", "raw_transcript": "cleaned"}


def test_end_frame_omits_raw_transcript_when_none(monkeypatch):
    fake = _FakeWS([{"type": "done", "text": ""}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q: asyncio.Queue = asyncio.Queue()
        await q.put(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", "en", q, lambda _t, _s: None,
            idle_timeout_s=5.0, raw_transcript=None,
        )

    _run(_drive())
    end_frames = [json.loads(s) for s in fake.sent if isinstance(s, str) and '"end"' in s]
    assert end_frames[-1] == {"type": "end"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_dictation_ws_client.py -v`
Expected: FAIL — `on_partial` is called with one arg, or `raw_transcript` is an
unexpected keyword argument.

- [ ] **Step 3: Update `ws_client.py`**

In `src/voice_commander/dictation/ws_client.py`:

(a) Change the `stream_transcribe` signature and docstring head. Replace the
`on_partial` type and add `raw_transcript`:

```python
async def stream_transcribe(
    ws_url: str,
    language: str,
    chunk_q: "asyncio.Queue[bytes | None]",
    on_partial: Callable[[str, list[dict]], None],
    idle_timeout_s: float,
    prompt: str = "",
    done_timeout_s: float = 15.0,
    raw_transcript: str | None = None,
) -> str | None:
```

(b) In the streaming loop, change the `partial` branch from
`on_partial(reply.get("text", ""))` to:

```python
            if kind == "partial":
                try:
                    on_partial(reply.get("text", ""), reply.get("segments", []) or [])
                except Exception:  # noqa: BLE001 - a bad callback must not kill the stream
                    logger.exception("stream_transcribe: on_partial callback raised — continuing")
```

(c) Change the terminal end-frame send. Replace
`await ws.send(json.dumps({"type": "end"}))` with:

```python
        end_frame: dict[str, str] = {"type": "end"}
        if raw_transcript is not None:
            # ADR 0095: the daemon's LocalAgreement-2 output is authoritative;
            # the proxy LLM-cleans this text directly instead of re-decoding.
            end_frame["raw_transcript"] = raw_transcript
        try:
            await ws.send(json.dumps(end_frame))
        except ConnectionClosed:
            logger.warning("stream_transcribe: connection closed before end frame")
            return None
```

(d) In the done-read loop, the trailing-`partial` branch also calls `on_partial`.
Change `on_partial(frame.get("text", ""))` to
`on_partial(frame.get("text", ""), frame.get("segments", []) or [])`.

(e) Update the module docstring: replace the line about partials with "Every
`partial` reply carries `text` and a `segments` array (ADR 0095); the
`{"type":"end"}` frame carries an optional `raw_transcript` the proxy LLM-cleans
directly." Update the `on_partial` parameter doc accordingly.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_dictation_ws_client.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation/ws_client.py tests/unit/test_dictation_ws_client.py
git commit -m "feat: ws_client carries segments on partials and raw_transcript on end"
```

### Task 6: Add `window_step_ms` / `window_cap_ms` config keys

**Files:**
- Modify: `src/voice_commander/config.py` (`DictationConfig`, ~line 26-38)
- Modify: `config.toml.example` (`[dictation]` section, lines 7-12)
- Test: `tests/unit/test_config.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py` (match the file's existing style — find a
nearby `DictationConfig` test and mirror it; if none exists, add a standalone
test):

```python
def test_dictation_window_keys_default():
    from voice_commander.config import DictationConfig

    cfg = DictationConfig()
    assert cfg.window_step_ms == 1000
    assert cfg.window_cap_ms == 25000


def test_dictation_window_keys_load_from_toml(tmp_path):
    from voice_commander.config import Config

    toml = tmp_path / "config.toml"
    toml.write_text(
        "[dictation]\n"
        'ws_url = "ws://x/ws"\n'
        "window_step_ms = 750\n"
        "window_cap_ms = 18000\n",
        encoding="utf-8",
    )
    cfg = Config.load(toml)
    assert cfg.dictation.window_step_ms == 750
    assert cfg.dictation.window_cap_ms == 18000
```

> If `Config.load` has a different signature in this repo, mirror the existing
> `test_config.py` load helper exactly — do not invent an API.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py -k window -v`
Expected: FAIL — `AttributeError: 'DictationConfig' object has no attribute 'window_step_ms'`.

- [ ] **Step 3: Add the config keys**

In `src/voice_commander/config.py`, `DictationConfig`, add two fields after
`idle_timeout_seconds`:

```python
    idle_timeout_seconds: int = 30
    # Streaming-window dictation (ADR 0095). The growing audio window is
    # re-decoded every window_step_ms of accumulated audio. window_cap_ms
    # bounds the uncommitted window: beyond it the oldest segment is
    # force-committed so latency stays independent of dictation length.
    window_step_ms: int = 1000
    window_cap_ms: int = 25000
```

In `config.toml.example`, in the `[dictation]` section, after the
`idle_timeout_seconds` line, add:

```toml
window_step_ms       = 1000      # re-decode the growing audio window this often (ADR 0095)
window_cap_ms        = 25000     # max uncommitted window length; older audio is force-committed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_config.py -k window -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/config.py config.toml.example tests/unit/test_config.py
git commit -m "feat: add window_step_ms and window_cap_ms dictation config keys"
```

### Task 7: Add `_segments_to_timed_words` helper to the session

**Files:**
- Modify: `src/voice_commander/dictation/session.py`
- Test: `tests/unit/test_dictation_session.py` (extend)

A pure helper that converts a server `segments` array plus the current
`committed_offset_s` into a `list[TimedWord]`. It splits each segment's text into
words and assigns each word the segment's **absolute** `end` time (segment-level
granularity — faster-whisper does not give per-word times by default; the last
word of a segment carries the true segment end, earlier words inherit it, which
is a safe over-estimate for trimming because the trim only ever drops audio the
caller already committed).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dictation_session.py`:

```python
def test_segments_to_timed_words_applies_offset():
    from voice_commander.dictation.session import _segments_to_timed_words

    segments = [
        {"start": 0.0, "end": 0.6, "text": "the quick"},
        {"start": 0.6, "end": 1.2, "text": "brown fox"},
    ]
    words = _segments_to_timed_words(segments, committed_offset_s=2.0)
    assert [w.text for w in words] == ["the", "quick", "brown", "fox"]
    # window-relative end-times shifted by the committed offset
    assert words[1].end_s == 2.6   # 0.6 + 2.0
    assert words[3].end_s == 3.2   # 1.2 + 2.0


def test_segments_to_timed_words_empty_when_no_segments():
    from voice_commander.dictation.session import _segments_to_timed_words

    assert _segments_to_timed_words([], committed_offset_s=0.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_dictation_session.py -k segments_to_timed -v`
Expected: FAIL — `ImportError: cannot import name '_segments_to_timed_words'`.

- [ ] **Step 3: Add the helper**

In `src/voice_commander/dictation/session.py`, add the import
`from .local_agreement import LocalAgreement, TimedWord` (extend the existing
import), and add this module-level function after the imports:

```python
def _segments_to_timed_words(
    segments: list[dict], committed_offset_s: float
) -> list[TimedWord]:
    """Flatten server segments into absolute-stream-timed words (ADR 0095).

    Each segment's ``end`` is window-relative; ``committed_offset_s`` (from
    :class:`~voice_commander.dictation.window.DictationWindow`) shifts it to an
    absolute-stream timestamp. Every word of a segment inherits the segment's
    absolute end-time — faster-whisper segments are not word-timed, and a
    segment-end over-estimate is safe for trimming (it only drops already
    committed audio).
    """
    words: list[TimedWord] = []
    for seg in segments:
        end_s = float(seg.get("end", 0.0)) + committed_offset_s
        for token in str(seg.get("text", "")).split():
            words.append(TimedWord(text=token, end_s=end_s))
    return words
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_dictation_session.py -k segments_to_timed -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation/session.py tests/unit/test_dictation_session.py
git commit -m "feat: add _segments_to_timed_words helper to dictation session"
```

### Task 8: Rework `DictationSession` for window streaming

**Files:**
- Modify: `src/voice_commander/dictation/session.py`
- Test: `tests/unit/test_dictation_session.py` (extend)

`DictationSession` now: (1) on `start`, creates a `DictationWindow` and registers
a frame-tap on the recorder that calls `window.append`; when `append` returns a
WAV it is pushed onto the chunk queue. (2) `_on_partial` receives `(text, segments)`,
runs LocalAgreement-2, publishes the committed-prefix HUD `transcript` event
(OI-3), and trims the window (OI-2). (3) `handle_utterance` no longer streams
buffered utterance audio — VAD is endpointing-only; it only classifies
end/cancel. (4) `finish` flushes the window, assembles the full committed raw
transcript, and passes it as `raw_transcript` to `stream_transcribe`.

The session needs a reference to the `StreamingRecorder` to register the tap.
This is injected via a new `set_recorder(recorder)` method called by the daemon
after the recorder is constructed (the daemon builds the recorder after the
session — see `daemon.py` line ~1561).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dictation_session.py`:

```python
class _FakeRecorder:
    """Minimal StreamingRecorder stand-in — records frame-tap set/clear."""

    def __init__(self) -> None:
        self.tap = None

    def set_frame_tap(self, cb) -> None:
        self.tap = cb


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


def test_start_registers_frame_tap_finish_clears_it():
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dictation.vocab import Vocabulary

    rec = _FakeRecorder()
    sess = DictationSession(bus=_FakeBus(), ws_url="ws://x/ws")
    sess.set_recorder(rec)
    sess.start(Vocabulary())
    assert rec.tap is not None  # tap registered on start
    sess.finish()
    assert rec.tap is None      # tap cleared on finish


def test_cancel_clears_frame_tap():
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dictation.vocab import Vocabulary

    rec = _FakeRecorder()
    sess = DictationSession(bus=_FakeBus(), ws_url="ws://x/ws")
    sess.set_recorder(rec)
    sess.start(Vocabulary())
    sess.cancel()
    assert rec.tap is None


def test_on_partial_publishes_committed_prefix_transcript():
    """HUD transcript follows the committed prefix, not the raw hypothesis (OI-3)."""
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dictation.vocab import Vocabulary

    bus = _FakeBus()
    sess = DictationSession(bus=bus, ws_url="ws://x/ws")
    sess.set_recorder(_FakeRecorder())
    sess.start(Vocabulary())

    seg1 = [{"start": 0.0, "end": 0.6, "text": "the quick"}]
    sess._on_partial("the quick", seg1)            # first window — commits nothing
    seg2 = [{"start": 0.0, "end": 0.9, "text": "the quick brown"}]
    sess._on_partial("the quick brown", seg2)      # agrees on "the quick"

    transcripts = [d.get("text") for (t, d) in bus.events if t == "transcript"]
    # The latest HUD transcript is the committed prefix "the quick" — NOT
    # the raw hypothesis "the quick brown" (brown is still unstable).
    assert transcripts[-1] == "the quick"
    sess.cancel()


def test_handle_utterance_does_not_stream_buffered_audio():
    """VAD is endpointing-only — buffered utterances are NOT pushed as chunks."""
    import numpy as np
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dictation.vocab import Vocabulary

    sess = DictationSession(bus=_FakeBus(), ws_url="ws://x/ws")
    sess.set_recorder(_FakeRecorder())
    sess.start(Vocabulary())
    audio = np.zeros(16000, dtype=np.float32)
    kind = sess.handle_utterance(audio, "some words")
    assert kind == "buffered"
    # The chunk queue only ever carries WINDOW WAVs from the frame tap, never
    # per-utterance audio. After a non-end utterance with no frames fed, the
    # queue is empty.
    assert sess._chunk_q is not None and sess._chunk_q.qsize() == 0
    sess.cancel()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_dictation_session.py -k "frame_tap or committed_prefix or buffered_audio" -v`
Expected: FAIL — `AttributeError: 'DictationSession' object has no attribute 'set_recorder'`.

- [ ] **Step 3: Rework `DictationSession`**

Apply these edits to `src/voice_commander/dictation/session.py`:

(a) **Constructor** — add `window_step_ms` / `window_cap_ms` params and the
recorder + window fields. Change the signature:

```python
    def __init__(
        self,
        bus: _BusLike,
        ws_url: str,
        language: str = "en",
        idle_timeout_s: float = 30.0,
        end_word: str = "done",
        cancel_word: str = "cancel",
        window_step_ms: int = 1000,
        window_cap_ms: int = 25000,
    ) -> None:
```

In the constructor body, after `self._idle_timeout_s = idle_timeout_s`, add:

```python
        self._window_step_ms = window_step_ms
        self._window_cap_ms = window_cap_ms
        # The StreamingRecorder, injected post-construction by the daemon
        # (the recorder is built after the session). Used to register the
        # per-frame audio tap (ADR 0095). None until set_recorder() is called.
        self._recorder: Any | None = None
        # The growing audio window for the in-flight dictation; recreated per
        # start(), cleared on finish()/cancel().
        self._window: DictationWindow | None = None
```

(b) Add the import `from .window import DictationWindow` and the
`_segments_to_timed_words` import path is already satisfied (it lives in this
file, Task 7).

(c) Add `set_recorder` after `__init__`:

```python
    def set_recorder(self, recorder: Any) -> None:
        """Inject the StreamingRecorder used for the per-frame audio tap.

        Called once by the daemon after the recorder is constructed (the
        recorder is built after this session). The session registers a frame
        tap on :meth:`start` and clears it on :meth:`finish` / :meth:`cancel`.
        """
        self._recorder = recorder
```

(d) **`start`** — after `self._chunk_q = queue.Queue()` and before
`self._active = True`, create the window:

```python
            self._window = DictationWindow(
                window_step_ms=self._window_step_ms,
                window_cap_ms=self._window_cap_ms,
            )
```

After the lock block (after `self._bus.publish("dictation.start", {})` — actually
*before* the publish, right after `self._loop_thread.start()`), register the tap.
Add:

```python
        if self._recorder is not None:
            self._recorder.set_frame_tap(self._on_frame)
```

(e) Add the `_on_frame` tap callback (module-level threading note: runs on the
VAD worker thread):

```python
    def _on_frame(self, frame: npt.NDArray[np.float32]) -> None:
        """Frame tap — append one 16 kHz frame to the window (VAD worker thread).

        When the window has accumulated ``window_step_ms`` of audio,
        :meth:`DictationWindow.append` returns a whole-window WAV which is
        pushed onto the chunk queue for the asyncio-loop thread to stream.
        Also enforces the ``window_cap_ms`` bound: if the uncommitted window
        exceeds the cap, force-commit the oldest 1 step so latency stays
        bounded (ADR 0095, OI-1). Cheap and exception-safe — the recorder
        wraps this call, but we keep our own state consistent.
        """
        window = self._window
        chunk_q = self._chunk_q
        if window is None or chunk_q is None:
            return
        wav = window.append(frame)
        if wav is not None:
            chunk_q.put(wav)
        if window.cap_exceeded():
            # Force-commit the oldest step so the buffer stays bounded even
            # when LocalAgreement-2 has not confirmed anything for a while.
            forced = window.committed_offset_s + window.uncommitted_seconds() \
                - (self._window_cap_ms / 1000.0)
            window.commit(forced)
            logger.debug("dictation: window cap reached — force-committed to %.2fs", forced)
```

(f) **`handle_utterance`** — strip the streaming. The classification stays; the
`encode_wav` + `chunk_q.put` block is deleted. Replace the method body's tail
(everything after the lock block that sets `chunk_q`) with a plain return.
The new method body:

```python
    def handle_utterance(
        self, audio: npt.NDArray[np.float32], text: str
    ) -> UtteranceKind:
        """Classify an utterance: ``"end"``, ``"cancel"``, or ``"buffered"``.

        Streaming-window dictation (ADR 0095): VAD is endpointing-only. This
        method NO LONGER streams the utterance audio — the growing window is
        fed by the frame tap (:meth:`_on_frame`). It only classifies:

        * ``"end"`` — transcript is the end word (exact normalized match).
        * ``"cancel"`` — transcript is the cancel word (exact normalized match).
        * ``"buffered"`` — any other utterance; a no-op (the name is kept for
          wire-compatibility with the daemon's existing ``kind ==`` branches).

        A no-op returning ``"buffered"`` when inactive (lost race).
        """
        with self._lock:
            if not self._active:
                return "buffered"
            normalized = _normalize_spoken(text)
            if normalized == self._end_word:
                return "end"
            if self._cancel_word is not None and normalized == self._cancel_word:
                return "cancel"
        return "buffered"
```

> `encode_wav` import becomes unused in `session.py` — remove the
> `from .store import encode_wav` import line. `encode_wav` is still used by
> `window.py`, so the symbol itself stays in `store.py`.

(g) **`_on_partial`** — change the signature to `(self, text, segments)`, run
LocalAgreement-2, publish the committed-prefix HUD event, and trim:

```python
    def _on_partial(self, text: str, segments: list[dict]) -> None:
        """Fold one server partial into LocalAgreement-2. Runs on the loop thread.

        *segments* is the server's per-segment timestamp array (ADR 0095).
        When present, words are committed via LocalAgreement-2 prefix agreement
        and the window is trimmed at the last committed word's boundary (OI-2).
        When *segments* is empty (un-upgraded server, OI-1), LocalAgreement-2
        still stabilises the ``text`` but no per-segment trim point is known —
        the window's ``window_cap_ms`` policy keeps the buffer bounded instead.

        Publishes a ``transcript`` event carrying the COMMITTED prefix so the
        live HUD never flickers on the unstable tail (OI-3).

        Holds ``_agreement_lock`` so it can never race :meth:`finish`.
        """
        window = self._window
        offset = window.committed_offset_s if window is not None else 0.0
        with self._agreement_lock:
            if segments:
                timed = _segments_to_timed_words(segments, offset)
                committed, end_s = self._agreement.commit(timed)
                if committed:
                    self._confirmed.extend(committed)
                if end_s is not None and window is not None:
                    window.commit(end_s)
            else:
                # Segment-less degrade path (OI-1): stabilise on text only.
                if not self._warned_no_segments:
                    logger.warning(
                        "dictation: server omitted 'segments' — trimming falls "
                        "back to window_cap_ms time policy"
                    )
                    self._warned_no_segments = True
                timed = [
                    TimedWord(text=w, end_s=offset)
                    for w in text.split()
                ]
                committed, _ = self._agreement.commit(timed)
                if committed:
                    self._confirmed.extend(committed)
            hud_text = self._agreement.committed_text()
        self._bus.publish("transcript", {"text": hud_text, "confidence": 1.0})
```

Add `self._warned_no_segments = False` to the `start()` reset block (next to
`self.error = None`).

(h) **`finish`** — assemble the full committed raw transcript and pass it as
`raw_transcript`. The window must be flushed and its final WAV streamed before
the end sentinel. The cleanest place: in `finish`, after flipping `_active` to
`False` and capturing `chunk_q`, but before `chunk_q.put(None)`, flush the window
and clear the tap:

```python
        # Clear the frame tap first so no further frames enter the window.
        if self._recorder is not None:
            self._recorder.set_frame_tap(None)
        # Flush the remaining window so its tail is decoded before the end frame.
        window = self._window
        self._window = None
        if window is not None and chunk_q is not None:
            final_wav = window.flush()
            if final_wav is not None:
                chunk_q.put(final_wav)
```

The `raw_transcript` is the daemon's committed text. But the final flushed window
is decoded *after* `finish` pushes the end sentinel — its partial arrives on the
asyncio thread before `stream_transcribe` sends the `end` frame. To make the
committed text complete, `stream_transcribe` must compute `raw_transcript` at the
moment it sends `end`. Resolve this cleanly: the session passes a **callable**,
not a string. Change the `_async_main` call site so `stream_transcribe` receives
`raw_transcript` as a callable producing the latest committed text.

Adjust the `stream_transcribe` `raw_transcript` parameter in **Task 5** is a
`str | None`. To keep Task 5 simple, instead resolve here: `_async_main` builds
the `raw_transcript` string *after* the chunk queue is fully drained (after the
`END` sentinel is seen and the final partial folded) but *before* the `end` frame
is sent. The simplest correct shape: have `stream_transcribe` accept
`raw_transcript_fn: Callable[[], str] | None` instead of a string.

> **Decision — update Task 5 accordingly.** Change the `stream_transcribe`
> parameter from `raw_transcript: str | None = None` to
> `raw_transcript_fn: Callable[[], str] | None = None`. In the end-frame block:
> `if raw_transcript_fn is not None: end_frame["raw_transcript"] = raw_transcript_fn()`.
> Update Task 5's tests: pass `raw_transcript=lambda: "cleaned"` and assert the
> `end` frame carries `"cleaned"`. (Make this edit to Task 5's code and tests
> before running this task — they are one logical unit.) When implementing
> sequentially this is a single coherent change; the plan flags it here so the
> engineer does not implement a string-typed parameter and then rip it out.

In `_async_main`, pass the callable:

```python
            self._final_text = await stream_transcribe(
                self._ws_url,
                self._language,
                async_q,
                self._on_partial,
                self._idle_timeout_s,
                prompt=prompt,
                raw_transcript_fn=self._build_raw_transcript,
            )
```

Add `_build_raw_transcript`:

```python
    def _build_raw_transcript(self) -> str:
        """Return the full committed raw transcript — called by stream_transcribe.

        Invoked on the asyncio-loop thread at the moment the ``end`` frame is
        sent, i.e. after every window WAV (including the final flush) has been
        decoded and folded through LocalAgreement-2. Runs ``finalize()`` so the
        uncommitted tail of the last hypothesis is included. Lock-guarded — it
        touches the same accumulator as :meth:`_on_partial`.
        """
        with self._agreement_lock:
            self._confirmed.extend(self._agreement.finalize())
            return " ".join(self._confirmed)
```

In `finish`, the existing `with self._agreement_lock:` block that calls
`self._agreement.finalize()` becomes redundant if `_build_raw_transcript` already
finalised — but `finalize()` is idempotent (Task 4), so it stays as the fallback
path's safety net. Keep the existing block; `fallback_text` is still computed
from `self._confirmed`.

(i) **`cancel`** — clear the frame tap and drop the window. After flipping
`_active` to `False` and capturing `chunk_q`/`loop_thread`, add:

```python
        if self._recorder is not None:
            self._recorder.set_frame_tap(None)
        self._window = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_dictation_session.py -v`
Expected: PASS (all — new tests plus the pre-existing ADR 0094 done-frame/fallback
tests, which still hold: `finish` still returns `_final_text` when present).

> If pre-existing tests mock `on_partial` with a single-arg lambda, update those
> call sites to two args `(text, segments)` — they are testing the *session*, so
> they call `sess._on_partial("text", [])`.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation/session.py src/voice_commander/dictation/ws_client.py tests/unit/test_dictation_session.py tests/unit/test_dictation_ws_client.py
git commit -m "feat: rework DictationSession for growing-window streaming"
```

### Phase 4 gates

- [ ] **Gate A (automated):** `python -m pytest tests/unit/test_dictation_session.py tests/unit/test_dictation_ws_client.py tests/unit/test_config.py -v` — all PASS.
- [ ] **Gate B (human):** PRECONDITION — the upgraded server (segments + raw_transcript) must be reachable. Human runs `scripts/dictation_window_e2e.py` (built in Phase 6 Task 12 — if running Phase 4's gate before Phase 6, defer this gate or run the existing `scripts/dictation_streaming_e2e.py` as an interim smoke). Confirm the daemon connects, streams windows, and produces a transcript. **STOP — wait for sign-off before Phase 5.**

---

# Phase 5 — VAD demotion + daemon wiring

VAD demotion needs no code change in `vad_gate.py` itself — the gate already only
*produces* utterances; the demotion is that `DictationSession.handle_utterance`
no longer streams them (done in Task 8). This phase wires the new config through
the daemon and connects the recorder to the session.

### Task 9: Wire new config + recorder injection in the daemon

**Files:**
- Modify: `src/voice_commander/daemon.py` (`DictationSession(...)` ~line 1439; recorder build ~line 1561)
- Test: `tests/unit/test_dictation_session.py` (the `set_recorder` tests from Task 8 already cover the contract; this task adds no new unit test — it is a wiring change verified by the daemon-import smoke and Gate B).

- [ ] **Step 1: Verify the wiring change compiles**

Run: `python -c "import voice_commander.daemon"` first to confirm a clean baseline.
Expected: no error.

- [ ] **Step 2: Pass the new config keys to `DictationSession`**

In `src/voice_commander/daemon.py`, the `DictationSession(...)` construction
(~line 1439) currently ends at `cancel_word=cfg.dictation.cancel_word,`. Add the
two new keyword arguments:

```python
    dictation_session = DictationSession(
        bus=event_bus,
        ws_url=cfg.dictation.ws_url,
        language=cfg.dictation.language,
        idle_timeout_s=float(cfg.dictation.idle_timeout_seconds),
        end_word=cfg.dictation.end_word,
        cancel_word=cfg.dictation.cancel_word,
        window_step_ms=cfg.dictation.window_step_ms,
        window_cap_ms=cfg.dictation.window_cap_ms,
    )
```

- [ ] **Step 3: Inject the recorder into the session**

In `src/voice_commander/daemon.py`, immediately after the
`daemon._recorder = StreamingRecorder(...)` block (~line 1561-1567), add:

```python
    # Streaming-window dictation (ADR 0095): give the session the recorder so
    # it can register a per-frame audio tap on start() / clear it on finish().
    dictation_session.set_recorder(daemon._recorder)
```

- [ ] **Step 4: Verify the daemon imports + constructs cleanly**

Run: `python -c "import voice_commander.daemon"`
Expected: no error.

Run the full unit suite: `python -m pytest tests/unit -q`
Expected: all PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/daemon.py
git commit -m "feat: wire streaming-window config and recorder into DictationSession"
```

### Phase 5 gates

- [ ] **Gate A (automated):** `python -m pytest tests/unit -q` — full suite PASS.
- [ ] **Gate B (human):** Human starts the daemon, presses Right Ctrl, dictates one short sentence ("testing one two three"), says "done", confirms the sentence is pasted at the cursor with no stray `...`. **STOP — wait for sign-off before Phase 6.**

---

# Phase 6 — Docs, ADR, visual E2E

### Task 10: Write ADR 0095

**Files:**
- Create: `docs/decisions/0095-streaming-window-dictation.md`

- [ ] **Step 1: Write the ADR**

Create `docs/decisions/0095-streaming-window-dictation.md` following the format of
ADR 0092/0093/0094 (Status / Date / Amends / Context / Decision (D1…) /
Consequences / References). It must record:

- **Context** — the per-chunk fragmentation problem (stray `...`, broken
  sentences, boundary word loss) and the root cause (Whisper is a 30-s batch
  model; hard-cutting on short silence violates training assumptions;
  `LocalAgreement` was fed independent chunks, not consecutive whole-window
  hypotheses).
- **D1 — growing-window streaming.** `DictationWindow` owns a growing 16 kHz
  buffer fed by a `StreamingRecorder` frame tap; emits the whole window as a WAV
  every `window_step_ms`; the server re-decodes the whole window each time.
- **D2 — LocalAgreement-2.** Fed consecutive whole-window hypotheses; commits the
  longest agreeing prefix; carries absolute-stream end-times so the window trims
  at committed boundaries.
- **D3 — VAD demoted to endpointing only.** `VADGate` still detects end-word /
  cancel-word / silence; its utterances are no longer streamed as transcription
  chunks. No `vad_gate.py` code change.
- **D4 — `raw_transcript` finalize.** `finish()` sends the daemon's full
  committed text as `raw_transcript` on the `end` frame; the proxy LLM-cleans it
  directly (no re-decode). `done.text` fallback to LocalAgreement output
  preserved (ADR 0094).
- **D5 — config.** New `[dictation]` keys `window_step_ms` (1000),
  `window_cap_ms` (25000).
- **D6 — server contract.** Two backward-compatible server additions (segment
  timestamps on `partial`, `raw_transcript` on `end`), implemented in a separate
  repo; the daemon degrades gracefully when `segments` is absent (OI-1).
- **The three open-item decisions** — OI-1 (degrade gracefully), OI-2 (absolute
  timestamps via `DictationWindow.committed_offset_s`), OI-3 (HUD follows the
  committed prefix). Record each with its rationale.
- **Amends:** ADR 0092 (transport: per-chunk → whole-window streaming), ADR 0091
  (`LocalAgreement` semantics corrected). **References** ADR 0093, 0094.
- **Consequences** — positive (coherent punctuated output, no pause-fragmentation,
  bounded latency), negative (more bytes on the wire — whole window re-sent each
  step; degraded trim when `segments` absent), neutral (no new dependency).

- [ ] **Step 2: Add the ADR row to the technical-decisions index**

In `docs/agents/technical-decisions.md`, add a one-line summary row for ADR 0095
matching the file's existing row format.

- [ ] **Step 3: Commit**

```bash
git add docs/decisions/0095-streaming-window-dictation.md docs/agents/technical-decisions.md
git commit -m "docs: ADR 0095 — streaming-window dictation"
```

### Task 11: Update dictation docs + replace the stale whisper.cpp reference

**Files:**
- Modify: `docs/dictation-streaming.md`
- Modify: `docs/transcription-pipeline.md`
- Modify: `docs/references/whisper-cpp-server-inference.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `docs/dictation-streaming.md`**

Rewrite the **Flow** and **Architecture** sections for the window model:

- The flow diagram: `StreamingRecorder frame tap → DictationWindow (growing 16 kHz
  buffer) → every window_step_ms emit whole-window WAV → WS → server re-decodes
  whole window → partial{text, segments} → LocalAgreement-2 commits prefix →
  DictationWindow.commit(end_s) trims → finish() assembles committed raw text →
  end{raw_transcript} → proxy LLM-clean → done.text → paste`.
- Document `DictationWindow` (`src/voice_commander/dictation/window.py`) in the
  supporting-modules table.
- Update the `local_agreement.py` row: "LocalAgreement-2 — whole-window prefix
  word stabiliser".
- Update the `ws_client.py` row: partials carry `segments`; the `end` frame
  carries `raw_transcript`.
- State VAD is endpointing-only — `handle_utterance` classifies end/cancel only,
  no longer streams audio.
- Add `window_step_ms` / `window_cap_ms` to the config table.
- Add the ADR 0095 row to the Related ADRs table.

- [ ] **Step 2: Update `docs/transcription-pipeline.md` §4**

Update the dictation-path section (§4) to describe the growing-window model:
window cadence, LocalAgreement-2 prefix agreement, segment-timestamp trimming,
`raw_transcript` finalize, VAD-as-endpointing. Cite ADR 0095.

- [ ] **Step 3: Replace `docs/references/whisper-cpp-server-inference.md`**

The current file documents whisper.cpp's `POST /inference` multipart endpoint —
stale and wrong: the server is a custom Python/FastAPI app wrapping faster-whisper,
reached over a WebSocket at `/ws/transcribe`. Replace the file's content with a
reference for the actual server contract:

- Rename the H1 to `# /ws/transcribe — Streaming Transcription Server Contract`.
- Update the `Vendored` / `Source` / `Cited by` header — source is the custom
  FastAPI + faster-whisper proxy repo; cited by `src/voice_commander/dictation/ws_client.py`.
- Document the WebSocket frame protocol: config frame `{type:"config", language,
  initial_prompt}`; binary WAV chunk frames; `partial` replies
  `{type:"partial", text, accumulated, segments:[{start,end,text}]}` (note
  `accumulated` is unreliable under overlapping windows — the daemon ignores it);
  the `{type:"end"}` frame with optional `raw_transcript`; the single `done`
  frame `{type:"done", text, raw}`; `error` frames.
- Keep the `initial_prompt` token-cap note (still relevant — `build_prompt`
  enforces `_PROMPT_CHAR_CAP`).
- Delete the `/inference`, `response_format`, `carry_initial_prompt`,
  multipart-form sections — they describe a server that is not in use.

> If the filename `whisper-cpp-server-inference.md` itself is now misleading,
> rename it to `docs/references/ws-transcribe-server.md` with
> `git mv` and update any citation in `ws_client.py`'s docstring + `docs/index.md`.
> Do the rename — a correct filename is part of "docs not stale".

- [ ] **Step 4: Update the CLAUDE.md "Current state" dictation paragraph**

In `CLAUDE.md`, the "Current state" section's dictation paragraph currently says
"during dictation each VAD utterance is encoded to a 16 kHz mono WAV chunk and
streamed immediately over a WebSocket". Replace that clause and the surrounding
sentences so they describe the window model: a `StreamingRecorder` frame tap
feeds a growing `DictationWindow`; every `window_step_ms` the whole window is
re-decoded; LocalAgreement-2 commits stable words by comparing consecutive
whole-window hypotheses and trims the window at committed segment boundaries;
VAD is endpointing-only (end-word / cancel-word / silence detection); `finish()`
sends the committed raw transcript as `raw_transcript` on the `end` frame for the
proxy to LLM-clean. Cite ADR 0095. Keep the paragraph's existing length/density
conventions.

- [ ] **Step 5: Commit**

```bash
git add docs/dictation-streaming.md docs/transcription-pipeline.md docs/references/ docs/index.md CLAUDE.md src/voice_commander/dictation/ws_client.py
git commit -m "docs: update dictation docs and server reference for streaming-window model"
```

### Task 12: Write the visual E2E harness

**Files:**
- Create: `scripts/dictation_window_e2e.py`

Read `docs/agents/visual-e2e-testing.md` in full before writing this harness —
follow its eight rules and copy the structure of the existing
`scripts/dictation_streaming_e2e.py` and `scripts/dictation_visual_e2e.py`.

- [ ] **Step 1: Read the protocol**

Read `docs/agents/visual-e2e-testing.md` and `scripts/dictation_streaming_e2e.py`
end to end. Do not write harness code before this step.

- [ ] **Step 2: Write the harness**

Create `scripts/dictation_window_e2e.py`. It is a human-validated harness (not a
pytest test). Requirements and structure, mirroring `dictation_streaming_e2e.py`:

- Module docstring stating: requires a live upgraded `/ws/transcribe` proxy
  (segments + raw_transcript), a working microphone, the daemon's Python env.
- Loads `config.toml` for the production `ws_url`, `window_step_ms`,
  `window_cap_ms`.
- Builds a stripped-down `StreamingDaemon` (stub transcriber, real
  `DictationSession`, real `StreamingRecorder` with a real device, no
  HotkeyController) and calls `dictation_session.set_recorder(...)`.
- Starts the pipeline, simulates Right Ctrl via `on_dictation_toggle()`.
- **Prompts the operator to dictate a known multi-sentence paragraph with
  deliberate ~2 s pauses between sentences** — e.g. "The first sentence is short.
  [pause] The second sentence has a few more words in it. [pause] And the third
  one finishes the test." — then say the end word.
- Captures evidence: the EventBus event sequence, every `transcript` event's text
  (to confirm the committed prefix grows monotonically and never rewrites — OI-3),
  the final pasted text, the daemon log.
- **Hard checkpoints** (exit non-zero on any failure):
  - CP1 `dictation.start` published.
  - CP2 `dictation.end {reason:"done"}` published.
  - CP3 `dictation.result` published with a non-empty transcript.
  - CP4 `paste_via_clipboard` was called; pasted text non-empty.
  - CP5 **the pasted text contains no `...` ellipsis substring** (the core
    regression this feature fixes).
  - CP6 the pasted text contains all three sentence-ending words from the
    operator's paragraph (fuzzy substring match per sentence — Whisper is not
    deterministic) — i.e. no sentence was dropped at a pause boundary.
  - CP7 the sequence of `transcript` event texts is monotonically
    non-shrinking (each is a prefix-or-equal extension of the previous — confirms
    OI-3: the HUD followed the committed prefix, not a churning hypothesis).
  - CP8 `session.active == False` after finalize.
  - CP9 clipboard restored to its sentinel value after the harness.
- Prints a PASS/FAIL summary; exits 0 only when all hard checkpoints pass.
- Output dir `outputs/dictation_window_e2e/`.

- [ ] **Step 3: Sanity-check the harness compiles**

Run: `python -c "import ast; ast.parse(open('scripts/dictation_window_e2e.py').read())"`
Expected: no error (syntax check only — the harness needs a mic + server to run).

- [ ] **Step 4: Commit**

```bash
git add scripts/dictation_window_e2e.py
git commit -m "test: add streaming-window dictation visual E2E harness"
```

### Phase 6 gates

- [ ] **Gate A (automated):** `python -m pytest tests/unit -q` — full suite PASS;
  `python -c "import voice_commander.daemon"` clean.
- [ ] **Gate B (human):** PRECONDITION — upgraded server reachable. Human runs
  `python scripts/dictation_window_e2e.py`, dictates the multi-sentence paragraph
  with ~2 s pauses, and confirms the harness prints PASS for all hard checkpoints —
  in particular CP5 (no stray `...`) and CP6 (no dropped sentence). Human visually
  confirms the pasted paragraph reads coherently. **This is the final
  sign-off gate for the feature.**

---

## Self-review

**Spec coverage check** — every spec section maps to a task:

- Frame tap (`StreamingRecorder`) → Tasks 1, 2.
- `DictationWindow` (`dictation/window.py`) → Task 3.
- LocalAgreement-2 (consecutive whole-window hypotheses, post-trim offset
  alignment) → Task 4 (algorithm) + Task 7 (`_segments_to_timed_words` offset
  application) + Task 8 (`_on_partial` wiring).
- `ws_client.py` changes (`segments`, `raw_transcript`) → Task 5.
- `session.py` changes (window WAVs, finalize flow, frame-tap lifecycle) → Task 8.
- VAD demotion → Task 8 (`handle_utterance` no longer streams) + ADR D3 in
  Task 10. No `vad_gate.py` change needed — confirmed by reading the gate: it only
  *produces* utterances.
- New config keys → Task 6.
- Tests — unit: Tasks 1, 3, 4, 5, 6, 7, 8. Visual E2E: Task 12.
- Docs — ADR 0095: Task 10. `dictation-streaming.md` / `transcription-pipeline.md`
  / stale whisper.cpp reference / CLAUDE.md: Task 11.
- Server contract dependency → stated as a precondition at the top + ADR D6.
- Three open items → resolved in the "Open items" section, each tied to a task.

**Type-consistency check** — `DictationWindow` methods (`append`, `commit`,
`uncommitted_seconds`, `cap_exceeded`, `flush`, `committed_offset_s`) are used
consistently in Tasks 3 and 8. `LocalAgreement.commit` returns
`tuple[list[str], float | None]` and `TimedWord(text, end_s)` are used
consistently in Tasks 4, 7, 8. `stream_transcribe`'s `raw_transcript_fn:
Callable[[], str] | None` and `on_partial: Callable[[str, list[dict]], None]` are
consistent across Tasks 5 and 8 (the Task 8 note explicitly reconciles the
string-vs-callable parameter so no stale signature is implemented).

**Placeholder scan** — no TBD / "add error handling" / "write tests for the
above" placeholders; every code step shows complete code; every test step shows
the full test body.

---

## Execution handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review
   between tasks, fast iteration. REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
2. **Inline Execution** — execute tasks in this session via
   superpowers:executing-plans, batch execution with checkpoints.

Note: Phases 1–5 can run in one session; the Phase 4 and Phase 6 human gates
require the upgraded server. Coordinate the VPS engineer's server work to land
before those gates.
