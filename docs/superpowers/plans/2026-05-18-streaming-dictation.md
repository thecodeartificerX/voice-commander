# Streaming Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated, standalone streaming-dictation pipeline — WebSocket transcription with hybrid chunking and LocalAgreement word stabilisation — without touching the shipped batch dictation.

**Architecture:** A new self-contained `voice_commander.dictation_stream` package. Mic audio is captured at the device-native rate, resampled to 16 kHz, and segmented by the *reused* `VADGate` (silence gap or 15 s hard cap → one chunk). Each WAV chunk streams over a WebSocket to the live `/ws/transcribe` endpoint; the server carries `initial_prompt` context chunk-to-chunk. A `LocalAgreement` stabiliser holds back each chunk's unstable suffix until the next chunk confirms it by overlap. Confirmed words accumulate; at session end the full transcript passes through a `transform()` seam (identity now, a small-LLM rewrite later) and is pasted at the cursor. The package runs standalone via `python -m voice_commander.dictation_stream`, toggled by Right Ctrl.

**Tech Stack:** Python 3.11, `websockets>=14.0` (asyncio client), reused `VADGate`/`SileroVADOnnx` (ONNX, torch-free), `soxr` resampler, `sounddevice` capture, `pynput` hotkey, `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-05-18-streaming-dictation-design.md`

---

## File Structure

All new code lives under `src/voice_commander/dictation_stream/` — one file, one responsibility:

| File | Responsibility |
|------|----------------|
| `__init__.py` | Package marker + module docstring. |
| `config.py` | `StreamDictationConfig` dataclass + self-contained `load()` for the `[dictation_stream]` TOML table. |
| `local_agreement.py` | Pure `LocalAgreement` word stabiliser. No I/O. |
| `chunker.py` | `Chunker` — raw native-rate audio → 16 kHz WAV speech chunks via reused `VADGate`. |
| `capture.py` | `MicCapture` — `sounddevice` input stream → raw block queue. |
| `ws_client.py` | `stream_transcribe()` — async WebSocket client; chunks out, partials in. |
| `bridge.py` | `pump()` — sync `queue.Queue` → `asyncio.Queue` bridge. |
| `sink.py` | `transform()` seam + `TextSink` — accumulate confirmed words, paste at end. |
| `session.py` | `StreamSession` — wires capture + chunker + bridge + ws + agreement + sink. |
| `__main__.py` | `SessionToggle` + `main()` — Right Ctrl entrypoint. |

**Reused, never forked:** `voice_commander.vad_gate.VADGate`, `voice_commander.vad_onnx.load_silero_vad`, `voice_commander.resampler.Resampler`, `voice_commander.dictation.store.encode_wav`, `voice_commander.dictation.clipboard.paste_via_clipboard`.

**Tests:** `tests/unit/test_stream_*.py`, `tests/integration/test_stream_*.py`.

**Confirmed upstream signatures** (do not re-derive):
- `VADGate(model, threshold=0.5, min_speech_ms=250, min_silence_ms=100, speech_pad_ms=30, pre_roll_ms=320, max_utterance_ms=30_000)`; `process(frame_16k: NDArray[float32] of shape (512,)) -> NDArray[float32] | None`.
- `load_silero_vad(onnx=True, *, model_path=None, force_cpu=False) -> SileroVADOnnx`.
- `Resampler(src_rate, dst_rate=16000)`; `process(chunk: 1-D float32) -> 1-D float32`; `flush() -> 1-D float32`.
- `encode_wav(audio: NDArray[float32], sample_rate=16000) -> bytes`.
- `paste_via_clipboard(text: str, settle_ms=100) -> None` (blocking; snapshots + restores clipboard).

---

## Task 1: Scaffolding — dependency, package, config, vendored reference

**Files:**
- Modify: `pyproject.toml` (add `websockets>=14.0` to `dependencies`; add a `[project.scripts]` entry)
- Create: `src/voice_commander/dictation_stream/__init__.py`
- Modify: `config.toml.example` (append `[dictation_stream]` section)
- Verify exists: `docs/references/websockets.md` (already vendored during planning)

- [ ] **Step 1: Add the `websockets` dependency**

In `pyproject.toml`, in the `[project]` `dependencies` array, add after the `uiautomation` line:

```toml
    "uiautomation>=2.0.29",
    "websockets>=14.0",
```

- [ ] **Step 2: Add the standalone entrypoint script**

In `pyproject.toml`, in the `[project.scripts]` table, add:

```toml
[project.scripts]
voice-commander = "voice_commander.__main__:main"
voice-sprite = "voice_sprite.__main__:main"
voice-commander-supervisor = "voice_commander.supervisor:main"
voice-dictation-stream = "voice_commander.dictation_stream.__main__:main"
```

- [ ] **Step 3: Create the package marker**

Create `src/voice_commander/dictation_stream/__init__.py`:

```python
"""Streaming dictation experiment — WebSocket transcription + LocalAgreement.

Self-contained, isolated from the shipped batch dictation
(`voice_commander.dictation`). Run standalone:

    python -m voice_commander.dictation_stream

See `docs/superpowers/specs/2026-05-18-streaming-dictation-design.md`.
"""
```

- [ ] **Step 4: Add the `[dictation_stream]` config section**

Append to `config.toml.example`:

```toml

# --- Streaming dictation experiment (ADR 0091) ---
# Isolated experimental pipeline — not used by the daemon. Run standalone via
# `python -m voice_commander.dictation_stream`. Press Right Ctrl to dictate.
[dictation_stream]
ws_url                  = "ws://192.168.4.200:8765/ws/transcribe"  # streaming transcription endpoint
language                = "en"   # whisper decode language
vad_threshold           = 0.5    # silero speech probability cutoff (raise in noisy rooms)
min_silence_duration_ms = 1200   # silence gap that ends a chunk (lets mid-sentence pauses through)
speech_pad_ms           = 300    # audio kept before/after detected speech
max_chunk_seconds       = 15     # hard time cap — force-flush a chunk even without a pause
min_chunk_seconds       = 1      # discard chunks shorter than this (too short for whisper)
idle_timeout_seconds    = 30     # close the stream after this long with no audio
```

- [ ] **Step 5: Install and verify**

Run: `pip install -e .` (or `uv pip install -e .`)
Then: `python -c "import websockets; from websockets.asyncio.client import connect; import voice_commander.dictation_stream; print('ok')"`
Expected: prints `ok` with no ImportError.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml config.toml.example src/voice_commander/dictation_stream/__init__.py docs/references/websockets.md
git commit -m "chore(dictation-stream): scaffold package, add websockets dep and config"
```

---

## Task 2: `LocalAgreement` word stabiliser

**Files:**
- Create: `src/voice_commander/dictation_stream/local_agreement.py`
- Test: `tests/unit/test_stream_local_agreement.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_stream_local_agreement.py`:

```python
"""Unit tests for the LocalAgreement word stabiliser."""

from __future__ import annotations

import pytest

from voice_commander.dictation_stream.local_agreement import LocalAgreement


def test_first_chunk_commits_nothing():
    la = LocalAgreement()
    assert la.commit("The quick brown fox") == []


def test_overlap_commits_stable_prefix():
    la = LocalAgreement()
    la.commit("The quick brown fox")
    assert la.commit("brown fox jumps over") == ["The", "quick"]


def test_progressive_commits_across_three_chunks():
    la = LocalAgreement()
    assert la.commit("The quick brown fox") == []
    assert la.commit("brown fox jumps over") == ["The", "quick"]
    assert la.commit("jumps over the lazy") == ["brown", "fox"]
    assert la.finalize() == ["jumps", "over", "the", "lazy"]


def test_no_overlap_commits_everything():
    la = LocalAgreement()
    la.commit("alpha beta")
    assert la.commit("gamma delta") == ["alpha", "beta"]


def test_full_overlap_commits_nothing():
    la = LocalAgreement()
    la.commit("same words here")
    assert la.commit("same words here") == []


def test_empty_text_commits_nothing_and_keeps_hypothesis():
    la = LocalAgreement()
    la.commit("hello world")
    assert la.commit("") == []
    assert la.commit("   ") == []
    assert la.finalize() == ["hello", "world"]


def test_finalize_on_empty_returns_empty():
    assert LocalAgreement().finalize() == []


def test_finalize_is_idempotent():
    la = LocalAgreement()
    la.commit("one two")
    assert la.finalize() == ["one", "two"]
    assert la.finalize() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_stream_local_agreement.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.local_agreement'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/local_agreement.py`:

```python
"""LocalAgreement word stabiliser for streaming dictation.

Whisper transcribes each audio chunk independently. The last few words of a
chunk are unstable — the model has not heard what follows, so it guesses.
LocalAgreement holds back the unstable suffix of every chunk transcript and
commits a word only once the next chunk's transcript confirms it by overlap.

Pure module — no I/O, no threads. See spec
`docs/superpowers/specs/2026-05-18-streaming-dictation-design.md` section 7.
"""

from __future__ import annotations

from collections import deque


class LocalAgreement:
    """Commit words confirmed stable across consecutive chunk transcripts."""

    def __init__(self) -> None:
        self._hypothesis: deque[str] = deque()

    def commit(self, new_text: str) -> list[str]:
        """Fold a new chunk transcript in; return newly-confirmed words.

        The longest suffix of the current hypothesis equal to a prefix of
        ``new_text`` is the overlap. Hypothesis words *before* that overlap are
        confirmed and returned. The hypothesis is then replaced by ``new_text``
        (the overlap tail plus whatever follows it).
        """
        new_words = new_text.split()
        if not new_words:
            return []

        hyp = list(self._hypothesis)
        overlap = 0
        for k in range(min(len(hyp), len(new_words)), 0, -1):
            if hyp[-k:] == new_words[:k]:
                overlap = k
                break

        stable_count = len(hyp) - overlap
        committed = hyp[:stable_count]
        self._hypothesis = deque(new_words)
        return committed

    def finalize(self) -> list[str]:
        """Flush every remaining hypothesis word — call at session end."""
        remaining = list(self._hypothesis)
        self._hypothesis.clear()
        return remaining
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_stream_local_agreement.py -v`
Expected: PASS — all 8 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation_stream/local_agreement.py tests/unit/test_stream_local_agreement.py
git commit -m "feat(dictation-stream): LocalAgreement word stabiliser"
```

---

## Task 3: `StreamDictationConfig` + loader

**Files:**
- Create: `src/voice_commander/dictation_stream/config.py`
- Test: `tests/unit/test_stream_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_stream_config.py`:

```python
"""Unit tests for streaming dictation config loading."""

from __future__ import annotations

import pytest

from voice_commander.dictation_stream.config import StreamDictationConfig, load


def test_defaults_when_file_absent(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    assert cfg == StreamDictationConfig()
    assert cfg.ws_url == "ws://192.168.4.200:8765/ws/transcribe"
    assert cfg.max_chunk_seconds == 15


def test_defaults_when_section_absent(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nkey = "scroll_lock"\n')
    assert load(path) == StreamDictationConfig()


def test_section_overrides_only_named_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[dictation_stream]\n"
        'ws_url = "ws://example:9000/ws"\n'
        "max_chunk_seconds = 10\n"
    )
    cfg = load(path)
    assert cfg.ws_url == "ws://example:9000/ws"
    assert cfg.max_chunk_seconds == 10
    assert cfg.language == "en"  # untouched default


def test_unknown_key_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[dictation_stream]\nbogus_key = 1\n")
    with pytest.raises(ValueError, match="bogus_key"):
        load(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_stream_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.config'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/config.py`:

```python
"""Configuration for the streaming dictation experiment.

Reads the ``[dictation_stream]`` table from ``config.toml`` into a frozen
dataclass. Fully self-contained — the shipped ``voice_commander.config``
module is never touched, and an absent table yields all defaults. An unknown
``[dictation_stream]`` table elsewhere in ``config.toml`` is harmless: the
daemon's loader ignores unknown top-level tables.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class StreamDictationConfig:
    """Streaming dictation tunables (spec 2026-05-18 section 9)."""

    ws_url: str = "ws://192.168.4.200:8765/ws/transcribe"
    language: str = "en"
    vad_threshold: float = 0.5
    min_silence_duration_ms: int = 1200
    speech_pad_ms: int = 300
    max_chunk_seconds: int = 15
    min_chunk_seconds: int = 1
    idle_timeout_seconds: int = 30


def load(path: Path) -> StreamDictationConfig:
    """Load the ``[dictation_stream]`` table from a TOML file.

    Returns all defaults if the file or the table is absent. Raises
    ``ValueError`` if the table carries a key the dataclass does not define.
    """
    if not path.exists():
        return StreamDictationConfig()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    section = raw.get("dictation_stream", {})
    known = {f.name for f in fields(StreamDictationConfig)}
    unknown = set(section) - known
    if unknown:
        raise ValueError(f"Unknown [dictation_stream] config keys: {sorted(unknown)}")
    return StreamDictationConfig(**section)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_stream_config.py -v`
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation_stream/config.py tests/unit/test_stream_config.py
git commit -m "feat(dictation-stream): StreamDictationConfig and TOML loader"
```

---

## Task 4: `Chunker` — raw audio → WAV speech chunks

**Files:**
- Create: `src/voice_commander/dictation_stream/chunker.py`
- Test: `tests/unit/test_stream_chunker.py`

**Design note:** `Chunker` resamples to 16 kHz, re-frames into the 512-sample blocks `VADGate` needs, and drives `VADGate`. `VADGate` emits a completed utterance on a silence gap *or* at `max_utterance_ms` — that dual trigger *is* the hybrid silence + hard-cap chunking. The `vad` constructor argument allows a fake to be injected for deterministic tests; production passes `None` and a real `VADGate` is built. When the device rate already equals 16 kHz the resampler is skipped (an optimisation that also keeps tests' sample counts exact).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_stream_chunker.py`:

```python
"""Unit tests for the streaming dictation chunker."""

from __future__ import annotations

import numpy as np

from voice_commander.dictation_stream.chunker import Chunker


class _FakeVad:
    """Emits a fixed utterance every ``period`` frames fed to ``process``."""

    def __init__(self, period: int, utterance: np.ndarray) -> None:
        self._period = period
        self._utterance = utterance
        self._count = 0

    def process(self, frame: np.ndarray) -> np.ndarray | None:
        assert frame.shape == (512,)
        self._count += 1
        return self._utterance if self._count % self._period == 0 else None


def test_emits_wav_chunk_when_vad_completes_utterance():
    one_second = np.ones(16_000, dtype=np.float32)
    chunker = Chunker(16_000, min_chunk_seconds=1, vad=_FakeVad(period=2, utterance=one_second))
    raw = np.zeros(512 * 4, dtype=np.float32)  # 4 frames -> 2 utterances at period 2
    chunks = chunker.process_raw(raw)
    assert len(chunks) == 2
    assert all(isinstance(c, bytes) and c[:4] == b"RIFF" for c in chunks)


def test_drops_chunk_shorter_than_min_length():
    half_second = np.ones(8_000, dtype=np.float32)  # 0.5 s < 1 s minimum
    chunker = Chunker(16_000, min_chunk_seconds=1, vad=_FakeVad(period=2, utterance=half_second))
    chunks = chunker.process_raw(np.zeros(512 * 4, dtype=np.float32))
    assert chunks == []


def test_reframes_audio_across_multiple_calls():
    one_second = np.ones(16_000, dtype=np.float32)
    chunker = Chunker(16_000, min_chunk_seconds=1, vad=_FakeVad(period=1, utterance=one_second))
    # 300 + 300 = 600 samples buffered across two calls -> one full 512 frame.
    assert chunker.process_raw(np.zeros(300, dtype=np.float32)) == []
    chunks = chunker.process_raw(np.zeros(300, dtype=np.float32))
    assert len(chunks) == 1


def test_accepts_2d_mono_block():
    one_second = np.ones(16_000, dtype=np.float32)
    chunker = Chunker(16_000, min_chunk_seconds=1, vad=_FakeVad(period=1, utterance=one_second))
    raw = np.zeros((512, 1), dtype=np.float32)  # sounddevice shape (frames, channels)
    chunks = chunker.process_raw(raw)
    assert len(chunks) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_stream_chunker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.chunker'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/chunker.py`:

```python
"""Hybrid chunker: raw mic audio -> 16 kHz WAV speech chunks.

Resamples device-native audio to 16 kHz, re-frames into the 512-sample blocks
``VADGate`` requires, and drives ``VADGate`` to segment speech. ``VADGate``
emits a completed utterance on a silence gap *or* once the utterance reaches
``max_utterance_ms`` — that dual trigger is exactly the hybrid silence + hard
time-cap chunking the spec calls for. Chunks shorter than ``min_chunk_seconds``
are discarded as too short for whisper.

Reuses `voice_commander.vad_gate.VADGate`, `vad_onnx.load_silero_vad`,
`resampler.Resampler` and `dictation.store.encode_wav` without forking them.
See spec `docs/superpowers/specs/2026-05-18-streaming-dictation-design.md`
section 6.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import numpy.typing as npt

from voice_commander.dictation.store import encode_wav
from voice_commander.resampler import Resampler
from voice_commander.vad_gate import VADGate
from voice_commander.vad_onnx import load_silero_vad

_SAMPLE_RATE = 16_000
_FRAME_SAMPLES = 512


class _Vad(Protocol):
    """The slice of `VADGate` the chunker depends on."""

    def process(self, frame_16k: npt.NDArray[np.float32]) -> npt.NDArray[np.float32] | None: ...


class Chunker:
    """Turn raw native-rate mic audio into 16 kHz WAV speech chunks."""

    def __init__(
        self,
        native_rate: int,
        *,
        vad_threshold: float = 0.5,
        min_silence_ms: int = 1200,
        speech_pad_ms: int = 300,
        max_chunk_seconds: int = 15,
        min_chunk_seconds: int = 1,
        vad: _Vad | None = None,
    ) -> None:
        self._resampler: Resampler | None = (
            None if native_rate == _SAMPLE_RATE
            else Resampler(src_rate=native_rate, dst_rate=_SAMPLE_RATE)
        )
        self._vad: _Vad = vad if vad is not None else VADGate(
            load_silero_vad(),
            threshold=vad_threshold,
            min_silence_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
            max_utterance_ms=max_chunk_seconds * 1000,
        )
        self._min_samples = min_chunk_seconds * _SAMPLE_RATE
        self._frame_buf: npt.NDArray[np.float32] = np.empty(0, dtype=np.float32)

    def process_raw(self, raw: npt.NDArray[np.float32]) -> list[bytes]:
        """Feed one raw mic block; return any completed WAV chunks."""
        mono = raw if raw.ndim == 1 else raw[:, 0]
        mono = np.ascontiguousarray(mono, dtype=np.float32)
        resampled = mono if self._resampler is None else self._resampler.process(mono)
        return self._consume(resampled)

    def flush(self) -> list[bytes]:
        """Drain the resampler tail at session end; return any final chunk."""
        tail = (
            np.empty(0, dtype=np.float32) if self._resampler is None
            else self._resampler.flush()
        )
        chunks = self._consume(tail)
        self._frame_buf = np.empty(0, dtype=np.float32)  # remainder < 32 ms — drop
        return chunks

    def _consume(self, samples: npt.NDArray[np.float32]) -> list[bytes]:
        self._frame_buf = np.concatenate([self._frame_buf, samples])
        chunks: list[bytes] = []
        while len(self._frame_buf) >= _FRAME_SAMPLES:
            frame = self._frame_buf[:_FRAME_SAMPLES]
            self._frame_buf = self._frame_buf[_FRAME_SAMPLES:]
            utterance = self._vad.process(frame)
            if utterance is not None and len(utterance) >= self._min_samples:
                chunks.append(encode_wav(utterance))
        return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_stream_chunker.py -v`
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation_stream/chunker.py tests/unit/test_stream_chunker.py
git commit -m "feat(dictation-stream): hybrid chunker reusing VADGate"
```

---

## Task 5: `MicCapture` — sounddevice input stream

**Files:**
- Create: `src/voice_commander/dictation_stream/capture.py`
- Test: `tests/unit/test_stream_capture.py`

**Design note:** Capture stays at the device-native rate (WASAPI shared mode locks the device to ~48 kHz; a direct 16 kHz stream is unreliable) — `Chunker` resamples downstream. Tests inject fakes for `sd.query_devices` / `sd.InputStream` so no real microphone is needed.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_stream_capture.py`:

```python
"""Unit tests for streaming dictation mic capture (sounddevice mocked)."""

from __future__ import annotations

import queue

import numpy as np

import voice_commander.dictation_stream.capture as capture_mod
from voice_commander.dictation_stream.capture import MicCapture


class _FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def _patch_sounddevice(monkeypatch, native_rate=48000):
    created = {}

    def _fake_input_stream(**kwargs):
        stream = _FakeStream(**kwargs)
        created["stream"] = stream
        return stream

    monkeypatch.setattr(
        capture_mod.sd, "query_devices", lambda kind: {"default_samplerate": float(native_rate)}
    )
    monkeypatch.setattr(capture_mod.sd, "InputStream", _fake_input_stream)
    return created


def test_start_opens_stream_at_native_rate(monkeypatch):
    created = _patch_sounddevice(monkeypatch, native_rate=44100)
    mc = MicCapture(queue.Queue())
    mc.start()
    assert mc.native_rate == 44100
    assert created["stream"].kwargs["samplerate"] == 44100
    assert created["stream"].kwargs["dtype"] == "float32"
    assert created["stream"].started is True


def test_callback_enqueues_copied_audio(monkeypatch):
    _patch_sounddevice(monkeypatch)
    q: queue.Queue = queue.Queue()
    mc = MicCapture(q)
    mc.start()
    block = np.ones((480, 1), dtype=np.float32)
    mc._on_audio(block, 480, None, None)
    got = q.get_nowait()
    assert got.shape == (480, 1)
    block[0, 0] = 99.0  # mutate original — queued copy must be unaffected
    assert got[0, 0] == 1.0


def test_stop_closes_stream_and_pushes_sentinel(monkeypatch):
    created = _patch_sounddevice(monkeypatch)
    q: queue.Queue = queue.Queue()
    mc = MicCapture(q)
    mc.start()
    mc.stop()
    assert created["stream"].closed is True
    assert q.get_nowait() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_stream_capture.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.capture'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/capture.py`:

```python
"""Microphone capture for standalone streaming dictation.

Opens a ``sounddevice`` input stream at the device-native rate and pushes raw
float32 blocks onto a queue. Mirrors the daemon's capture approach — native
rate in, resample downstream — because WASAPI shared mode locks the device to
its native rate (usually 48 kHz); a direct 16 kHz stream is unreliable.
"""

from __future__ import annotations

import logging
import queue
from typing import Any

import numpy.typing as npt
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class MicCapture:
    """Open the default input device; stream raw blocks onto ``raw_q``."""

    def __init__(self, raw_q: "queue.Queue[npt.NDArray[np.float32] | None]") -> None:
        self._raw_q = raw_q
        self._stream: Any = None
        self.native_rate: int = 0

    def start(self) -> None:
        """Open and start the input stream. Raises on device failure."""
        device_info = sd.query_devices(kind="input")
        self.native_rate = int(device_info["default_samplerate"])
        self._stream = sd.InputStream(
            samplerate=self.native_rate,
            channels=1,
            dtype="float32",
            callback=self._on_audio,
        )
        self._stream.start()
        logger.info("MicCapture: input stream open at %d Hz", self.native_rate)

    def stop(self) -> None:
        """Close the stream and push the ``None`` end-of-audio sentinel."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._raw_q.put(None)

    def _on_audio(
        self,
        indata: npt.NDArray[np.float32],
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """PortAudio callback — copy the block onto the queue, never block."""
        try:
            self._raw_q.put_nowait(indata.copy())
        except queue.Full:
            logger.warning("MicCapture: raw_q full — dropping audio block")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_stream_capture.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation_stream/capture.py tests/unit/test_stream_capture.py
git commit -m "feat(dictation-stream): MicCapture sounddevice input stream"
```

---

## Task 6: `stream_transcribe` — WebSocket client

**Files:**
- Create: `src/voice_commander/dictation_stream/ws_client.py`
- Test: `tests/integration/test_stream_ws_client.py`

**Design note:** Built against the protocol in spec section 8 and the API in `docs/references/websockets.md`. The protocol is request/reply: send one binary WAV chunk, receive one `partial`. On the `None` sentinel the client sends `{"type":"end"}` and returns. A human re-validates against the real `192.168.4.200` endpoint in Task 11.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_stream_ws_client.py`:

```python
"""Integration tests for the streaming dictation WebSocket client."""

from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from voice_commander.dictation_stream.ws_client import stream_transcribe


@pytest.fixture
async def mock_server():
    """An in-process WS server that replies one partial per binary chunk."""
    state = {"configs": [], "chunks": 0, "ended": False, "behaviour": "ok"}

    async def handler(conn):
        async for message in conn:
            if isinstance(message, str):
                data = json.loads(message)
                if data["type"] == "config":
                    state["configs"].append(data)
                elif data["type"] == "end":
                    state["ended"] = True
            else:
                state["chunks"] += 1
                if state["behaviour"] == "error":
                    await conn.send(json.dumps({"type": "error", "detail": "boom"}))
                else:
                    await conn.send(
                        json.dumps({"type": "partial", "text": f"word{state['chunks']}"})
                    )

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://localhost:{port}", state


async def test_streams_chunks_collects_partials_and_ends(mock_server):
    url, state = mock_server
    chunk_q: asyncio.Queue = asyncio.Queue()
    await chunk_q.put(b"chunk-a")
    await chunk_q.put(b"chunk-b")
    await chunk_q.put(None)
    partials: list[str] = []

    await stream_transcribe(url, "en", chunk_q, partials.append, idle_timeout_s=5.0)

    assert state["configs"] == [{"type": "config", "language": "en"}]
    assert state["chunks"] == 2
    assert state["ended"] is True
    assert partials == ["word1", "word2"]


async def test_server_error_frame_stops_streaming(mock_server):
    url, state = mock_server
    state["behaviour"] = "error"
    chunk_q: asyncio.Queue = asyncio.Queue()
    await chunk_q.put(b"chunk-a")
    await chunk_q.put(b"chunk-b")
    await chunk_q.put(None)
    partials: list[str] = []

    await stream_transcribe(url, "en", chunk_q, partials.append, idle_timeout_s=5.0)

    assert state["chunks"] == 1  # stopped after the first chunk's error reply
    assert partials == []


async def test_idle_timeout_ends_session(mock_server):
    url, state = mock_server
    chunk_q: asyncio.Queue = asyncio.Queue()  # never fed — forces the idle path
    partials: list[str] = []

    await stream_transcribe(url, "en", chunk_q, partials.append, idle_timeout_s=0.2)

    assert state["ended"] is True
    assert partials == []


async def test_connect_failure_raises_oserror():
    chunk_q: asyncio.Queue = asyncio.Queue()
    await chunk_q.put(None)
    with pytest.raises(OSError):
        await stream_transcribe(
            "ws://localhost:1", "en", chunk_q, lambda _t: None, idle_timeout_s=1.0
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_stream_ws_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.ws_client'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/ws_client.py`:

```python
"""WebSocket client for the streaming whisper transcription endpoint.

Connects to the confirmed-live ``/ws/transcribe`` endpoint, sends a one-time
JSON ``config`` handshake, streams binary WAV chunks, and routes the server's
``partial`` / ``error`` JSON replies. The protocol is request/reply: one
binary chunk out, one reply in. The server carries ``initial_prompt`` context
across chunks itself — the client sends no prompt.

See `docs/references/websockets.md` for the library API and spec section 8 for
the frame contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

# Sentinel placed on the chunk queue to signal end-of-session.
END: None = None


async def stream_transcribe(
    ws_url: str,
    language: str,
    chunk_q: "asyncio.Queue[bytes | None]",
    on_partial: Callable[[str], None],
    idle_timeout_s: float,
) -> None:
    """Stream WAV chunks to the server; route partials to ``on_partial``.

    Returns when the ``END`` sentinel is dequeued, the idle timeout elapses, or
    the server sends an ``error`` frame. Propagates ``OSError`` if the initial
    connection fails.
    """
    async with connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "config", "language": language}))
        while True:
            try:
                chunk = await asyncio.wait_for(chunk_q.get(), timeout=idle_timeout_s)
            except asyncio.TimeoutError:
                logger.info("stream_transcribe: idle %.1fs — ending session", idle_timeout_s)
                break
            if chunk is END:
                break
            await ws.send(chunk)
            try:
                reply = json.loads(await ws.recv())
            except ConnectionClosed:
                logger.warning("stream_transcribe: connection closed by server")
                return
            kind = reply.get("type")
            if kind == "partial":
                on_partial(reply.get("text", ""))
            elif kind == "error":
                logger.error("stream_transcribe: server error — %s", reply.get("detail"))
                return
            else:
                logger.warning("stream_transcribe: unexpected reply type %r", kind)
        try:
            await ws.send(json.dumps({"type": "end"}))
        except ConnectionClosed:
            logger.warning("stream_transcribe: connection closed before end frame")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_stream_ws_client.py -v`
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation_stream/ws_client.py tests/integration/test_stream_ws_client.py
git commit -m "feat(dictation-stream): WebSocket streaming transcription client"
```

---

## Task 7: `pump` — sync-to-async queue bridge

**Files:**
- Create: `src/voice_commander/dictation_stream/bridge.py`
- Test: `tests/integration/test_stream_bridge.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_stream_bridge.py`:

```python
"""Integration tests for the sync-to-async queue bridge."""

from __future__ import annotations

import asyncio
import queue

from voice_commander.dictation_stream.bridge import pump


async def test_pump_moves_items_then_forwards_sentinel():
    sync_q: queue.Queue = queue.Queue()
    async_q: asyncio.Queue = asyncio.Queue()
    sync_q.put(b"one")
    sync_q.put(b"two")
    sync_q.put(None)

    await pump(sync_q, async_q)

    assert await async_q.get() == b"one"
    assert await async_q.get() == b"two"
    assert await async_q.get() is None
    assert async_q.empty()


async def test_pump_waits_for_late_items():
    sync_q: queue.Queue = queue.Queue()
    async_q: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(pump(sync_q, async_q))

    await asyncio.sleep(0.05)  # pump is blocked waiting on an empty sync queue
    assert not task.done()

    sync_q.put(b"late")
    sync_q.put(None)
    await task
    assert await async_q.get() == b"late"
    assert await async_q.get() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_stream_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.bridge'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/bridge.py`:

```python
"""Bridge a synchronous queue to an asyncio queue.

The audio stack (sounddevice callback, chunker worker thread) is synchronous;
the WebSocket client is async. ``pump`` moves WAV chunks across the boundary
without blocking the event loop, running the blocking ``queue.Queue.get`` in
the default executor.
"""

from __future__ import annotations

import asyncio
import queue


async def pump(
    sync_q: "queue.Queue[bytes | None]",
    async_q: "asyncio.Queue[bytes | None]",
) -> None:
    """Move items from ``sync_q`` to ``async_q`` until a ``None`` is seen.

    The terminating ``None`` is forwarded onto ``async_q`` so the consumer also
    observes end-of-stream.
    """
    loop = asyncio.get_running_loop()
    while True:
        item = await loop.run_in_executor(None, sync_q.get)
        await async_q.put(item)
        if item is None:
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_stream_bridge.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation_stream/bridge.py tests/integration/test_stream_bridge.py
git commit -m "feat(dictation-stream): sync-to-async queue bridge"
```

---

## Task 8: `TextSink` + `transform` seam

**Files:**
- Create: `src/voice_commander/dictation_stream/sink.py`
- Test: `tests/unit/test_stream_sink.py`

**Design note:** `transform(text) -> text` is the seam for the future small-LLM rewrite step (spec section 3). It is the identity function now; the future change replaces only its body, leaving the signature stable.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_stream_sink.py`:

```python
"""Unit tests for the streaming dictation output sink."""

from __future__ import annotations

import voice_commander.dictation_stream.sink as sink_mod
from voice_commander.dictation_stream.sink import TextSink, transform


def test_transform_is_identity_for_now():
    assert transform("hello world") == "hello world"


def test_accumulate_builds_transcript():
    sink = TextSink()
    sink.accumulate(["the", "quick"])
    sink.accumulate(["brown", "fox"])
    assert sink.text == "the quick brown fox"


def test_flush_transforms_and_pastes(monkeypatch):
    pasted: list[str] = []
    monkeypatch.setattr(sink_mod, "paste_via_clipboard", pasted.append)
    sink = TextSink()
    sink.accumulate(["hello", "world"])
    result = sink.flush()
    assert result == "hello world"
    assert pasted == ["hello world"]


def test_flush_with_no_words_pastes_nothing(monkeypatch):
    pasted: list[str] = []
    monkeypatch.setattr(sink_mod, "paste_via_clipboard", pasted.append)
    sink = TextSink()
    assert sink.flush() == ""
    assert pasted == []


def test_flush_runs_text_through_transform(monkeypatch):
    pasted: list[str] = []
    monkeypatch.setattr(sink_mod, "paste_via_clipboard", pasted.append)
    monkeypatch.setattr(sink_mod, "transform", lambda text: text.upper())
    sink = TextSink()
    sink.accumulate(["quiet"])
    assert sink.flush() == "QUIET"
    assert pasted == ["QUIET"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_stream_sink.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.sink'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/sink.py`:

```python
"""Output sink for streaming dictation.

Accumulates the words ``LocalAgreement`` confirms during a session, then on
``flush`` joins them, runs them through ``transform`` and pastes the result at
the cursor via a clipboard round-trip (reusing the shipped
``dictation.clipboard`` helper).

``transform`` is the seam for a future small-LLM rewrite step (spec section 3);
it is the identity function for now. ``flush`` looks ``transform`` up on the
module at call time, so a replacement is picked up without touching callers.
"""

from __future__ import annotations

import logging

from voice_commander.dictation.clipboard import paste_via_clipboard

logger = logging.getLogger(__name__)


def transform(text: str) -> str:
    """Post-process the full transcript before paste. Identity for now.

    The future small-LLM rewrite step replaces this body; callers and tests
    depend only on the ``str -> str`` signature.
    """
    return text


class TextSink:
    """Collect committed words; paste the transformed transcript on flush."""

    def __init__(self) -> None:
        self._words: list[str] = []

    def accumulate(self, words: list[str]) -> None:
        """Append newly-confirmed words to the pending transcript."""
        self._words.extend(words)

    @property
    def text(self) -> str:
        """The raw accumulated transcript so far."""
        return " ".join(self._words)

    def flush(self) -> str:
        """Transform the accumulated transcript and paste it.

        Returns the pasted text (empty string if nothing was accumulated).
        """
        raw = self.text
        if not raw:
            logger.info("TextSink: nothing to paste")
            return ""
        import voice_commander.dictation_stream.sink as _self  # late bind transform
        result = _self.transform(raw)
        paste_via_clipboard(result)
        logger.info("TextSink: pasted %d chars", len(result))
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_stream_sink.py -v`
Expected: PASS — all 5 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation_stream/sink.py tests/unit/test_stream_sink.py
git commit -m "feat(dictation-stream): TextSink with transform seam for future LLM step"
```

---

## Task 9: `StreamSession` orchestrator

**Files:**
- Create: `src/voice_commander/dictation_stream/session.py`
- Test: `tests/integration/test_stream_session.py`

**Design note:** `StreamSession` owns the lifecycle. `start()` opens the mic, builds the chunker, and spawns two threads: a chunker worker (raw queue → chunk queue) and an asyncio-loop thread (bridge + `stream_transcribe`). `stop()` closes capture (which pushes the `None` sentinel), joins both threads, finalizes `LocalAgreement` and flushes the sink. `_on_partial` runs on the loop thread; `stop()` joins that thread *before* touching the agreement/sink, so there is no concurrent access. The optional `capture` and `chunker_factory` arguments exist for test injection.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_stream_session.py`:

```python
"""Integration test: full streaming dictation pipeline, mic + WS faked."""

from __future__ import annotations

import asyncio
import json
import queue

import numpy as np
import pytest
from websockets.asyncio.server import serve

import voice_commander.dictation_stream.sink as sink_mod
from voice_commander.dictation_stream.chunker import Chunker
from voice_commander.dictation_stream.config import StreamDictationConfig
from voice_commander.dictation_stream.session import StreamSession


class _FakeCapture:
    """Feeds canned raw blocks to raw_q on start, the None sentinel on stop."""

    native_rate = 16_000

    def __init__(self, raw_q: queue.Queue, blocks: list[np.ndarray]) -> None:
        self._raw_q = raw_q
        self._blocks = blocks

    def start(self) -> None:
        for block in self._blocks:
            self._raw_q.put(block)

    def stop(self) -> None:
        self._raw_q.put(None)


class _FakeVad:
    """Emits a 1 s utterance once per frame fed."""

    def process(self, frame):
        return np.ones(16_000, dtype=np.float32)


@pytest.fixture
async def mock_server():
    """WS server: replies a two-word partial per chunk so LocalAgreement runs."""
    replies = ["hello world", "world done"]
    state = {"i": 0}

    async def handler(conn):
        async for message in conn:
            if isinstance(message, (bytes, bytearray)):
                text = replies[min(state["i"], len(replies) - 1)]
                state["i"] += 1
                await conn.send(json.dumps({"type": "partial", "text": text}))

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://localhost:{port}"


async def test_full_pipeline_pastes_stabilised_transcript(mock_server, monkeypatch):
    pasted: list[str] = []
    monkeypatch.setattr(sink_mod, "paste_via_clipboard", pasted.append)

    config = StreamDictationConfig(ws_url=mock_server, idle_timeout_seconds=3)
    raw_q: queue.Queue = queue.Queue()
    # Two raw blocks of one 512-frame each -> _FakeVad emits one chunk per block.
    blocks = [np.zeros(512, dtype=np.float32), np.zeros(512, dtype=np.float32)]

    session = StreamSession(
        config,
        capture=_FakeCapture(raw_q, blocks),
        chunker_factory=lambda rate: Chunker(rate, min_chunk_seconds=1, vad=_FakeVad()),
        raw_q=raw_q,
    )
    session.start()
    # stop() blocks on thread joins — run it off the event loop so the mock
    # server keeps serving.
    pasted_text = await asyncio.get_running_loop().run_in_executor(None, session.stop)

    # commit("hello world")->[]; commit("world done")->["hello"]; finalize->["world","done"]
    assert pasted_text == "hello world done"
    assert pasted == ["hello world done"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/integration/test_stream_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.session'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/session.py`:

```python
"""Streaming dictation session orchestrator.

Wires the pieces: ``MicCapture`` (device) -> raw queue -> chunker worker thread
-> chunk queue -> asyncio bridge -> ``stream_transcribe`` -> ``LocalAgreement``
-> ``TextSink``. One ``StreamSession`` is one dictation: ``start`` opens the
mic and spawns the worker + event-loop threads, ``stop`` tears them down,
finalizes ``LocalAgreement`` and pastes the transcript.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Callable

from .bridge import pump
from .capture import MicCapture
from .chunker import Chunker
from .config import StreamDictationConfig
from .local_agreement import LocalAgreement
from .sink import TextSink
from .ws_client import stream_transcribe

logger = logging.getLogger(__name__)


class StreamSession:
    """One streaming-dictation session."""

    def __init__(
        self,
        config: StreamDictationConfig,
        *,
        capture: object | None = None,
        chunker_factory: Callable[[int], Chunker] | None = None,
        raw_q: "queue.Queue | None" = None,
    ) -> None:
        self._config = config
        self._raw_q: queue.Queue = raw_q if raw_q is not None else queue.Queue(maxsize=256)
        self._chunk_q: queue.Queue = queue.Queue()
        self._capture = capture if capture is not None else MicCapture(self._raw_q)
        self._chunker_factory = chunker_factory or self._default_chunker
        self._agreement = LocalAgreement()
        self._sink = TextSink()
        self._worker: threading.Thread | None = None
        self._loop_thread: threading.Thread | None = None
        self._running = False

    def _default_chunker(self, native_rate: int) -> Chunker:
        return Chunker(
            native_rate,
            vad_threshold=self._config.vad_threshold,
            min_silence_ms=self._config.min_silence_duration_ms,
            speech_pad_ms=self._config.speech_pad_ms,
            max_chunk_seconds=self._config.max_chunk_seconds,
            min_chunk_seconds=self._config.min_chunk_seconds,
        )

    def start(self) -> None:
        """Open the mic and start the chunker + event-loop threads."""
        if self._running:
            return
        self._capture.start()
        chunker = self._chunker_factory(self._capture.native_rate)
        self._running = True
        self._worker = threading.Thread(
            target=self._chunker_loop, args=(chunker,),
            name="dict-stream-chunker", daemon=True,
        )
        self._worker.start()
        self._loop_thread = threading.Thread(
            target=self._run_asyncio, name="dict-stream-loop", daemon=True,
        )
        self._loop_thread.start()
        logger.info("StreamSession: started")

    def stop(self) -> str:
        """Stop capture, drain, finalize LocalAgreement, paste. Returns text."""
        if not self._running:
            return ""
        self._running = False
        self._capture.stop()  # pushes the None sentinel onto raw_q
        if self._worker is not None:
            self._worker.join(timeout=10.0)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=self._config.idle_timeout_seconds + 10.0)
        self._sink.accumulate(self._agreement.finalize())
        pasted = self._sink.flush()
        logger.info("StreamSession: stopped")
        return pasted

    def _chunker_loop(self, chunker: Chunker) -> None:
        """Drain raw audio, chunk it, feed chunk_q. Ends on the None sentinel."""
        while True:
            raw = self._raw_q.get()
            if raw is None:
                for chunk in chunker.flush():
                    self._chunk_q.put(chunk)
                self._chunk_q.put(None)
                return
            for chunk in chunker.process_raw(raw):
                self._chunk_q.put(chunk)

    def _run_asyncio(self) -> None:
        try:
            asyncio.run(self._async_main())
        except OSError as exc:
            logger.error("StreamSession: WebSocket connection failed — %s", exc)
        except Exception:  # noqa: BLE001 - surface any pipeline failure
            logger.exception("StreamSession: pipeline error")

    async def _async_main(self) -> None:
        async_q: asyncio.Queue = asyncio.Queue()
        bridge_task = asyncio.create_task(pump(self._chunk_q, async_q))
        try:
            await stream_transcribe(
                self._config.ws_url,
                self._config.language,
                async_q,
                self._on_partial,
                float(self._config.idle_timeout_seconds),
            )
        finally:
            bridge_task.cancel()

    def _on_partial(self, text: str) -> None:
        """Runs on the loop thread; stop() joins that thread before reading."""
        self._sink.accumulate(self._agreement.commit(text))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/integration/test_stream_session.py -v`
Expected: PASS — the pipeline pastes `"hello world done"`.

- [ ] **Step 5: Run the full streaming test suite**

Run: `pytest tests/unit/test_stream_*.py tests/integration/test_stream_*.py -v`
Expected: PASS — every streaming-dictation test green.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/dictation_stream/session.py tests/integration/test_stream_session.py
git commit -m "feat(dictation-stream): StreamSession pipeline orchestrator"
```

---

## Task 10: `__main__` entrypoint + `SessionToggle`

**Files:**
- Create: `src/voice_commander/dictation_stream/__main__.py`
- Test: `tests/unit/test_stream_toggle.py`

**Design note:** The Right Ctrl press logic is extracted into a `SessionToggle` class so it is unit-testable with a fake session and fake clock — the `pynput` listener wiring in `main()` stays a thin shell. `SessionToggle` carries the same 50 ms debounce the shipped daemon uses to drop driver double-release events.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_stream_toggle.py`:

```python
"""Unit tests for the Right Ctrl session toggle."""

from __future__ import annotations

from voice_commander.dictation_stream.__main__ import SessionToggle


class _FakeSession:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> str:
        self.stopped = True
        return "pasted text"


def test_first_press_starts_a_session():
    sessions: list[_FakeSession] = []

    def make() -> _FakeSession:
        s = _FakeSession()
        sessions.append(s)
        return s

    clock = iter([10.0, 20.0])
    toggle = SessionToggle(make, clock=lambda: next(clock))

    assert toggle.fire() is None
    assert len(sessions) == 1 and sessions[0].started is True


def test_second_press_stops_and_returns_text():
    session = _FakeSession()
    clock = iter([10.0, 20.0])
    toggle = SessionToggle(lambda: session, clock=lambda: next(clock))

    toggle.fire()              # start
    result = toggle.fire()     # stop
    assert result == "pasted text"
    assert session.stopped is True


def test_debounce_drops_rapid_second_press():
    sessions: list[_FakeSession] = []
    clock = iter([10.0, 10.02])  # 20 ms apart — inside the 50 ms debounce

    def make() -> _FakeSession:
        s = _FakeSession()
        sessions.append(s)
        return s

    toggle = SessionToggle(make, debounce_s=0.05, clock=lambda: next(clock))
    toggle.fire()              # start
    assert toggle.fire() is None  # debounced — ignored
    assert len(sessions) == 1
    assert sessions[0].stopped is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_stream_toggle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.dictation_stream.__main__'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice_commander/dictation_stream/__main__.py`:

```python
"""Standalone entrypoint for the streaming dictation experiment.

    python -m voice_commander.dictation_stream

Press Right Ctrl to start a dictation session; press it again to end — the
transcript is pasted at the cursor. Ctrl-C exits.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from .config import load as load_config
from .session import StreamSession

logger = logging.getLogger(__name__)

_DEBOUNCE_S = 0.05


class SessionToggle:
    """Debounced start/stop toggle for streaming-dictation sessions.

    ``fire`` is called once per Right Ctrl press. The first press starts a
    session; the next press stops it and returns the pasted transcript.
    Presses within ``debounce_s`` of the previous one are dropped (driver
    double-release events).
    """

    def __init__(
        self,
        make_session: Callable[[], object],
        *,
        debounce_s: float = _DEBOUNCE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._make_session = make_session
        self._debounce_s = debounce_s
        self._clock = clock
        self._last = float("-inf")
        self._session: object | None = None

    def fire(self) -> str | None:
        """Handle one hotkey press. Returns pasted text on stop, else None."""
        now = self._clock()
        if now - self._last < self._debounce_s:
            return None
        self._last = now
        if self._session is None:
            self._session = self._make_session()
            self._session.start()  # type: ignore[attr-defined]
            return None
        session, self._session = self._session, None
        return session.stop()  # type: ignore[attr-defined]


def main() -> None:
    """Bind Right Ctrl and toggle streaming-dictation sessions until Ctrl-C."""
    from pynput import keyboard

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(Path("config.toml"))
    logger.info(
        "Streaming dictation ready — endpoint %s. Right Ctrl to dictate, Ctrl-C to quit.",
        config.ws_url,
    )

    toggle = SessionToggle(lambda: StreamSession(config))

    def on_press(key: object) -> None:
        if key is keyboard.Key.ctrl_r:
            toggle.fire()

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        listener.join()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        listener.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_stream_toggle.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation_stream/__main__.py tests/unit/test_stream_toggle.py
git commit -m "feat(dictation-stream): Right Ctrl entrypoint with SessionToggle"
```

---

## Task 11: End-to-end harness

**Files:**
- Create: `scripts/dictation_stream_e2e.py`

**Design note:** Per `docs/agents/visual-e2e-testing.md`, a user-visible feature ships with a harness that drives it like a real user and asserts on captured evidence. This harness runs the real pipeline against the real microphone and the real `192.168.4.200:8765/ws/transcribe` server, then asserts the transcript landed on the clipboard. It is **human-validated** — the operator speaks a scripted paragraph.

- [ ] **Step 1: Write the harness**

Create `scripts/dictation_stream_e2e.py`:

```python
"""End-to-end harness for streaming dictation.

Drives the real pipeline: real microphone, real WebSocket server, real
clipboard. The operator speaks a scripted paragraph; the harness captures the
clipboard contents and prints a PASS/FAIL verdict.

Usage:
    python scripts/dictation_stream_e2e.py

Protocol: docs/agents/visual-e2e-testing.md
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from voice_commander.dictation.clipboard import read_clipboard_text
from voice_commander.dictation_stream.config import load as load_config
from voice_commander.dictation_stream.session import StreamSession

SCRIPT = (
    "The quick brown fox jumps over the lazy dog while the streaming "
    "dictation pipeline transcribes every word."
)


def main() -> int:
    config = load_config(Path("config.toml"))
    print(f"Endpoint: {config.ws_url}")
    print("=" * 70)
    print("Read this sentence aloud, clearly, at a normal pace:")
    print()
    print(f"  {SCRIPT}")
    print()
    input("Press Enter, then immediately start speaking...")

    session = StreamSession(config)
    session.start()
    print("Recording — speak now. Press Enter when finished.")
    input()
    pasted = session.stop()

    time.sleep(0.3)  # let the clipboard paste settle
    clipboard = read_clipboard_text() or ""

    print("=" * 70)
    print(f"Pipeline returned : {pasted!r}")
    print(f"Clipboard contents: {clipboard!r}")
    print()

    ok = bool(pasted.strip()) and pasted.strip() in clipboard
    if ok:
        print("PASS — transcript was produced and landed on the clipboard.")
        print("Human check: does the transcript match what you said?")
        return 0
    print("FAIL — no transcript, or it did not reach the clipboard.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the harness (human-validated)**

Run: `python scripts/dictation_stream_e2e.py`
Expected: the harness records, streams to the server, and prints `PASS` with a transcript matching the spoken sentence. The operator confirms the transcript is correct.

- [ ] **Step 3: Commit**

```bash
git add scripts/dictation_stream_e2e.py
git commit -m "test(dictation-stream): end-to-end harness — real mic, server, clipboard"
```

---

## Task 12: Documentation

**Files:**
- Create: `docs/decisions/0091-streaming-dictation-experiment.md`
- Create: `docs/dictation-streaming.md`
- Modify: `docs/agents/technical-decisions.md`
- Modify: `docs/libraries.md`
- Modify: `docs/index.md`

- [ ] **Step 1: Write ADR 0091**

Create `docs/decisions/0091-streaming-dictation-experiment.md`:

```markdown
# ADR 0091 — Streaming Dictation Experiment (WebSocket + LocalAgreement)

**Status:** Experimental
**Date:** 2026-05-18
**Extends:** [ADR 0086](0086-dictation-remote-transcription.md) — an alternative transcription path, not yet wired into the daemon

## Context

Shipped dictation (ADR 0086, amended 0090) is batch: VAD buffers the whole
utterance, the daemon POSTs one WAV to the remote whisper.cpp `/inference`
endpoint, and pastes the result once. Latency scales with utterance length and
there is no cross-chunk decoding context.

This ADR records an isolated experiment in streaming transcription. It is not
wired into the daemon; it runs standalone. If it proves out, a later, separate
ADR will cover replacing the batch path.

## Decision

### D1 — Isolated `dictation_stream` package

A new self-contained package `src/voice_commander/dictation_stream/` holds the
experiment. It is run standalone via `python -m voice_commander.dictation_stream`.
The shipped `voice_commander.dictation` package and `daemon.py` are not edited.

### D2 — Hybrid chunking reuses `VADGate`

The chunker drives the existing `VADGate` with dictation-tuned parameters
(`threshold=0.5`, `min_silence_ms=1200`, `speech_pad_ms=300`,
`max_utterance_ms=15000`). `VADGate` already emits one utterance per speech-end
and force-caps at `max_utterance_ms` — that dual trigger is the hybrid silence
+ hard-time-cap chunking. Chunks under 1 s are discarded.

### D3 — WebSocket streaming with server-side prompt carry

Each WAV chunk streams to the confirmed-live `ws://192.168.4.200:8765/ws/transcribe`
endpoint. The protocol: a one-time `{"type":"config"}` handshake, binary WAV
frames, `{"type":"partial"}` / `{"type":"error"}` replies, `{"type":"end"}` on
stop. The server carries `initial_prompt` context across chunks.

### D4 — LocalAgreement word stabilisation

Chunk boundaries land mid-sentence, so each chunk's suffix is unstable.
`LocalAgreement` holds back a chunk's tail and commits a word only when the
next chunk confirms it by overlap. `finalize()` flushes the held-back tail at
session end.

### D5 — Accumulate-then-paste with a `transform` seam

Confirmed words accumulate; at session end the full transcript passes through
`transform(text) -> text` (identity now) and is pasted via a clipboard
round-trip. `transform` is the seam for a future small-LLM rewrite step.

### D6 — Right Ctrl toggle, 50 ms debounce

The standalone runner binds Right Ctrl (matching the shipped `dictation_key`).
A first press starts a session, the next stops it. A 50 ms debounce drops
driver double-release events, as in the shipped daemon.

## Consequences

### Positive

- Streaming transcription with progressive, context-carrying decoding.
- Zero risk to shipped dictation — the experiment is fully isolated.
- The chunker reuses proven, torch-free ONNX VAD code.

### Negative

- A second dictation code path exists until the experiment is resolved.
- Adds a `websockets` dependency.

### Neutral

- The experiment is standalone; integration into the daemon is future work.
- `transform` is identity until the LLM rewrite step lands.

## References

- [ADR 0086](0086-dictation-remote-transcription.md) — shipped batch dictation
- `docs/superpowers/specs/2026-05-18-streaming-dictation-design.md` — design spec
- `docs/superpowers/plans/2026-05-18-streaming-dictation.md` — implementation plan
- `docs/dictation-streaming.md` — subsystem overview
- `src/voice_commander/dictation_stream/` — implementation
- `docs/references/websockets.md` — WebSocket library reference
```

- [ ] **Step 2: Write the subsystem overview doc**

Create `docs/dictation-streaming.md`:

```markdown
# Streaming Dictation (Experimental)

An isolated experiment in streaming dictation, separate from the shipped batch
dictation (`docs/transcription-pipeline.md`). Run standalone:

    python -m voice_commander.dictation_stream

Press Right Ctrl to start dictating, press it again to finish. The transcript
is pasted at the cursor.

## Flow

```
mic -> MicCapture -> raw_q -> Chunker (VADGate: silence | 15s cap)
    -> chunk_q -> bridge.pump -> stream_transcribe (WebSocket)
    -> partials -> LocalAgreement.commit -> TextSink.accumulate
    -> stop: LocalAgreement.finalize -> transform() -> clipboard paste
```

## Modules — `src/voice_commander/dictation_stream/`

| Module | Responsibility |
|--------|----------------|
| `config.py` | `StreamDictationConfig` + `[dictation_stream]` loader |
| `capture.py` | `MicCapture` — sounddevice input stream |
| `chunker.py` | `Chunker` — raw audio → WAV speech chunks (reuses `VADGate`) |
| `ws_client.py` | `stream_transcribe` — WebSocket client |
| `bridge.py` | `pump` — sync→async queue bridge |
| `local_agreement.py` | `LocalAgreement` — word stabiliser |
| `sink.py` | `TextSink` + `transform` seam |
| `session.py` | `StreamSession` — pipeline orchestrator |
| `__main__.py` | Right Ctrl entrypoint |

## Status

Experimental — not wired into the daemon. See ADR 0091. If it proves out, a
later decision covers replacing the batch transcription path.
```

- [ ] **Step 3: Add the `technical-decisions.md` row**

In `docs/agents/technical-decisions.md`, append a row to the decisions table:

```markdown
| Streaming dictation (experimental) | Isolated `dictation_stream` package: hybrid chunking via reused `VADGate`, WebSocket streaming to `/ws/transcribe`, `LocalAgreement` word stabilisation, accumulate-then-paste with a `transform()` seam for a future LLM step. Standalone (`python -m voice_commander.dictation_stream`), Right Ctrl toggle. Not wired into the daemon. | Streaming transcription with progressive, context-carrying decoding; zero risk to shipped batch dictation | [0091](../decisions/0091-streaming-dictation-experiment.md) |
```

- [ ] **Step 4: Add the `libraries.md` row**

In `docs/libraries.md`, add an entry for `websockets`:

```markdown
- **`websockets>=14.0`** — async WebSocket client for the streaming dictation
  experiment (`dictation_stream/ws_client.py`). Uses the modern
  `websockets.asyncio` API. See `docs/references/websockets.md`.
```

- [ ] **Step 5: Link the overview doc from `docs/index.md`**

In `docs/index.md`, under "Quick links", add:

```markdown
- **Streaming dictation (experimental):** [`dictation-streaming.md`](dictation-streaming.md) — WebSocket + LocalAgreement experiment, ADR [`decisions/0091-streaming-dictation-experiment.md`](decisions/0091-streaming-dictation-experiment.md)
```

- [ ] **Step 6: Run the full streaming suite once more**

Run: `pytest tests/unit/test_stream_*.py tests/integration/test_stream_*.py -v`
Expected: PASS — every streaming-dictation test green.

- [ ] **Step 7: Commit**

```bash
git add docs/decisions/0091-streaming-dictation-experiment.md docs/dictation-streaming.md docs/agents/technical-decisions.md docs/libraries.md docs/index.md
git commit -m "docs(dictation-stream): ADR 0091, overview, library + decision rows"
```

---

## Done criteria

- [ ] All `tests/unit/test_stream_*.py` and `tests/integration/test_stream_*.py` pass.
- [ ] `python -m voice_commander.dictation_stream` runs; Right Ctrl starts/stops a session.
- [ ] `scripts/dictation_stream_e2e.py` produces a correct transcript on the clipboard (human-validated).
- [ ] Shipped dictation (`src/voice_commander/dictation/`, `daemon.py`) is byte-for-byte unchanged — verify with `git diff main -- src/voice_commander/dictation/ src/voice_commander/daemon.py` showing no output.
- [ ] ADR 0091, the overview doc, and the `technical-decisions.md` / `libraries.md` / `index.md` rows are committed.
