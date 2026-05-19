# Streaming Dictation Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the daemon's batch dictation transcription (buffer whole utterance → HTTP POST to whisper.cpp `/inference` → paste) with a streaming WebSocket pipeline that streams each VAD utterance as the user speaks.

**Architecture:** Approach A from the spec — keep the daemon's battle-tested dictation lifecycle wiring intact; rework `DictationSession` internals so each VAD utterance is streamed immediately instead of buffered. The reusable WebSocket transport from the ADR 0091 experiment (`ws_client`, `local_agreement`, `bridge`) is moved into the shipped `dictation/` package; the mic-owning experiment code is deleted. `DictationSession` owns an asyncio-loop thread running `bridge.pump` + `stream_transcribe` + a lock-guarded `LocalAgreement` (the ADR 0091 Task-9 threading pattern).

**Tech Stack:** Python 3.14, `websockets>=14.0` (asyncio client + server), `numpy`, faster-whisper (command mode, untouched), pytest + pytest-asyncio (`asyncio_mode = "auto"`), FastAPI/Jinja2 (web UI). Windows-only. Bare `python` = `C:\Python314\python.exe`.

---

## Environment & conventions

- Working directory: `F:\Tools\Projects\voice-commander`. Branch `experiment/streaming-dictation` is already checked out — do **not** create a new branch.
- Shell is PowerShell. `python` resolves to `C:\Python314\python.exe` (the env with all deps + pytest).
- Run tests with `python -m pytest`. The repo sets `asyncio_mode = "auto"` in `pyproject.toml`, so `async def test_*` functions run without an `@pytest.mark.asyncio` decorator.
- Commit after every task. Commit messages use the conventional-commit prefixes shown in each task. Every commit message ends with the two trailer lines:
  ```
  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  ```
- After moving/deleting a `.py` file, also delete its stale `__pycache__/*.pyc` siblings so import errors are not masked by cached bytecode. Each task's commands include this where relevant.
- The new ADR number is **0092** (current highest is `0091-streaming-dictation-experiment.md`).

## File structure — what each task touches

**Moved into `src/voice_commander/dictation/`** (Phase 1):
- `dictation_stream/ws_client.py` → `dictation/ws_client.py` — WebSocket transport.
- `dictation_stream/local_agreement.py` → `dictation/local_agreement.py` — word stabiliser.
- `dictation_stream/bridge.py` → `dictation/bridge.py` — sync→async queue pump.

**Reworked** (Phase 3): `src/voice_commander/dictation/session.py` — `DictationSession` becomes a streaming session that owns the WebSocket transport.

**Modified** (Phases 4–6): `src/voice_commander/daemon.py` (`_finalize_dictation`, constructor params, `_finalize_pending_dictation_end`), `src/voice_commander/config.py` (`DictationConfig`), `config.toml.example`, `src/voice_commander/web/app.py` (drop retranscribe route), `src/voice_commander/web/templates/page_dictation.html`, `src/voice_commander/dictation/store.py` (drop audio methods).

**Deleted** (Phase 7): `dictation_stream/__init__.py`, `capture.py`, `chunker.py`, `config.py`, `session.py`, `sink.py`, `__main__.py`; `dictation/remote.py`; `pyproject.toml` `voice-dictation-stream` script entry; `scripts/dictation_stream_e2e.py`; `scripts/dictation_remote_smoke.py`; tests `test_stream_capture/chunker/config/sink/toggle/session`, `test_dictation_remote`, `test_dictation_remote_timeout`; templates `_dictation_result.html`.

**Created**: `docs/decisions/0092-streaming-dictation-integration.md`, `tests/integration/test_dictation_streaming_session.py`, `scripts/dictation_streaming_e2e.py`.

---

## Phase 1 — Move the reusable transport into `dictation/`

Goal: relocate the three reusable transport modules out of the experiment package into the shipped `dictation/` package, fixing every import. After this phase the moved modules' own tests are green; nothing else has changed behaviour.

### Task 1: Move `local_agreement.py` into `dictation/`

**Files:**
- Move: `src/voice_commander/dictation_stream/local_agreement.py` → `src/voice_commander/dictation/local_agreement.py`
- Move: `tests/unit/test_stream_local_agreement.py` → `tests/unit/test_dictation_local_agreement.py`

- [ ] **Step 1: Move the module file**

```powershell
git mv src/voice_commander/dictation_stream/local_agreement.py src/voice_commander/dictation/local_agreement.py
```

- [ ] **Step 2: Fix the module docstring reference**

Edit `src/voice_commander/dictation/local_agreement.py` — the docstring's final paragraph currently reads:

```python
Pure module — no I/O, no threads. See spec
`docs/superpowers/specs/2026-05-18-streaming-dictation-design.md` section 7.
```

Replace with:

```python
Pure module — no I/O, no threads. See ADR 0092
(`docs/decisions/0092-streaming-dictation-integration.md`).
```

No code changes — `LocalAgreement` has no internal imports.

- [ ] **Step 3: Move the test file**

```powershell
git mv tests/unit/test_stream_local_agreement.py tests/unit/test_dictation_local_agreement.py
```

- [ ] **Step 4: Fix the test import**

Edit `tests/unit/test_dictation_local_agreement.py` — change the import line:

```python
from voice_commander.dictation_stream.local_agreement import LocalAgreement
```

to:

```python
from voice_commander.dictation.local_agreement import LocalAgreement
```

- [ ] **Step 5: Run the moved test**

Run: `python -m pytest tests/unit/test_dictation_local_agreement.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "refactor: move local_agreement into dictation package

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 2: Move `bridge.py` into `dictation/`

**Files:**
- Move: `src/voice_commander/dictation_stream/bridge.py` → `src/voice_commander/dictation/bridge.py`
- Move: `tests/integration/test_stream_bridge.py` → `tests/integration/test_dictation_bridge.py`

- [ ] **Step 1: Move the module file**

```powershell
git mv src/voice_commander/dictation_stream/bridge.py src/voice_commander/dictation/bridge.py
```

- [ ] **Step 2: Fix the module docstring reference**

Edit `src/voice_commander/dictation/bridge.py` — the docstring mentions `StreamSession` (which is being deleted). Replace the `Cancellation:` paragraph in `pump`'s docstring:

```python
    Cancellation: if this coroutine is cancelled while blocked in
    ``run_in_executor``, the underlying ``sync_q.get`` call cannot be
    interrupted — the executor thread keeps blocking until a ``None`` or any
    item is placed on ``sync_q``. Callers are responsible for ensuring a
    sentinel arrives promptly after cancellation (``StreamSession`` guarantees
    this via ``MicCapture.stop``). ``async_q`` must be unbounded (the default
    ``maxsize=0``) so ``async_q.put`` never blocks the pump.
```

with:

```python
    Cancellation: if this coroutine is cancelled while blocked in
    ``run_in_executor``, the underlying ``sync_q.get`` call cannot be
    interrupted — the executor thread keeps blocking until a ``None`` or any
    item is placed on ``sync_q``. Callers are responsible for ensuring a
    sentinel arrives promptly after cancellation (``DictationSession.finish``
    and ``DictationSession.cancel`` guarantee this by pushing the ``None``
    end sentinel onto the chunk queue). ``async_q`` must be unbounded (the
    default ``maxsize=0``) so ``async_q.put`` never blocks the pump.
```

Also fix the module-level docstring's first paragraph — it references the chunker worker thread:

```python
"""Bridge a synchronous queue to an asyncio queue.

The audio stack (sounddevice callback, chunker worker thread) is synchronous;
the WebSocket client is async. ``pump`` moves WAV chunks across the boundary
without blocking the event loop, running the blocking ``queue.Queue.get`` in
the default executor.
"""
```

Replace with:

```python
"""Bridge a synchronous queue to an asyncio queue.

The daemon's dictation pipeline pushes WAV chunks from synchronous threads
(the VAD pipeline worker, via ``DictationSession.handle_utterance``); the
WebSocket client is async. ``pump`` moves WAV chunks across the boundary
without blocking the event loop, running the blocking ``queue.Queue.get`` in
the default executor.
"""
```

- [ ] **Step 3: Move the test file**

```powershell
git mv tests/integration/test_stream_bridge.py tests/integration/test_dictation_bridge.py
```

- [ ] **Step 4: Fix the test import**

Edit `tests/integration/test_dictation_bridge.py` — change:

```python
from voice_commander.dictation_stream.bridge import pump
```

to:

```python
from voice_commander.dictation.bridge import pump
```

- [ ] **Step 5: Run the moved test**

Run: `python -m pytest tests/integration/test_dictation_bridge.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "refactor: move bridge into dictation package

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 3: Move `ws_client.py` into `dictation/` and add the `prompt` config field

**Files:**
- Move: `src/voice_commander/dictation_stream/ws_client.py` → `src/voice_commander/dictation/ws_client.py`
- Move: `tests/integration/test_stream_ws_client.py` → `tests/integration/test_dictation_ws_client.py`

This task moves the file **and** adds a `prompt` parameter to `stream_transcribe` so the config frame can carry the vocabulary prompt. The exact server field name (`prompt` vs `initial_prompt`) is **provisionally `initial_prompt`** here and **confirmed in Phase 2** — if Phase 2 finds a different name, Phase 2's task corrects it. Using `initial_prompt` as the provisional name because the existing `ws_client.py` docstring already says "The server carries `initial_prompt` context across chunks".

- [ ] **Step 1: Move the module file**

```powershell
git mv src/voice_commander/dictation_stream/ws_client.py src/voice_commander/dictation/ws_client.py
```

- [ ] **Step 2: Move the test file**

```powershell
git mv tests/integration/test_stream_ws_client.py tests/integration/test_dictation_ws_client.py
```

- [ ] **Step 3: Update the failing test first (TDD) — assert the prompt is sent**

Edit `tests/integration/test_dictation_ws_client.py`. Change the import:

```python
from voice_commander.dictation_stream.ws_client import stream_transcribe
```

to:

```python
from voice_commander.dictation.ws_client import stream_transcribe
```

Then update `test_streams_chunks_collects_partials_and_ends` to pass and assert a prompt. Replace the test body:

```python
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
```

with:

```python
async def test_streams_chunks_collects_partials_and_ends(mock_server):
    url, state = mock_server
    chunk_q: asyncio.Queue = asyncio.Queue()
    await chunk_q.put(b"chunk-a")
    await chunk_q.put(b"chunk-b")
    await chunk_q.put(None)
    partials: list[str] = []

    await stream_transcribe(
        url, "en", chunk_q, partials.append, idle_timeout_s=5.0, prompt="Supabase"
    )

    assert state["configs"] == [
        {"type": "config", "language": "en", "initial_prompt": "Supabase"}
    ]
    assert state["chunks"] == 2
    assert state["ended"] is True
    assert partials == ["word1", "word2"]


async def test_empty_prompt_omits_field(mock_server):
    url, state = mock_server
    chunk_q: asyncio.Queue = asyncio.Queue()
    await chunk_q.put(None)
    await stream_transcribe(url, "en", chunk_q, lambda _t: None, idle_timeout_s=5.0)
    assert state["configs"] == [{"type": "config", "language": "en"}]
```

Update the three remaining tests (`test_server_error_frame_stops_streaming`, `test_idle_timeout_ends_session`, `test_connect_failure_raises_oserror`) — they call `stream_transcribe` with positional args only, so they already work with the new optional keyword param. No change needed for those three.

- [ ] **Step 4: Run the test — verify it fails**

Run: `python -m pytest tests/integration/test_dictation_ws_client.py::test_streams_chunks_collects_partials_and_ends -v`
Expected: FAIL — the assertion on `state["configs"]` fails because `stream_transcribe` does not yet send `initial_prompt` and does not accept a `prompt` argument (TypeError).

- [ ] **Step 5: Add the `prompt` parameter to `stream_transcribe`**

Edit `src/voice_commander/dictation/ws_client.py`. Update the module docstring's third sentence:

```python
``partial`` / ``error`` JSON replies. The protocol is request/reply: one
binary chunk out, one reply in. The server carries ``initial_prompt`` context
across chunks itself — the client sends no prompt.
```

Replace with:

```python
``partial`` / ``error`` JSON replies. The protocol is request/reply: one
binary chunk out, one reply in. The config handshake carries an optional
``initial_prompt`` field that biases the whisper decoder toward the user's
custom vocabulary (built by ``postprocess.build_prompt``).
```

Update the `stream_transcribe` signature and the config-frame send. The current signature:

```python
async def stream_transcribe(
    ws_url: str,
    language: str,
    chunk_q: "asyncio.Queue[bytes | None]",
    on_partial: Callable[[str], None],
    idle_timeout_s: float,
) -> None:
```

Replace with:

```python
async def stream_transcribe(
    ws_url: str,
    language: str,
    chunk_q: "asyncio.Queue[bytes | None]",
    on_partial: Callable[[str], None],
    idle_timeout_s: float,
    prompt: str = "",
) -> None:
```

Add to the docstring's parameter description (after the existing prose, before the body):

```python
    ``prompt`` (when non-empty) is sent in the config frame as the
    ``initial_prompt`` field so the server biases its decoder toward the
    user's custom vocabulary. An empty ``prompt`` omits the field entirely.
```

Then change the config-frame send line:

```python
        await ws.send(json.dumps({"type": "config", "language": language}))
```

to:

```python
        config: dict[str, str] = {"type": "config", "language": language}
        if prompt:
            config["initial_prompt"] = prompt
        await ws.send(json.dumps(config))
```

- [ ] **Step 6: Run the test — verify it passes**

Run: `python -m pytest tests/integration/test_dictation_ws_client.py -v`
Expected: all tests PASS (including the new `test_empty_prompt_omits_field`).

- [ ] **Step 7: Commit**

```powershell
git add -A
git commit -m "refactor: move ws_client into dictation package, add prompt config field

Adds an optional prompt parameter to stream_transcribe; when non-empty it is
sent in the WebSocket config handshake as the initial_prompt field. The exact
field name is confirmed against the live server in a follow-up task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 4: Update the `dictation/__init__.py` docstring

**Files:**
- Modify: `src/voice_commander/dictation/__init__.py`

The `__init__.py` lists sub-modules; it now mentions `remote` (being deleted) and not the three new transport modules. Update it now so the package docstring is honest at the end of Phase 1 (`remote` still exists until Phase 7, but the docstring should describe the target layout — this is acceptable because the deletion is already committed in this plan; alternatively defer this edit to Task 18. To keep Phase 1 self-contained, update it now to describe both transitional states minimally).

- [ ] **Step 1: Rewrite the `__init__.py` docstring**

Replace the entire contents of `src/voice_commander/dictation/__init__.py` with:

```python
"""Dictation mode — streaming WebSocket transcription pipeline (ADR 0092).

Sub-modules:

* :mod:`session`        — ``DictationSession`` state machine; owns the
                          WebSocket transport for one dictation.
* :mod:`ws_client`      — async WebSocket client for the ``/ws/transcribe``
                          streaming endpoint.
* :mod:`local_agreement` — ``LocalAgreement`` word stabiliser (commits words
                          confirmed stable across chunk boundaries).
* :mod:`bridge`         — sync-queue → asyncio-queue pump.
* :mod:`store`          — WAV chunk encoding and single-slot text persistence.
* :mod:`clipboard`      — clipboard snapshot/paste/restore for result delivery.
* :mod:`postprocess`    — prompt building + corrections/commands text passes.
* :mod:`vocab`          — ``Vocabulary``/``Correction``/``Command`` dataclasses
                          and ``VocabStore`` persistence for ``vocab.json``.
"""
```

- [ ] **Step 2: Verify the package still imports**

Run: `python -c "import voice_commander.dictation; import voice_commander.dictation.ws_client; import voice_commander.dictation.bridge; import voice_commander.dictation.local_agreement; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Run the full transport test set + delete stale pycache**

```powershell
Remove-Item -Recurse -Force src/voice_commander/dictation_stream/__pycache__ -ErrorAction SilentlyContinue
python -m pytest tests/unit/test_dictation_local_agreement.py tests/integration/test_dictation_bridge.py tests/integration/test_dictation_ws_client.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```powershell
git add -A
git commit -m "docs: update dictation package docstring for streaming transport

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Verify the live server's prompt field name

Goal: confirm against the real `ws://192.168.4.200:8765/ws/transcribe` server that the config-handshake initial-prompt field is named `initial_prompt` (the provisional name used in Phase 1). This is a **verification-and-correction** task — it produces no production code unless the field name is wrong.

### Task 5: Confirm the WS server config-frame prompt field name

**Files:**
- (Conditionally) Modify: `src/voice_commander/dictation/ws_client.py`
- (Conditionally) Modify: `tests/integration/test_dictation_ws_client.py`

- [ ] **Step 1: Probe the live server**

Run the following one-off probe (requires the server at `192.168.4.200:8765` to be reachable on the dev LAN). Create a temp script `scripts/_probe_ws_prompt.py` with:

```python
"""One-off probe: confirm the /ws/transcribe config-handshake prompt field name.

Sends a config frame with BOTH candidate field names and a tiny silent WAV
chunk, then prints whatever the server replies. Run manually; not a test.
"""
from __future__ import annotations

import asyncio
import io
import json
import wave

import numpy as np
from websockets.asyncio.client import connect

WS_URL = "ws://192.168.4.200:8765/ws/transcribe"


def _silent_wav() -> bytes:
    pcm = np.zeros(16000, dtype="<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


async def main() -> None:
    for field in ("initial_prompt", "prompt"):
        print(f"--- trying config field {field!r} ---")
        try:
            async with connect(WS_URL) as ws:
                await ws.send(
                    json.dumps({"type": "config", "language": "en", field: "Supabase"})
                )
                await ws.send(_silent_wav())
                reply = await asyncio.wait_for(ws.recv(), timeout=10.0)
                print("  reply:", reply)
                await ws.send(json.dumps({"type": "end"}))
        except Exception as exc:  # noqa: BLE001
            print("  error:", exc)


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `python scripts/_probe_ws_prompt.py`

Inspect the output. The server should accept the config frame without an `error` reply for the correct field name. If the server source is accessible, read its config-handshake parser directly to confirm the field name — that is authoritative over the probe.

- [ ] **Step 2: If the field name is `initial_prompt` — no code change needed**

If the probe confirms `initial_prompt`, Phase 1's code is already correct. Proceed to Step 4.

- [ ] **Step 3: If the field name is different — correct the code and tests**

If the server expects a different name (e.g. `prompt`), edit `src/voice_commander/dictation/ws_client.py` — change the field name in the config dict and the docstring:

```python
        if prompt:
            config["initial_prompt"] = prompt
```

→ replace `"initial_prompt"` with the confirmed name. Then update every occurrence of `initial_prompt` in `tests/integration/test_dictation_ws_client.py` (the two assertions added in Task 3) to the confirmed name, and update the `ws_client.py` docstring sentences that name the field.

Run: `python -m pytest tests/integration/test_dictation_ws_client.py -v` — Expected: PASS.

- [ ] **Step 4: Delete the probe script**

```powershell
Remove-Item scripts/_probe_ws_prompt.py
```

- [ ] **Step 5: Record the confirmed field name**

In the plan's own commit message (Step 6) and later in ADR 0092 (Task 18), state the confirmed field name explicitly so future readers know it was verified, not guessed.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "test: confirm /ws/transcribe config prompt field name against live server

Verified the streaming server accepts the <CONFIRMED_NAME> field in its
config handshake. <Adjusted ws_client + tests | No code change needed>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Replace `<CONFIRMED_NAME>` and the bracketed phrase with the actual outcome.

---

## Phase 3 — Rework `DictationSession` into a streaming session

Goal: rewrite `DictationSession` so it owns the WebSocket transport and streams each utterance immediately. No audio buffer. After this phase the session has streaming unit + integration tests green; the daemon still imports `DictationSession` but the daemon's `_finalize_dictation` is not yet updated — Phase 3 keeps the old daemon path compiling by leaving `take_and_finish`/`take_audio` **out** and Phase 4 fixes the daemon in the same logical sequence. To keep each phase independently green, Phase 3 also updates the `DictationSession` **unit** test file (`test_dictation_session.py`) so it tests the new surface.

> **Phasing note:** `test_dictation_pipeline.py`, `test_dictation_cancel.py`, `test_dictation_opens_session.py`, `test_dictation_vocab_pipeline.py` import `DictationSession` and drive the daemon. Reworking `DictationSession` (removing `take_and_finish`/`take_audio`) breaks the daemon's `_process_utterance` / `_finalize_pending_dictation_end` at runtime. Therefore **Phase 3 and Phase 4 must be reviewed as a pair** — the daemon will not run correctly between Task 8 and Task 11. The full suite is run green at the **end of Phase 4** (Task 11), not after Task 8. Within Phase 3, the *new* `DictationSession` tests (Task 7) and the streaming integration test (Task 8) are green in isolation.

### Task 6: Rewrite `DictationSession` — streaming transport

**Files:**
- Rewrite: `src/voice_commander/dictation/session.py`

- [ ] **Step 1: Replace `session.py` entirely**

Replace the full contents of `src/voice_commander/dictation/session.py` with:

```python
"""DictationSession — daemon sub-state that streams dictation to a WS server.

While dictation is active the daemon routes every VAD utterance here instead
of the VerbRouter. Each non-end utterance is encoded to a WAV chunk and pushed
onto a synchronous chunk queue; an asyncio-loop thread drains that queue and
streams the chunks to the ``/ws/transcribe`` server. The server's ``partial``
replies are folded through :class:`~voice_commander.dictation.local_agreement.LocalAgreement`
under a lock, accumulating the confirmed-word transcript.

Finalisation happens via one of three exit paths (lifecycle wiring unchanged
from ADR 0086/0089/0090 — only the session internals changed, ADR 0092):

(a) **End-word path** — the pipeline thread recognises the configured end word
    (default ``"done"``), calls :meth:`finish`, which pushes the end sentinel,
    joins the asyncio thread, finalises ``LocalAgreement``, and returns the
    stabilised transcript.

(b) **Hotkey-end path** — the hotkey thread calls :meth:`request_end`, which
    sets :attr:`pending_end` without deactivating the session. The pipeline
    thread later drains in-flight utterances and calls :meth:`finish`.

(c) **Spoken-cancel path** — :meth:`handle_utterance` recognises the configured
    cancel word and returns ``"cancel"``; the pipeline thread calls
    :meth:`cancel`, which closes the WebSocket and discards the transcript.

Threading: the confirmed-word accumulator + ``LocalAgreement`` are touched by
the asyncio-loop thread (via ``_on_partial``) and by :meth:`finish` (the
``_dictation_executor`` thread). A single lock (``_agreement_lock``)
serialises them — the ADR 0091 Task-9 pattern.

A connect failure inside the asyncio thread is recorded on :attr:`error`;
:meth:`finish` then returns whatever ``LocalAgreement`` confirmed (``""`` when
nothing connected) and the daemon emits ``dictation.error`` when ``error`` is
set and the transcript is empty.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt

from ..verb_router import _normalize_spoken
from .bridge import pump
from .local_agreement import LocalAgreement
from .postprocess import build_prompt
from .store import encode_wav
from .vocab import Vocabulary
from .ws_client import stream_transcribe

logger = logging.getLogger(__name__)


class _BusLike(Protocol):
    """The minimal EventBus surface DictationSession depends on.

    Structurally satisfied by :class:`~voice_commander.event_bus.EventBus`.
    """

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None: ...


UtteranceKind = Literal["buffered", "end", "cancel"]

# Extra seconds added to idle_timeout_s when bounding the asyncio-thread join,
# so finish() never blocks the _dictation_executor indefinitely.
_JOIN_MARGIN_S = 10.0


class DictationSession:
    """Streams one dictation to the WebSocket server; owns the transport."""

    def __init__(
        self,
        bus: _BusLike,
        ws_url: str,
        language: str = "en",
        idle_timeout_s: float = 30.0,
        end_word: str = "done",
        cancel_word: str = "cancel",
    ) -> None:
        self._bus = bus
        self._ws_url = ws_url
        self._language = language
        self._idle_timeout_s = idle_timeout_s
        self._end_word = _normalize_spoken(end_word)
        self._lock = threading.Lock()
        self._active = False
        self._pending_end = threading.Event()

        # --- streaming transport state (recreated per start()) ---
        self._chunk_q: queue.Queue[bytes | None] | None = None
        self._loop_thread: threading.Thread | None = None
        self._agreement = LocalAgreement()
        self._agreement_lock = threading.Lock()
        self._confirmed: list[str] = []
        self._vocab: Vocabulary = Vocabulary()
        self.error: str | None = None

        # Validate cancel_word against end_word and emptiness. Cross-field
        # validation lives here (precedent: dictation_key == hotkey_key
        # warn-and-degrade in daemon.py).
        normalized_cancel = _normalize_spoken(cancel_word)
        if not normalized_cancel:
            logger.warning(
                "dictation: cancel_word %r is empty after normalization; "
                "spoken cancel disabled",
                cancel_word,
            )
            self._cancel_word: str | None = None
        elif normalized_cancel == self._end_word:
            logger.warning(
                "dictation: cancel_word %r collides with end_word %r; "
                "spoken cancel disabled to avoid ambiguity",
                cancel_word,
                end_word,
            )
            self._cancel_word = None
        else:
            self._cancel_word = normalized_cancel

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def pending_end(self) -> bool:
        """True when the hotkey-end path has requested finalisation.

        Set by :meth:`request_end`; cleared by :meth:`finish` and
        :meth:`cancel`. Safe to read from any thread without the lock —
        :meth:`threading.Event.is_set` is atomic in CPython.
        """
        return self._pending_end.is_set()

    def request_end(self) -> None:
        """Signal that the hotkey-end path wants to finalise dictation.

        Called from the hotkey thread. Does NOT deactivate the session — the
        pipeline thread polls :attr:`pending_end` and calls :meth:`finish`
        after draining in-flight utterances. A no-op when already inactive.
        """
        with self._lock:
            if not self._active:
                return
            self._pending_end.set()
        logger.debug("dictation: pending_end requested")

    def start(self, vocab: Vocabulary) -> None:
        """Enter dictation mode and open the streaming WebSocket transport.

        *vocab* is the daemon's start-time snapshot of ``vocab.json``. It is
        stored so :meth:`finish` applies corrections/commands against the same
        snapshot, and ``build_prompt(vocab)`` biases the decoder. The asyncio
        thread is spawned here; the WebSocket connect happens inside it, so a
        connect failure is reported asynchronously via :attr:`error`.
        """
        with self._lock:
            if self._active:
                return
            self._active = True
            self._pending_end.clear()
        self._agreement = LocalAgreement()
        self._confirmed = []
        self._vocab = vocab
        self.error = None
        self._chunk_q = queue.Queue()
        prompt = build_prompt(vocab)
        self._loop_thread = threading.Thread(
            target=self._run_asyncio,
            args=(self._chunk_q, prompt),
            name="dictation-ws-loop",
            daemon=True,
        )
        self._loop_thread.start()
        self._bus.publish("dictation.start", {})
        logger.info("dictation: started (streaming)")

    @property
    def vocab(self) -> Vocabulary:
        """The start-time vocab snapshot — used by the daemon for post-processing."""
        return self._vocab

    def handle_utterance(
        self, audio: npt.NDArray[np.float32], text: str
    ) -> UtteranceKind:
        """Classify an utterance: ``"end"``, ``"cancel"``, or ``"buffered"``.

        * ``"end"`` — transcript is the end word (exact normalized match).
          NOT streamed; caller MUST call :meth:`finish`.
        * ``"cancel"`` — transcript is the cancel word (exact normalized match,
          when ``_cancel_word`` is not ``None``). NOT streamed; caller MUST
          call :meth:`cancel`.
        * ``"buffered"`` — audio is encoded to a WAV chunk and pushed onto the
          chunk queue (streamed immediately); session stays active. The name
          ``"buffered"`` is kept for wire-compatibility with the daemon's
          existing ``kind ==`` branches — no audio is actually buffered.

        A no-op returning ``"buffered"`` when inactive (lost race with
        finish/cancel).
        """
        with self._lock:
            if not self._active:
                return "buffered"
            normalized = _normalize_spoken(text)
            if normalized == self._end_word:
                return "end"
            if self._cancel_word is not None and normalized == self._cancel_word:
                return "cancel"
            chunk_q = self._chunk_q

        # encode_wav + queue push happen outside the lock — encoding is pure
        # CPU work and the queue is unbounded, so neither can deadlock.
        if chunk_q is None:
            return "buffered"
        try:
            wav = encode_wav(audio)
        except Exception:
            logger.exception("dictation: encode_wav failed for an utterance chunk")
            self.error = "encode"
            return "buffered"
        chunk_q.put(wav)
        return "buffered"

    def finish(self) -> str:
        """End dictation normally; return the raw stabilised transcript.

        Pushes the ``None`` end sentinel, joins the asyncio-loop thread (bounded
        by ``idle_timeout_s + _JOIN_MARGIN_S``), finalises ``LocalAgreement``,
        and returns the confirmed-word transcript. Returns ``""`` when the
        WebSocket never connected or produced nothing. Publishes
        ``dictation.end {reason: "done"}``. Idempotent — a no-op returning
        ``""`` when the session is already inactive (lost race).
        """
        with self._lock:
            if not self._active:
                return ""
            self._active = False
            self._pending_end.clear()
            chunk_q = self._chunk_q
            loop_thread = self._loop_thread
            self._chunk_q = None
            self._loop_thread = None

        if chunk_q is not None:
            chunk_q.put(None)  # end sentinel — bridge.pump forwards it
        if loop_thread is not None:
            loop_thread.join(timeout=self._idle_timeout_s + _JOIN_MARGIN_S)
            if loop_thread.is_alive():
                logger.warning(
                    "dictation: ws-loop thread did not stop within %.0fs — "
                    "transcript may be incomplete",
                    self._idle_timeout_s + _JOIN_MARGIN_S,
                )

        with self._agreement_lock:
            self._confirmed.extend(self._agreement.finalize())
            text = " ".join(self._confirmed)

        self._bus.publish("dictation.end", {"reason": "done"})
        logger.info("dictation: finished (streaming) — %d chars", len(text))
        return text

    def cancel(self) -> None:
        """Abort dictation; close the WebSocket and discard the transcript.

        Pushes the end sentinel so the asyncio thread unwinds, joins it with a
        bounded timeout, discards the confirmed words, and publishes
        ``dictation.end {reason: "cancel"}``. A no-op (no event) when the
        session was already inactive.
        """
        with self._lock:
            was_active = self._active
            self._active = False
            self._pending_end.clear()
            chunk_q = self._chunk_q
            loop_thread = self._loop_thread
            self._chunk_q = None
            self._loop_thread = None

        if chunk_q is not None:
            chunk_q.put(None)
        if loop_thread is not None:
            loop_thread.join(timeout=self._idle_timeout_s + _JOIN_MARGIN_S)
        with self._agreement_lock:
            self._confirmed = []
            self._agreement = LocalAgreement()

        if was_active:
            self._bus.publish("dictation.end", {"reason": "cancel"})
            logger.info("dictation: cancelled")

    # --- asyncio-loop thread internals ---

    def _run_asyncio(self, chunk_q: "queue.Queue[bytes | None]", prompt: str) -> None:
        """Entry point for the asyncio-loop thread (one per dictation)."""
        try:
            asyncio.run(self._async_main(chunk_q, prompt))
        except OSError as exc:
            logger.error("dictation: WebSocket connection failed — %s", exc)
            self.error = "endpoint"
        except Exception:  # noqa: BLE001 - surface any pipeline failure
            logger.exception("dictation: streaming pipeline error")
            self.error = "endpoint"

    async def _async_main(
        self, chunk_q: "queue.Queue[bytes | None]", prompt: str
    ) -> None:
        async_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        bridge_task = asyncio.create_task(pump(chunk_q, async_q))
        try:
            await stream_transcribe(
                self._ws_url,
                self._language,
                async_q,
                self._on_partial,
                self._idle_timeout_s,
                prompt=prompt,
            )
        finally:
            bridge_task.cancel()

    def _on_partial(self, text: str) -> None:
        """Fold one server partial into the agreement. Runs on the loop thread.

        Holds ``_agreement_lock`` so it can never race :meth:`finish`'s
        ``finalize`` even if the loop-thread join times out.
        """
        with self._agreement_lock:
            self._confirmed.extend(self._agreement.commit(text))
```

- [ ] **Step 2: Verify the module imports**

Run: `python -c "from voice_commander.dictation.session import DictationSession; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```powershell
git add src/voice_commander/dictation/session.py
git commit -m "feat: rework DictationSession into a streaming WebSocket session

DictationSession no longer buffers audio. start(vocab) opens the WS transport
on an asyncio-loop thread; handle_utterance encodes each utterance to a WAV
chunk and streams it; finish() returns the LocalAgreement-stabilised
transcript. take_audio/take_and_finish are removed (ADR 0092).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 7: Rewrite the `DictationSession` unit tests

**Files:**
- Rewrite: `tests/unit/test_dictation_session.py`

The old unit tests assert `take_audio`/`take_and_finish`/buffering — all removed. Rewrite to test the new streaming surface with an in-process mock WebSocket server. The cancel-word/end-word classification + collision guard tests are preserved (those still apply).

- [ ] **Step 1: Write the failing test file**

Replace the full contents of `tests/unit/test_dictation_session.py` with:

```python
"""Unit + light-integration tests for the streaming DictationSession.

A mock WebSocket server (websockets.asyncio.server.serve) stands in for the
real /ws/transcribe endpoint. The session's own asyncio-loop thread connects
to it. These tests exercise classification, the streaming round-trip, and the
finish/cancel exit paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import numpy as np
import pytest
from websockets.asyncio.server import serve

from voice_commander.dictation.session import DictationSession
from voice_commander.dictation.vocab import Correction, Vocabulary


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


class _MockWsServer:
    """Runs websockets.serve on a background asyncio loop thread.

    Replies one ``partial`` frame per binary chunk; the partial text is the
    next entry of *replies* (last entry repeats). Exposes the bound URL.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self.url = ""
        self.configs: list[dict] = []

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        state = {"i": 0}

        async def handler(conn):
            async for message in conn:
                if isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "config":
                        self.configs.append(data)
                else:
                    text = self._replies[min(state["i"], len(self._replies) - 1)]
                    state["i"] += 1
                    await conn.send(json.dumps({"type": "partial", "text": text}))

        server = await serve(handler, "localhost", 0)
        port = server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        self._ready.set()
        await asyncio.Future()  # run forever

    def start(self) -> str:
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "mock WS server did not start"
        return self.url


# --- classification (no server needed) ---


def test_handle_utterance_when_inactive_is_buffered() -> None:
    s = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    assert s.handle_utterance(_audio(), "anything") == "buffered"


def test_end_word_returns_end_exact_standalone() -> None:
    s = _MockWsServer([])
    url = s.start()
    sess = DictationSession(_FakeBus(), ws_url=url, end_word="done")
    sess.start(Vocabulary())
    assert sess.handle_utterance(_audio(), "I am done with this") == "buffered"
    assert sess.handle_utterance(_audio(), "Done.") == "end"
    sess.finish()


def test_cancel_word_collision_disables_cancel(caplog) -> None:
    bus = _FakeBus()
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.session"):
        sess = DictationSession(
            bus, ws_url="ws://localhost:1", end_word="done", cancel_word="done"
        )
    assert sess._cancel_word is None
    assert any(
        "collision" in r.message.lower() or "cancel" in r.message.lower()
        for r in caplog.records
    )


def test_cancel_word_empty_disables_cancel(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.session"):
        sess = DictationSession(
            _FakeBus(), ws_url="ws://localhost:1", end_word="done", cancel_word=""
        )
    assert sess._cancel_word is None


# --- streaming round-trip (mock server) ---


def test_start_publishes_dictation_start() -> None:
    bus = _FakeBus()
    url = _MockWsServer([]).start()
    sess = DictationSession(bus, ws_url=url)
    sess.start(Vocabulary())
    assert ("dictation.start", {}) in bus.events
    sess.finish()


def test_streamed_utterances_are_stabilised_by_finish() -> None:
    """Two utterances → two partials → LocalAgreement → finish returns text."""
    bus = _FakeBus()
    server = _MockWsServer(["hello world", "world done"])
    url = server.start()
    sess = DictationSession(bus, ws_url=url, end_word="stop", idle_timeout_s=3.0)
    sess.start(Vocabulary())
    assert sess.handle_utterance(_audio(), "hello world") == "buffered"
    assert sess.handle_utterance(_audio(), "more speech") == "buffered"
    text = sess.finish()
    # commit("hello world")->[]; commit("world done")->["hello"];
    # finalize->["world","done"]
    assert text == "hello world done"
    assert ("dictation.end", {"reason": "done"}) in bus.events
    assert not sess.active


def test_finish_returns_empty_on_connect_failure() -> None:
    """A bad ws_url → asyncio thread records error → finish() returns ''."""
    bus = _FakeBus()
    sess = DictationSession(bus, ws_url="ws://localhost:1", idle_timeout_s=2.0)
    sess.start(Vocabulary())
    text = sess.finish()
    assert text == ""
    assert sess.error == "endpoint"


def test_cancel_discards_and_publishes_cancel() -> None:
    bus = _FakeBus()
    server = _MockWsServer(["hello world"])
    url = server.start()
    sess = DictationSession(bus, ws_url=url, idle_timeout_s=2.0)
    sess.start(Vocabulary())
    sess.handle_utterance(_audio(), "hello world")
    sess.cancel()
    assert not sess.active
    assert ("dictation.end", {"reason": "cancel"}) in bus.events
    # finish() after cancel is a no-op returning ""
    assert sess.finish() == ""


def test_config_frame_carries_prompt_from_vocab() -> None:
    """build_prompt(vocab) is sent in the config handshake."""
    server = _MockWsServer([])
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=2.0)
    vocab = Vocabulary(vocab=("Supabase", "Postgres"))
    sess.start(vocab)
    sess.handle_utterance(_audio(), "filler")  # one chunk forces a config send
    sess.finish()
    assert server.configs, "server received no config frame"
    cfg = server.configs[0]
    # Field name confirmed in Phase 2 — adjust if Phase 2 found a different name.
    assert cfg.get("initial_prompt") == "Supabase, Postgres"


def test_request_end_sets_pending_end() -> None:
    server = _MockWsServer([])
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=2.0)
    sess.start(Vocabulary())
    assert not sess.pending_end
    sess.request_end()
    assert sess.pending_end
    sess.finish()
    assert not sess.pending_end
```

- [ ] **Step 2: Run the tests — verify they pass**

Run: `python -m pytest tests/unit/test_dictation_session.py -v`
Expected: all PASS. (If `test_config_frame_carries_prompt_from_vocab` fails because Phase 2 confirmed a different field name, update the assertion's field name to match.)

- [ ] **Step 3: Commit**

```powershell
git add tests/unit/test_dictation_session.py
git commit -m "test: rewrite DictationSession unit tests for streaming transport

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 8: Add the streaming `DictationSession` integration test

**Files:**
- Create: `tests/integration/test_dictation_streaming_session.py`

This is the spec's required "new integration test: streaming `DictationSession` against a mock WebSocket server — assert chunk-streamed → stabilised → corrections/commands → paste". It exercises the session plus the post-processing functions, mirroring what `_finalize_dictation` will do (Task 9), but in isolation.

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_dictation_streaming_session.py` with:

```python
"""Integration test: streaming DictationSession end-to-end with post-processing.

Drives a real DictationSession against an in-process mock WebSocket server,
then runs the same corrections/commands passes _finalize_dictation will run,
asserting the full transcript-shaping pipeline.
"""

from __future__ import annotations

import asyncio
import json
import threading

import numpy as np
from websockets.asyncio.server import serve

from voice_commander.dictation.postprocess import apply_commands, apply_corrections
from voice_commander.dictation.session import DictationSession
from voice_commander.dictation.vocab import Command, Correction, Vocabulary


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


class _MockWsServer:
    """Replies one partial per binary chunk; partial texts taken from *replies*."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self.url = ""

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        state = {"i": 0}

        async def handler(conn):
            async for message in conn:
                if isinstance(message, (bytes, bytearray)):
                    text = self._replies[min(state["i"], len(self._replies) - 1)]
                    state["i"] += 1
                    await conn.send(json.dumps({"type": "partial", "text": text}))

        server = await serve(handler, "localhost", 0)
        port = server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        self._ready.set()
        await asyncio.Future()

    def start(self) -> str:
        self._thread.start()
        assert self._ready.wait(timeout=5.0)
        return self.url


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


def test_streaming_session_then_postprocessing() -> None:
    """Chunks streamed → stabilised → corrections + commands applied."""
    # Partials chosen so LocalAgreement confirms "supa base new line then code".
    server = _MockWsServer(
        ["supa base", "base new line", "new line then code"]
    )
    url = server.start()
    bus = _FakeBus()
    sess = DictationSession(bus, ws_url=url, end_word="done", idle_timeout_s=3.0)

    vocab = Vocabulary(
        corrections=(Correction(wrong="supa base", right="Supabase"),),
        commands=(Command(phrase="new line", action="newline"),),
    )
    sess.start(vocab)
    sess.handle_utterance(_audio(), "supa base")
    sess.handle_utterance(_audio(), "base new line")
    sess.handle_utterance(_audio(), "new line then code")
    raw = sess.finish()

    # finish() returns the raw stabilised transcript.
    assert raw == "supa base new line then code"

    # _finalize_dictation will then apply corrections + commands.
    text = apply_corrections(raw, vocab.corrections)
    text = apply_commands(text, vocab.commands)
    assert text == "Supabase\nthen code"

    assert ("dictation.start", {}) in bus.events
    assert ("dictation.end", {"reason": "done"}) in bus.events
```

> **Note for the implementer:** verify the partial sequence produces the asserted `raw` string. `LocalAgreement.commit` confirms hypothesis words *before* the overlap with the next chunk. With partials `["supa base", "base new line", "new line then code"]`: commit#1 `[]` (hyp=`supa base`); commit#2 overlap `base` → confirms `["supa"]` (hyp=`base new line`); commit#3 overlap `new line` → confirms `["base"]` (hyp=`new line then code`); finalize → `["new","line","then","code"]`. Total = `supa base new line then code`. If you change the partial strings, recompute the expected `raw`.

- [ ] **Step 2: Run the test — verify it passes**

Run: `python -m pytest tests/integration/test_dictation_streaming_session.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full Phase 3 test set**

Run: `python -m pytest tests/unit/test_dictation_session.py tests/unit/test_dictation_local_agreement.py tests/integration/test_dictation_bridge.py tests/integration/test_dictation_ws_client.py tests/integration/test_dictation_streaming_session.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```powershell
git add tests/integration/test_dictation_streaming_session.py
git commit -m "test: add streaming DictationSession integration test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — Rewire the daemon

Goal: shrink `_finalize_dictation`, update the daemon constructor params, and adapt the dictation-branch wiring to the new `DictationSession` surface (`start(vocab)`, `finish()` instead of `take_and_finish`/`take_audio`). At the **end of this phase** the full test suite is green.

### Task 9: Shrink `_finalize_dictation` and update the daemon constructor

**Files:**
- Modify: `src/voice_commander/daemon.py`

- [ ] **Step 1: Update the daemon constructor signature**

In `src/voice_commander/daemon.py`, find the constructor keyword parameter (around line 163–164):

```python
        dictation_session: DictationSession | None = None,
        dictation_endpoint: str = "",
```

Replace with:

```python
        dictation_session: DictationSession | None = None,
        dictation_ws_url: str = "",
        dictation_language: str = "en",
        dictation_idle_timeout_s: float = 30.0,
```

- [ ] **Step 2: Update the constructor body field assignment**

Find (around line 263–264):

```python
        self._dictation_session = dictation_session
        self._dictation_endpoint = dictation_endpoint
```

Replace with:

```python
        self._dictation_session = dictation_session
        self._dictation_ws_url = dictation_ws_url
        self._dictation_language = dictation_language
        self._dictation_idle_timeout_s = dictation_idle_timeout_s
```

- [ ] **Step 3: Rewrite `_finalize_dictation`**

Find the whole `_finalize_dictation` method (around lines 898–961). Replace it entirely with:

```python
    def _finalize_dictation(self) -> None:
        """Worker-thread finalize: take the stabilised transcript → post-process → paste.

        Runs on ``self._dictation_executor`` so the pipeline thread is never
        blocked by the WebSocket teardown / join. ``DictationSession.finish``
        joins the asyncio-loop thread and returns the raw stabilised
        transcript; this method applies corrections + commands, pastes, saves
        ``last.txt``, and publishes events.

        Failure handling (events + chimes match the batch path exactly,
        ADR 0092):

        - WebSocket connect failed / nothing transcribed → ``dictation.error
          {reason: "endpoint"}`` + miss chime.
        - ``encode_wav`` failed on a streamed chunk → ``dictation.error
          {reason: "encode"}`` + miss chime.
        - ``paste_via_clipboard`` failed → ``dictation.error
          {reason: "clipboard"}`` + miss chime.

        Corrections run before commands so a lightly-mistranscribed command
        phrase can be repaired into its canonical form before command matching
        (ADR 0088, carried over).
        """
        from .dictation import clipboard
        from .dictation.postprocess import apply_commands, apply_corrections

        if self._dictation_session is None:
            return

        # Snapshot the vocab the session started with (one consistent snapshot
        # per dictation — ADR 0092).
        vocab = self._dictation_session.vocab

        # finish() joins the asyncio thread and returns the raw transcript.
        text = self._dictation_session.finish()

        # encode_wav failure on a chunk is recorded on session.error == "encode".
        if self._dictation_session.error == "encode":
            logger.warning("dictation: a chunk failed to encode")
            self._publish("dictation.error", {"reason": "encode"})
            self._feedback.on_miss("(dictation: encode error)", ())
            return

        # Empty transcript: either the WS never connected, or the user said
        # nothing. error == "endpoint" disambiguates the failure case.
        if not text:
            if self._dictation_session.error == "endpoint":
                logger.warning("dictation: streaming endpoint failed")
                self._publish("dictation.error", {"reason": "endpoint"})
                self._feedback.on_miss("(dictation: endpoint error)", ())
            else:
                logger.info("dictation: empty transcript — nothing to paste")
            return

        # Post-process: corrections then commands (ADR 0088).
        text = apply_corrections(text, vocab.corrections)
        text = apply_commands(text, vocab.commands)

        self._dictation_store.save_text(text)

        try:
            clipboard.paste_via_clipboard(text)
        except Exception:
            logger.exception("dictation: clipboard paste failed")
            self._publish("dictation.error", {"reason": "clipboard"})
            self._feedback.on_miss("(dictation: clipboard error)", ())
            return

        self._publish("transcript", {"text": text, "confidence": 1.0})
        self._publish("dictation.result", {"text": text})
        logger.info("dictation: pasted %d chars", len(text))
```

- [ ] **Step 4: Verify the daemon module imports**

Run: `python -c "import voice_commander.daemon; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/daemon.py
git commit -m "feat: rewrite _finalize_dictation for streaming, update daemon ctor params

_finalize_dictation now calls session.finish() for the stabilised transcript
instead of POSTing buffered audio. Constructor takes dictation_ws_url +
dictation_language + dictation_idle_timeout_s in place of dictation_endpoint.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 10: Adapt the daemon dictation-branch wiring

**Files:**
- Modify: `src/voice_commander/daemon.py`

The dictation branch in `_process_utterance`, the `__dictation.start` handler, `on_dictation_toggle`, and `_finalize_pending_dictation_end` all call `DictationSession.start()` (now needs a `vocab` arg) and `take_and_finish()` (removed). This task updates every call site. A new helper `_load_vocab()` centralises the hot-reload.

- [ ] **Step 1: Add a `_load_vocab` helper**

In `src/voice_commander/daemon.py`, add this method immediately **before** `_finalize_dictation` (so it sits with the other dictation helpers):

```python
    def _load_vocab(self) -> "Vocabulary":
        """Hot-reload vocab.json — called once per dictation at session start."""
        return self._vocab_store.load()
```

Add the import to the daemon's `TYPE_CHECKING` block (or near the existing dictation imports at the top of the file). Find the line:

```python
from .dictation.vocab import VocabStore
```

Replace with:

```python
from .dictation.vocab import Vocabulary, VocabStore
```

- [ ] **Step 2: Update the bare-`dictate` handler (`__dictation.start`)**

Find (around lines 837–839):

```python
            if len(plan.steps) == 1 and plan.steps[0].name == "__dictation.start":
                if self._dictation_session is not None:
                    self._dictation_session.start()
```

Replace with:

```python
            if len(plan.steps) == 1 and plan.steps[0].name == "__dictation.start":
                if self._dictation_session is not None:
                    self._dictation_session.start(self._load_vocab())
```

- [ ] **Step 3: Update `on_dictation_toggle` — both `start()` calls**

In `on_dictation_toggle` (around lines 486–530) there are **two** `self._dictation_session.start()` calls. The first (around line 507, the no-session-open branch):

```python
            self._session_opened_by_dictation = True
            if self._dictation_session is None:
                return
            self._dictation_session.start()
            return
```

Replace with:

```python
            self._session_opened_by_dictation = True
            if self._dictation_session is None:
                return
            self._dictation_session.start(self._load_vocab())
            return
```

The second (around line 530, the `else` branch — session open, dictation not active):

```python
        else:
            self._dictation_session.start()
```

Replace with:

```python
        else:
            self._dictation_session.start(self._load_vocab())
```

- [ ] **Step 4: Update the `_process_utterance` dictation branch**

Find the dictation branch (around lines 716–736):

```python
            if self._dictation_session is not None and self._dictation_session.active:
                kind = self._dictation_session.handle_utterance(utterance, result.text)
                if kind == "end":
                    audio = self._dictation_session.take_and_finish()
                    # Close-before-finalize: submit the session close FIRST so the
                    # recording stops promptly (≤5 s VAD-worker join) before the
                    # network POST begins. _end_owned_session_if_needed is a no-op
                    # when _session_opened_by_dictation is False (Scroll Lock session).
                    # Submitted unconditionally — covers the empty-buffer edge case.
                    self._dictation_executor.submit(self._end_owned_session_if_needed)
                    if audio is not None:
                        self._dictation_executor.submit(self._finalize_dictation, audio)
                elif kind == "cancel":
                    # Spoken cancel: abort dictation (no POST, no paste).
                    # Submit _end_owned_session_if_needed to close a Ctrl-opened session.
                    # cancel() publishes dictation.end {"reason": "cancel"}. (ADR 0089)
                    self._dictation_session.cancel()
                    self._dictation_executor.submit(self._end_owned_session_if_needed)
                # "buffered" → fall through, nothing to do
                run.set_status("ok")
                return
```

Replace with:

```python
            if self._dictation_session is not None and self._dictation_session.active:
                kind = self._dictation_session.handle_utterance(utterance, result.text)
                if kind == "end":
                    # Close-before-finalize (ADR 0090): submit the session close
                    # FIRST so recording stops promptly (≤5 s VAD-worker join)
                    # before _finalize_dictation joins the WS asyncio thread.
                    # _end_owned_session_if_needed is a no-op when
                    # _session_opened_by_dictation is False (Scroll Lock session).
                    # _finalize_dictation itself calls session.finish() — it must
                    # be submitted unconditionally (no audio handle to check).
                    self._dictation_executor.submit(self._end_owned_session_if_needed)
                    self._dictation_executor.submit(self._finalize_dictation)
                elif kind == "cancel":
                    # Spoken cancel: abort dictation (no transcript, no paste).
                    # cancel() closes the WS and publishes
                    # dictation.end {"reason": "cancel"}.
                    self._dictation_session.cancel()
                    self._dictation_executor.submit(self._end_owned_session_if_needed)
                # "buffered" → utterance streamed inside handle_utterance.
                run.set_status("ok")
                return
```

- [ ] **Step 5: Update `_finalize_pending_dictation_end`**

Find the whole method (around lines 861–896). Replace it entirely with:

```python
    def _finalize_pending_dictation_end(self) -> None:
        """Called on the pipeline thread when the hotkey-end drain window expires.

        Submits the close + finalize tasks to the dictation executor and returns
        immediately — all heavy work (WS teardown, asyncio-thread join,
        post-processing, paste) happens off-thread on ``_dictation_executor``.

        Close-before-finalize ordering (ADR 0090):
        1. submit ``_end_owned_session_if_needed`` UNCONDITIONALLY — covers the
           empty-session Ctrl-open → Ctrl-close case.
        2. submit ``_finalize_dictation`` — it calls ``session.finish()``, which
           is a no-op returning ``""`` when the session is already inactive
           (the "done"-word path won the race), so submitting it is always safe.
        """
        if self._dictation_session is None:
            return
        self._dictation_executor.submit(self._end_owned_session_if_needed)
        self._dictation_executor.submit(self._finalize_dictation)
        logger.info("dictation: hotkey-end drain complete — close + finalize submitted")
```

> **Note:** `_finalize_dictation` is now always idempotent-safe to submit because `session.finish()` is a no-op when inactive. The previous `take_and_finish()`-returns-`None` guard is no longer needed.

- [ ] **Step 6: Update the `build_streaming_daemon` wiring**

Find the `DictationSession` construction (around lines 1429–1434):

```python
    # --- Dictation mode (ADR 0086) ---
    dictation_session = DictationSession(
        bus=event_bus,
        end_word=cfg.dictation.end_word,
        cancel_word=cfg.dictation.cancel_word,
    )
```

Replace with:

```python
    # --- Dictation mode (ADR 0086, streaming since ADR 0092) ---
    dictation_session = DictationSession(
        bus=event_bus,
        ws_url=cfg.dictation.ws_url,
        language=cfg.dictation.language,
        idle_timeout_s=float(cfg.dictation.idle_timeout_seconds),
        end_word=cfg.dictation.end_word,
        cancel_word=cfg.dictation.cancel_word,
    )
```

- [ ] **Step 7: Update the `StreamingDaemon(...)` call**

Find (around line 1532–1533):

```python
        dictation_session=dictation_session,
        dictation_endpoint=cfg.dictation.endpoint,
```

Replace with:

```python
        dictation_session=dictation_session,
        dictation_ws_url=cfg.dictation.ws_url,
        dictation_language=cfg.dictation.language,
        dictation_idle_timeout_s=float(cfg.dictation.idle_timeout_seconds),
```

> **Dependency note:** this step references `cfg.dictation.ws_url` / `.language` / `.idle_timeout_seconds`, which do not exist on `DictationConfig` until Phase 5 (Task 12). To keep Phase 4 green in isolation, **Task 12 must be done before re-running the full suite at Step 9**. If executing strictly in order, swap: do Task 12 (config) immediately after Task 10's Steps 1–5, then return to Steps 6–9. The recommended execution order is **Task 9 → 10 (steps 1–5) → 12 → 10 (steps 6–9) → 11**. The subagent-driven workflow reviewer should be told this ordering.

- [ ] **Step 8: Verify the daemon module imports**

Run: `python -c "import voice_commander.daemon; print('ok')"`
Expected: prints `ok` (after Task 12 has added the config fields).

- [ ] **Step 9: Commit**

```powershell
git add src/voice_commander/daemon.py
git commit -m "feat: rewire daemon dictation branch for streaming DictationSession

start(vocab) carries the hot-reloaded vocab snapshot; the end/hotkey-end paths
submit _finalize_dictation (which calls session.finish()) instead of capturing
a buffer. build_streaming_daemon wires the new ws_url/language/idle params.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 11: Rewrite the daemon dictation integration tests

**Files:**
- Rewrite: `tests/integration/test_dictation_pipeline.py`
- Rewrite: `tests/integration/test_dictation_cancel.py`
- Rewrite: `tests/integration/test_dictation_opens_session.py`
- Rewrite: `tests/integration/test_dictation_vocab_pipeline.py`

These four tests build a real daemon and previously monkeypatched `remote.post_audio`. The streaming path has no `post_audio` — the tests must build the `DictationSession` against an in-process mock WebSocket server. This task introduces a shared mock-server helper to avoid repeating it four times (DRY).

- [ ] **Step 1: Create the shared mock-WS-server helper**

Create `tests/integration/_dictation_ws.py` with:

```python
"""Shared in-process mock WebSocket server for dictation integration tests.

Replaces the old mock /inference HTTP endpoint. The server replies one
``partial`` frame per binary chunk; the partial texts are supplied per
session via the ``replies`` constructor argument.
"""

from __future__ import annotations

import asyncio
import json
import threading


class MockWsServer:
    """A websockets.serve server on a background asyncio loop thread.

    Use as a context manager or call .start()/.stop(). ``url`` is the bound
    ws:// URL once started. ``replies`` is the list of partial texts returned
    one-per-chunk (the last entry repeats if more chunks arrive).
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self._server = None
        self.url = ""
        self.configs: list[dict] = []
        self.chunk_count = 0

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.run_forever()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve

        state = {"i": 0}

        async def handler(conn):
            async for message in conn:
                if isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "config":
                        self.configs.append(data)
                else:
                    self.chunk_count += 1
                    text = self._replies[min(state["i"], len(self._replies) - 1)]
                    state["i"] += 1
                    await conn.send(json.dumps({"type": "partial", "text": text}))

        self._server = await serve(handler, "localhost", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        self._ready.set()

    def start(self) -> str:
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "mock WS server did not start"
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        self._loop.call_soon_threadsafe(self._loop.stop)

    def __enter__(self) -> "MockWsServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
```

- [ ] **Step 2: Rewrite `test_dictation_pipeline.py`**

Replace the full contents of `tests/integration/test_dictation_pipeline.py` with:

```python
"""Daemon pipeline integration tests for streaming dictation mode (ADR 0092).

Builds a real daemon; the DictationSession streams to an in-process mock
WebSocket server. Only the OS clipboard (paste_via_clipboard) is monkeypatched.
Everything else — VerbRouter, DictationSession, _process_utterance,
_finalize_dictation, executor, DictationStore — is real production code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules

from ._dictation_ws import MockWsServer

if TYPE_CHECKING:
    from voice_commander.daemon import StreamingDaemon


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray) -> _Transcription:
        return self.queue.pop(0)


def _make_daemon(
    transcripts: list[_Transcription],
    tmp_path: Path,
    ws_url: str,
) -> tuple["StreamingDaemon", DictationSession, CapturingFeedbackSink, EventBus]:
    """Build a stripped-down daemon whose DictationSession streams to *ws_url*."""
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()

    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(
        bus=bus, ws_url=ws_url, end_word="done", idle_timeout_s=3.0
    )

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        dictation_ws_url=ws_url,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


def test_dictation_enter_stream_finalize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """type -> speak -> done : utterances streamed, transcript pasted."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(["hello world", "world done"]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("dictate"),
                _Transcription("hello world"),
                _Transcription("done"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        audio = np.zeros(16000, dtype=np.float32)

        daemon._process_utterance(audio)  # "dictate" → session active
        assert dictation_session.active is True

        daemon._process_utterance(audio)  # content → streamed
        assert dictation_session.active is True

        daemon._process_utterance(audio)  # "done" → finalize submitted
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert dictation_session.active is False
    # commit("hello world")->[]; commit("world done")->["hello"];
    # finalize->["world","done"]
    assert pasted == ["hello world done"], f"unexpected paste: {pasted}"
    assert daemon._dictation_store.read_text() == "hello world done"


def test_dictation_endpoint_failure_chimes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bad ws_url → finish() returns '' with error → miss chime fires."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    # No server — point at a closed port to force a connect failure.
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("some words"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        ws_url="ws://localhost:1",
    )
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "dictate"
    daemon._process_utterance(audio)  # "some words"
    daemon._process_utterance(audio)  # "done" → finalize

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert any(c[0] == "on_miss" for c in feedback.calls), (
        f"expected an on_miss call after endpoint failure; got {feedback.calls}"
    )
    assert pasted == []


def test_hotkey_end_with_in_flight_utterance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hotkey-end after queuing 2 utterances streams them, then finalizes."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(["hello world", "world more", "more text"]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("hello world"),
                _Transcription("more text"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        audio = np.zeros(16000, dtype=np.float32)
        dictation_session.start(daemon._load_vocab())
        dictation_session.request_end()
        daemon._process_utterance(audio)  # streamed
        daemon._process_utterance(audio)  # streamed
        daemon._finalize_pending_dictation_end()
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted, f"expected a paste after hotkey-end drain; got {pasted}"
    assert not dictation_session.active
    assert not dictation_session.pending_end


def test_hotkey_end_empty_session_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """request_end() then _finalize_pending_dictation_end() with no speech
    must not crash and must not paste."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer([]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[], tmp_path=tmp_path, ws_url=server.url
        )
        dictation_session.start(daemon._load_vocab())
        dictation_session.request_end()
        daemon._finalize_pending_dictation_end()
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted == [], "paste must not be called when nothing was streamed"
    assert not dictation_session.active


def test_scroll_lock_cancel_wins_the_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cancel() after request_end() clears pending_end; finalize is a no-op."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer([]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[], tmp_path=tmp_path, ws_url=server.url
        )
        dictation_session.start(daemon._load_vocab())
        dictation_session.request_end()
        assert dictation_session.pending_end
        dictation_session.cancel()
        assert not dictation_session.pending_end
        assert not dictation_session.active

        daemon._finalize_pending_dictation_end()
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted == []
```

> The original `test_pipeline_loop_drains_before_finalize` and `test_hotkey_end_finalizes_without_trailing_utterance` exercise the `_pipeline_loop` thread and the `_DICTATION_WAKE` sentinel. The sentinel/drain wiring is **unchanged** by this plan, so those two tests still pass against the streaming path with only the monkeypatch swap and `start(vocab)` change. Port them too:

- [ ] **Step 3: Port the two pipeline-loop regression tests**

Append to `tests/integration/test_dictation_pipeline.py`:

```python
def test_pipeline_loop_drains_before_finalize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Full pipeline-loop regression: queue utterances, on_dictation_toggle(),
    assert the streamed transcript is pasted after the drain."""
    import threading
    import time

    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(["line one", "one line two", "line two end"]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("spoken line one"),
                _Transcription("spoken line two"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop, name="vc-pipeline-test", daemon=True
        )
        pipeline_thread.start()

        audio = np.zeros(16000, dtype=np.float32)
        daemon._session_active = True
        dictation_session.start(daemon._load_vocab())
        daemon._utt_q.put((audio, daemon._audio_gen))
        daemon._utt_q.put((audio, daemon._audio_gen))
        daemon.on_dictation_toggle()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pasted:
            time.sleep(0.05)

        daemon._utt_q.put(None)
        pipeline_thread.join(timeout=3.0)
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted, f"hotkey-end should have pasted after pipeline drained; got {pasted}"


def test_hotkey_end_finalizes_without_trailing_utterance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR 0089 regression: hotkey-end with NO trailing utterance still
    finalizes (the _DICTATION_WAKE sentinel wakes the blocked pipeline)."""
    import threading
    import time

    finalized = threading.Event()
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda *a, **kw: finalized.set(),
    )

    @dataclass
    class _SentinelTranscription:
        text: str
        confidence: float = 0.95
        no_speech_prob: float = 0.05

    processed = threading.Event()

    class _SentinelStub:
        def __init__(self, result: _SentinelTranscription) -> None:
            self._result = result

        def load(self) -> None: ...
        def unload(self) -> None: ...

        def transcribe(self, _audio: np.ndarray) -> _SentinelTranscription:
            result = self._result
            processed.set()
            return result

    with MockWsServer(["some dictated content"]) as server:
        from voice_commander.daemon import StreamingDaemon
        from voice_commander.dispatcher import Dispatcher
        from voice_commander.picker.registry import reset_global_picker_registry
        from voice_commander.registry import get_global_registry, reset_global_registry

        reset_global_registry()
        reset_global_picker_registry()
        registry = get_global_registry()
        bus = EventBus()
        feedback = CapturingFeedbackSink()
        dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
        verb_router = VerbRouter(
            build_default_rules(), registry=registry, picker_registry=None
        )
        dictation_session = DictationSession(
            bus=bus, ws_url=server.url, end_word="done", idle_timeout_s=3.0
        )
        daemon = StreamingDaemon(
            feedback=feedback,
            recorder=None,
            transcriber=_SentinelStub(_SentinelTranscription("some dictated content")),
            dispatcher=dispatcher,
            verb_router=verb_router,
            registry=registry,
            event_bus=bus,
            dictation_session=dictation_session,
            dictation_ws_url=server.url,
            output_dir=str(tmp_path),
        )
        daemon._transcriber_ready.set()

        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop, name="vc-pipeline-sentinel-test", daemon=True
        )
        daemon._session_active = True
        pipeline_thread.start()

        dictation_session.start(daemon._load_vocab())
        audio = np.zeros(16000, dtype=np.float32)
        daemon._utt_q.put((audio, daemon._audio_gen))

        assert processed.wait(timeout=5.0), "stub transcriber never ran"
        time.sleep(0.020)
        daemon.on_dictation_toggle()

        assert finalized.wait(timeout=3.0), (
            "hotkey-end must finalize within 3 s with no trailing utterance "
            "(ADR 0089 sentinel regression gate)"
        )
        assert not dictation_session.active

        daemon._utt_q.put(None)
        pipeline_thread.join(timeout=3.0)
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)
```

- [ ] **Step 4: Run `test_dictation_pipeline.py`**

Run: `python -m pytest tests/integration/test_dictation_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 5: Rewrite `test_dictation_cancel.py`**

Replace the full contents of `tests/integration/test_dictation_cancel.py` with:

```python
"""Integration tests for spoken dictation cancel (streaming, ADR 0092).

Drives utterances through daemon._process_utterance — the real dispatch path —
so the `elif kind == "cancel"` branch in daemon.py actually executes. Asserts
spoken "cancel" during active dictation: (a) calls DictationSession.cancel(),
(b) never pastes, and (c) emits dictation.end {"reason": "cancel"}.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules

from ._dictation_ws import MockWsServer

if TYPE_CHECKING:
    from voice_commander.daemon import StreamingDaemon


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray) -> _Transcription:
        return self.queue.pop(0)


def _make_daemon(
    transcripts: list[_Transcription],
    tmp_path: Path,
    ws_url: str,
) -> tuple["StreamingDaemon", DictationSession, CapturingFeedbackSink, EventBus]:
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()
    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(
        bus=bus, ws_url=ws_url, end_word="done", cancel_word="cancel", idle_timeout_s=3.0
    )
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        dictation_ws_url=ws_url,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


def test_spoken_cancel_aborts_dictation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """type -> speak -> cancel : session cancelled, nothing pasted.

    The dictation.end {reason:"cancel"} event is published by
    DictationSession.cancel() onto the EventBus. EventBus.subscribe() returns
    a queue.Queue of Event objects (no callback API), so the test subscribes
    a consumer queue before driving the utterances and drains it afterwards.
    """
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(["hello world"]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("dictate"),
                _Transcription("hello world"),
                _Transcription("cancel"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        event_q = bus.subscribe()  # returns a queue.Queue[Event]

        audio = np.zeros(16000, dtype=np.float32)
        daemon._process_utterance(audio)  # "dictate"
        assert dictation_session.active
        daemon._process_utterance(audio)  # "hello world" → streamed
        daemon._process_utterance(audio)  # "cancel" → cancel()
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert not dictation_session.active
    assert pasted == [], "cancel must not paste"

    # Drain the event queue and assert a dictation.end {reason:"cancel"} fired.
    seen: list[tuple[str, dict]] = []
    while not event_q.empty():
        ev = event_q.get_nowait()
        # Event is a frozen dataclass with .type and .data fields
        # (src/voice_commander/event_bus.py).
        seen.append((ev.type, ev.data))
    assert ("dictation.end", {"reason": "cancel"}) in seen, (
        f"expected dictation.end cancel event; got {seen}"
    )
```

- [ ] **Step 6: Run `test_dictation_cancel.py`**

Run: `python -m pytest tests/integration/test_dictation_cancel.py -v`
Expected: all PASS.

- [ ] **Step 7: Rewrite `test_dictation_opens_session.py`**

This file tests ADR 0090 — Right Ctrl opens its own session. The lifecycle wiring is unchanged; only `start()` → `start(vocab)` and the mock-server swap are needed. Read the existing file fully first (it has multiple tests using a `MagicMock` executor to inspect submission order). For each test:
- Add `from ._dictation_ws import MockWsServer` to the imports.
- Change `_make_daemon` to accept a `ws_url` and build `DictationSession(bus=..., ws_url=ws_url, end_word="done", idle_timeout_s=3.0)` and pass `dictation_ws_url=ws_url` to `StreamingDaemon`.
- Wrap each test body in `with MockWsServer([...]) as server:` and pass `server.url`.
- Replace any `monkeypatch.setattr("voice_commander.dictation.remote.post_audio", ...)` with nothing (no remote module) — the mock server supplies transcripts.
- Replace any direct `dictation_session.start()` with `dictation_session.start(daemon._load_vocab())`.
- The tests that assert `_dictation_executor` submission **order** (close-before-finalize) still hold: `_process_utterance` submits `_end_owned_session_if_needed` then `_finalize_dictation` — assert that order. The `_finalize_dictation` call no longer takes an `audio` argument; update any `mock_executor.submit.assert_*` calls that referenced `(self._finalize_dictation, audio)` to just `self._finalize_dictation`.
- Keep every test's *intent* (session opened-by-dictation auto-closes; close submitted before finalize; etc.).

After editing, run: `python -m pytest tests/integration/test_dictation_opens_session.py -v` — Expected: all PASS. Iterate until green.

- [ ] **Step 8: Rewrite `test_dictation_vocab_pipeline.py`**

This file tests that `_finalize_dictation` applies a populated `VocabStore`. In the streaming world, vocab is loaded at `session.start(vocab)` and the snapshot is post-processed in `_finalize_dictation`. Rewrite so:
- A `vocab.json` is written under `tmp_path/outputs/dictation/vocab.json` **before** the daemon starts dictation (so `_load_vocab` picks it up at `start`).
- The mock server returns partials whose stabilised transcript contains the strings the corrections/commands target (e.g. server replies produce `"supa base"` which a correction maps to `"Supabase"`).
- Assert the pasted text reflects the corrections + commands.

Replace the full contents with:

```python
"""Integration test: streaming _finalize_dictation with a populated vocab.json.

vocab.json is loaded at DictationSession.start (the daemon's _load_vocab
hot-reload); the snapshot drives both the WS config prompt and the
corrections/commands applied in _finalize_dictation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules

from ._dictation_ws import MockWsServer

if TYPE_CHECKING:
    from voice_commander.daemon import StreamingDaemon


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray) -> _Transcription:
        return self.queue.pop(0)


def _make_daemon(
    transcripts: list[_Transcription],
    tmp_path: Path,
    ws_url: str,
) -> tuple["StreamingDaemon", DictationSession, CapturingFeedbackSink, EventBus]:
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()
    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(
        bus=bus, ws_url=ws_url, end_word="done", idle_timeout_s=3.0
    )
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        dictation_ws_url=ws_url,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


def test_vocab_corrections_and_commands_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A populated vocab.json shapes the streamed transcript before paste."""
    # Write vocab.json BEFORE the daemon starts dictation.
    vocab_dir = tmp_path / "dictation"
    vocab_dir.mkdir(parents=True)
    (vocab_dir / "vocab.json").write_text(
        json.dumps(
            {
                "vocab": ["Supabase"],
                "corrections": [{"wrong": "supa base", "right": "Supabase"}],
                "commands": [{"phrase": "new line", "action": "newline"}],
            }
        ),
        encoding="utf-8",
    )

    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    # Partials → LocalAgreement → raw "supa base new line then code".
    with MockWsServer(
        ["supa base", "base new line", "new line then code"]
    ) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("dictate"),
                _Transcription("supa base"),
                _Transcription("base new line"),
                _Transcription("new line then code"),
                _Transcription("done"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        audio = np.zeros(16000, dtype=np.float32)
        for _ in range(5):
            daemon._process_utterance(audio)
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    # corrections: "supa base" → "Supabase"; commands: "new line" → "\n".
    assert pasted == ["Supabase\nthen code"], f"unexpected paste: {pasted}"
```

> **Note:** the daemon's `VocabStore` path is `output_dir/"dictation"/"vocab.json"` (see `StreamingDaemon.__init__`). The test writes `tmp_path/"dictation"/vocab.json` because `output_dir=str(tmp_path)`. Verify the `raw` transcript with the same LocalAgreement walk shown in Task 8.

- [ ] **Step 9: Run `test_dictation_vocab_pipeline.py`**

Run: `python -m pytest tests/integration/test_dictation_vocab_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 10: Run the FULL test suite — Phase 4 gate**

Run: `python -m pytest -q`
Expected: all tests PASS **except** the still-present `test_stream_*`/`test_dictation_remote*` files (deleted in Phase 7) and the not-yet-updated `test_web_dictation.py` (updated in Phase 6). If any of those fail, that is expected at this point — note them and proceed. **No other test may fail.** If a non-Phase-7/6 test fails, fix it before committing.

> To get a clean signal, run the suite excluding the known-stale files:
> ```powershell
> python -m pytest -q --ignore=tests/unit/test_stream_capture.py --ignore=tests/unit/test_stream_chunker.py --ignore=tests/unit/test_stream_config.py --ignore=tests/unit/test_stream_sink.py --ignore=tests/unit/test_stream_toggle.py --ignore=tests/integration/test_stream_session.py --ignore=tests/unit/test_dictation_remote.py --ignore=tests/unit/test_dictation_remote_timeout.py --ignore=tests/integration/test_web_dictation.py
> ```
> This must be fully green.

- [ ] **Step 11: Commit**

```powershell
git add tests/integration/_dictation_ws.py tests/integration/test_dictation_pipeline.py tests/integration/test_dictation_cancel.py tests/integration/test_dictation_opens_session.py tests/integration/test_dictation_vocab_pipeline.py
git commit -m "test: rewrite daemon dictation integration tests for streaming

Replaces the mock /inference HTTP endpoint with an in-process mock WebSocket
server (shared helper tests/integration/_dictation_ws.py). All four dictation
integration suites now drive the streaming path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — Config

Goal: update `DictationConfig` and `config.toml.example` for the streaming endpoint. (Per the dependency note in Task 10 Step 7, this phase is executed *between* Task 10's Steps 5 and 6.)

### Task 12: Update `DictationConfig` and `config.toml.example`

**Files:**
- Modify: `src/voice_commander/config.py`
- Modify: `config.toml.example`
- Modify: `tests/unit/` config test (if one asserts `DictationConfig` fields — check `tests/unit/test_config*.py`)

- [ ] **Step 1: Update the `DictationConfig` dataclass**

In `src/voice_commander/config.py`, find:

```python
@dataclass(frozen=True)
class DictationConfig:
    """Dictation mode — remote whisper.cpp transcription (ADR 0086)."""

    endpoint: str = "http://192.168.4.200:8765/inference"
    end_word: str = "done"
    cancel_word: str = "cancel"  # say this word to abort dictation and discard the audio
```

Replace with:

```python
@dataclass(frozen=True)
class DictationConfig:
    """Dictation mode — streaming whisper WebSocket transcription (ADR 0092)."""

    ws_url: str = "ws://192.168.4.200:8765/ws/transcribe"
    language: str = "en"
    end_word: str = "done"
    cancel_word: str = "cancel"  # say this word to abort dictation and discard the transcript
    idle_timeout_seconds: int = 30
```

- [ ] **Step 2: Check for a config test that asserts dictation fields**

Run: `python -m pytest tests/unit -k config -q` and also grep:

Run: `python -c "import pathlib,re; [print(p) for p in pathlib.Path('tests').rglob('test_*.py') if 'endpoint' in p.read_text() and 'dictation' in p.read_text().lower()]"`

If any test asserts `cfg.dictation.endpoint`, update it to `cfg.dictation.ws_url` (and add `language` / `idle_timeout_seconds` assertions if the test is exhaustive).

- [ ] **Step 3: Update `config.toml.example` — the `[dictation]` section**

In `config.toml.example`, find (lines ~7–10):

```toml
[dictation]
endpoint    = "http://192.168.4.200:8765/inference"
end_word    = "done"
cancel_word = "cancel"   # say this word to abort dictation and discard the audio
```

Replace with:

```toml
[dictation]
ws_url               = "ws://192.168.4.200:8765/ws/transcribe"  # streaming whisper WebSocket endpoint
language             = "en"     # whisper decode language
end_word             = "done"   # say this word (standalone) to end dictation
cancel_word          = "cancel" # say this word to abort dictation and discard the transcript
idle_timeout_seconds = 30        # close the stream after this long with no audio
```

- [ ] **Step 4: Delete the `[dictation_stream]` section from `config.toml.example`**

In `config.toml.example`, delete the entire `[dictation_stream]` block (lines ~101–110):

```toml
[dictation_stream]
ws_url                  = "ws://192.168.4.200:8765/ws/transcribe"  # streaming transcription endpoint
language                = "en"   # whisper decode language
vad_threshold           = 0.5    # silero speech probability cutoff (raise in noisy rooms)
min_silence_duration_ms = 1200   # silence gap that ends a chunk (lets mid-sentence pauses through)
speech_pad_ms           = 300    # audio kept before/after detected speech
max_chunk_seconds       = 15     # hard time cap — force-flush a chunk even without a pause
min_chunk_seconds       = 1      # discard chunks shorter than this (too short for whisper)
idle_timeout_seconds    = 30     # close the stream after this long with no audio
# input_device           = 4      # mic device index or name substring; omit for the Windows default input device
```

(Delete the preceding section-separator comment line if one exists immediately above it.)

- [ ] **Step 5: Verify config loads**

Run: `python -c "from voice_commander.config import Config; c = Config.load(__import__('pathlib').Path('config.toml.example')); print(c.dictation)"`
Expected: prints a `DictationConfig(ws_url='ws://...', language='en', end_word='done', cancel_word='cancel', idle_timeout_seconds=30)` with no error.

> If `Config.load` rejects unknown sections strictly, the leftover `[dictation_stream]` in a user's real `config.toml` would error. Check `config.py`'s loader: the dictation_stream `config.py` docstring says "the daemon's loader ignores unknown top-level tables", so a stale `[dictation_stream]` in a real config.toml is harmless. No migration shim needed — but mention removing it in the ADR.

- [ ] **Step 6: Run config tests**

Run: `python -m pytest tests/unit -k config -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/voice_commander/config.py config.toml.example tests/
git commit -m "feat: replace [dictation] endpoint with ws_url + language + idle_timeout

DictationConfig now carries the streaming WebSocket URL, decode language, and
idle timeout. The standalone [dictation_stream] config section is deleted —
its tunables are folded into [dictation] (ADR 0092).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 6 — Web UI

Goal: drop the re-transcribe route + template (no `last.wav` to re-POST); keep `/page/dictation` (last.txt + vocab editor) and `POST /dictation/vocab`. Drop the audio methods from `DictationStore`.

### Task 13: Remove the re-transcribe route and update `/page/dictation`

**Files:**
- Modify: `src/voice_commander/web/app.py`
- Modify: `src/voice_commander/web/templates/page_dictation.html`
- Delete: `src/voice_commander/web/templates/_dictation_result.html`

- [ ] **Step 1: Delete the `/dictation/retranscribe` route**

In `src/voice_commander/web/app.py`, delete the entire `dictation_retranscribe` route function — from the line:

```python
    @app.post("/dictation/retranscribe", response_class=HTMLResponse)
    async def dictation_retranscribe(request: Request) -> HTMLResponse:
```

down to and including its final:

```python
        return HTMLResponse(
            templates.get_template("_dictation_result.html").render(ctx)
        )
```

(Lines ~268–321.)

- [ ] **Step 2: Update the `page_dictation` route docstring**

In `app.py`, the `page_dictation` route docstring says "last dictation + re-transcribe button + vocab editor". Change the first docstring line of `page_dictation`:

```python
        """``GET /page/dictation`` — last dictation + re-transcribe button + vocab editor.
```

to:

```python
        """``GET /page/dictation`` — last dictation text + vocab editor.
```

The `page_dictation` route body itself does not call `read_audio`, so no further change there. Also update the `dictation_vocab_save` docstring line that references the deleted route:

```python
        Returns ``_vocab_result.html`` fragment (same interaction pattern as
        ``/dictation/retranscribe``).
```

→

```python
        Returns the ``_vocab_result.html`` fragment.
```

- [ ] **Step 3: Delete the `_dictation_result.html` template**

```powershell
git rm src/voice_commander/web/templates/_dictation_result.html
```

- [ ] **Step 4: Remove the re-transcribe button from `page_dictation.html`**

In `src/voice_commander/web/templates/page_dictation.html`:

Change the intro paragraph (lines 8–11):

```html
  <p class="text-sm text-neutral-400 mb-4">
    The most recent dictation. Re-transcribe re-sends the saved audio to the
    whisper.cpp endpoint and loads the result onto your clipboard.
  </p>
```

to:

```html
  <p class="text-sm text-neutral-400 mb-4">
    The most recent dictation transcript, and the vocabulary editor that biases
    the streaming whisper decoder.
  </p>
```

Delete the re-transcribe button block (lines 21–24):

```html
  <button hx-post="/dictation/retranscribe" hx-target="#dictation-result" hx-swap="outerHTML"
          class="text-sm px-4 py-2 rounded bg-emerald-900 hover:bg-emerald-800 text-emerald-300 mb-8">
    Re-transcribe
  </button>
```

The `<div id="dictation-result" ...>` block (lines 13–20) that displays `last_text` stays — keep it, but it no longer needs the `id="dictation-result"` hook (it was the hx-swap target). Leave the `id` in place (harmless) or remove it; removing is cleaner — change:

```html
  <div id="dictation-result" class="bg-neutral-900 border border-neutral-800 rounded-lg p-4 mb-4">
```

to:

```html
  <div class="bg-neutral-900 border border-neutral-800 rounded-lg p-4 mb-8">
```

(Note `mb-4` → `mb-8` to keep spacing now that the button below is gone.)

- [ ] **Step 5: Run the web tests (will be updated in Task 14 — confirm no import errors)**

Run: `python -c "from voice_commander.web.app import create_app; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "feat: remove dictation re-transcribe route + button (no last.wav)

Streaming dictation never assembles a single WAV, so audio re-transcribe is
gone. /page/dictation keeps the last-transcript view and the vocab editor;
POST /dictation/vocab is unchanged (ADR 0092).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 14: Drop audio methods from `DictationStore` and update `test_web_dictation.py`

**Files:**
- Modify: `src/voice_commander/dictation/store.py`
- Rewrite: `tests/integration/test_web_dictation.py`
- Modify: `tests/unit/test_dictation_store.py`

- [ ] **Step 1: Drop `save_audio` / `read_audio` / `audio_path` from `DictationStore`**

In `src/voice_commander/dictation/store.py`, update the `DictationStore` class docstring and remove the three audio methods. Replace the whole `DictationStore` class with:

```python
class DictationStore:
    """One-slot on-disk store for the most recent dictation transcript.

    Each new dictation overwrites the previous ``last.txt``. The streaming
    pipeline never assembles a single WAV, so no audio slot exists (ADR 0092).
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def text_path(self) -> Path:
        return self._base / "last.txt"

    def save_text(self, text: str) -> None:
        self.text_path.write_text(text, encoding="utf-8")

    def read_text(self) -> str | None:
        if not self.text_path.exists():
            return None
        return self.text_path.read_text(encoding="utf-8")
```

`encode_wav` (the module-level function above the class) **stays** — `DictationSession.handle_utterance` uses it to encode each streamed chunk.

- [ ] **Step 2: Update `test_dictation_store.py`**

Read `tests/unit/test_dictation_store.py`. Delete every test that exercises `save_audio` / `read_audio` / `audio_path`. Keep `encode_wav` tests and `save_text` / `read_text` / `text_path` tests. Run `python -m pytest tests/unit/test_dictation_store.py -v` — Expected: remaining tests PASS.

- [ ] **Step 3: Rewrite `test_web_dictation.py`**

Replace the full contents of `tests/integration/test_web_dictation.py` with:

```python
"""Integration tests for /page/dictation and /dictation/vocab (ADR 0092).

The re-transcribe route is gone (no last.wav in the streaming pipeline);
these tests cover the last-transcript view and the vocab editor save.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app

HX = {"HX-Request": "true"}


def _make_client(tmp_path: Path) -> TestClient:
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
    """TestClient with cwd = tmp_path; routes use Path('outputs/dictation')."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[hotkey]\nkey = "scroll_lock"\n', encoding="utf-8"
    )
    dictation_dir = tmp_path / "outputs" / "dictation"
    dictation_dir.mkdir(parents=True)
    (dictation_dir / "last.txt").write_text("prior dictation", encoding="utf-8")
    return _make_client(tmp_path)


def test_page_dictation_renders_last_text(app_client: TestClient) -> None:
    """GET /page/dictation shows the text from outputs/dictation/last.txt."""
    resp = app_client.get("/page/dictation")
    assert resp.status_code == 200
    assert "prior dictation" in resp.text


def test_page_dictation_no_retranscribe_button(app_client: TestClient) -> None:
    """The re-transcribe button + route are gone (ADR 0092)."""
    resp = app_client.get("/page/dictation")
    assert resp.status_code == 200
    assert "/dictation/retranscribe" not in resp.text
    assert "Re-transcribe" not in resp.text


def test_retranscribe_route_is_removed(app_client: TestClient) -> None:
    """POST /dictation/retranscribe returns 404 — the route no longer exists."""
    resp = app_client.post("/dictation/retranscribe", headers=HX)
    assert resp.status_code == 404


def test_vocab_save_persists_vocab_json(app_client: TestClient) -> None:
    """POST /dictation/vocab writes outputs/dictation/vocab.json."""
    resp = app_client.post(
        "/dictation/vocab",
        headers=HX,
        data={
            "vocab": "Supabase\nPostgres",
            "corrections": json.dumps([{"wrong": "supa base", "right": "Supabase"}]),
            "commands": json.dumps([{"phrase": "new line", "action": "newline"}]),
        },
    )
    assert resp.status_code == 200
    saved = json.loads(
        (
            Path("outputs") / "dictation" / "vocab.json"
        ).read_text(encoding="utf-8")
    )
    assert saved["vocab"] == ["Supabase", "Postgres"]
    assert saved["corrections"] == [{"wrong": "supa base", "right": "Supabase"}]
    assert saved["commands"] == [{"phrase": "new line", "action": "newline"}]
```

- [ ] **Step 4: Run the web tests**

Run: `python -m pytest tests/integration/test_web_dictation.py tests/unit/test_dictation_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add -A
git commit -m "feat: drop audio slot from DictationStore; rewrite web dictation tests

DictationStore no longer has save_audio/read_audio/audio_path — streaming
dictation has no single WAV. test_web_dictation drops the retranscribe
assertions and verifies the route is 404 (ADR 0092).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 7 — Delete dead code

Goal: remove the experiment package remainder, `remote.py`, the standalone script entry, dead scripts, and dead tests. After this phase the full suite is green with **no ignores**.

### Task 15: Delete the `dictation_stream/` package remainder

**Files:**
- Delete: `src/voice_commander/dictation_stream/` (entire directory)

- [ ] **Step 1: Confirm no remaining imports of `dictation_stream`**

Run: `python -c "import pathlib; hits=[str(p) for p in pathlib.Path('src').rglob('*.py') if 'dictation_stream' in p.read_text()] + [str(p) for p in pathlib.Path('tests').rglob('*.py') if 'dictation_stream' in p.read_text()]; print('\n'.join(hits) or 'NONE')"`

Expected: `NONE`. If any file is listed, it is a test slated for deletion in Task 17 — verify it is on that list; if a *source* file imports `dictation_stream`, stop and fix it first.

- [ ] **Step 2: Delete the package directory**

```powershell
git rm -r src/voice_commander/dictation_stream
Remove-Item -Recurse -Force src/voice_commander/dictation_stream -ErrorAction SilentlyContinue
```

- [ ] **Step 3: Verify imports still resolve**

Run: `python -c "import voice_commander.daemon; import voice_commander.dictation.session; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```powershell
git add -A
git commit -m "chore: delete the dictation_stream experiment package

ws_client, local_agreement, and bridge were moved into the dictation package
(Phase 1); the mic-owning remainder (capture, chunker, config, session, sink,
__main__, __init__) is dead (ADR 0092).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 16: Delete `remote.py` and the standalone script entry

**Files:**
- Delete: `src/voice_commander/dictation/remote.py`
- Modify: `pyproject.toml`
- Delete: `scripts/dictation_stream_e2e.py`
- Delete: `scripts/dictation_remote_smoke.py`

- [ ] **Step 1: Confirm no remaining imports of `dictation.remote`**

Run: `python -c "import pathlib; hits=[str(p) for p in pathlib.Path('src').rglob('*.py') if 'import remote' in p.read_text() or 'dictation.remote' in p.read_text() or 'from .remote' in p.read_text()] + [str(p) for p in pathlib.Path('tests').rglob('*.py') if 'dictation.remote' in p.read_text()]; print('\n'.join(hits) or 'NONE')"`

Expected: only `test_dictation_remote.py` / `test_dictation_remote_timeout.py` (deleted in Task 17). No source file. If a source file appears, fix it.

- [ ] **Step 2: Delete `remote.py`**

```powershell
git rm src/voice_commander/dictation/remote.py
```

- [ ] **Step 3: Remove the `voice-dictation-stream` script entry from `pyproject.toml`**

In `pyproject.toml`, in the `[project.scripts]` table, delete the line:

```toml
voice-dictation-stream = "voice_commander.dictation_stream.__main__:main"
```

- [ ] **Step 4: Delete the standalone + remote-smoke scripts**

```powershell
git rm scripts/dictation_stream_e2e.py scripts/dictation_remote_smoke.py
```

> `scripts/dictation_remote_smoke.py` exercises the deleted batch `/inference` POST — it is dead. The other dictation E2E scripts (`dictation_hotkey_cancel_e2e.py`, `dictation_hotkey_end_e2e.py`, `dictation_opens_session_e2e.py`, `dictation_visual_e2e.py`, `dictation_vocab_e2e.py`, `dictation_cancel_smoke.py`) drive the daemon's *lifecycle* (hotkeys, sprite, session open) which is unchanged — they are **kept**. If any of them imports `remote` or `dictation_stream`, that import must be fixed; check in Step 5.

- [ ] **Step 5: Confirm the kept E2E scripts have no dead imports**

Run: `python -c "import pathlib; hits=[str(p) for p in pathlib.Path('scripts').rglob('*.py') if 'dictation_stream' in p.read_text() or 'dictation.remote' in p.read_text() or 'save_audio' in p.read_text() or 'read_audio' in p.read_text()]; print('\n'.join(hits) or 'NONE')"`

Expected: `NONE`. If a kept script references a deleted symbol, fix that script: replace any `remote`/audio usage with the streaming equivalent, or — if the script is purely lifecycle and the reference is incidental — remove the dead line. Document any such fix in the commit message.

- [ ] **Step 6: Verify the package imports**

Run: `python -c "import voice_commander.dictation; import voice_commander.daemon; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```powershell
git add -A
git commit -m "chore: delete batch remote.py, voice-dictation-stream entry, dead scripts

The batch /inference POST client and the standalone streaming runner are
removed; dictation is streaming-only (ADR 0092).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 17: Delete dead test files

**Files:**
- Delete: `tests/unit/test_stream_capture.py`, `test_stream_chunker.py`, `test_stream_config.py`, `test_stream_sink.py`, `test_stream_toggle.py`
- Delete: `tests/integration/test_stream_session.py`
- Delete: `tests/unit/test_dictation_remote.py`, `test_dictation_remote_timeout.py`

- [ ] **Step 1: Delete the dead test files**

```powershell
git rm tests/unit/test_stream_capture.py tests/unit/test_stream_chunker.py tests/unit/test_stream_config.py tests/unit/test_stream_sink.py tests/unit/test_stream_toggle.py tests/integration/test_stream_session.py tests/unit/test_dictation_remote.py tests/unit/test_dictation_remote_timeout.py
```

- [ ] **Step 2: Delete stale pycache for the removed tests**

```powershell
Get-ChildItem -Recurse -Path tests -Include __pycache__ -Directory | Remove-Item -Recurse -Force
```

- [ ] **Step 3: Run the FULL test suite — no ignores**

Run: `python -m pytest -q`
Expected: **all tests PASS, zero failures, zero errors, zero collection errors.** This is the Phase 7 gate. If anything fails, fix it before committing.

- [ ] **Step 4: Commit**

```powershell
git add -A
git commit -m "test: delete tests for removed dictation_stream + batch remote modules

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 8 — Documentation

Goal: file ADR 0092, refresh `CLAUDE.md`, `technical-decisions.md`, `libraries.md`, `dictation-streaming.md`, and add the new human-validated E2E harness.

### Task 18: Write ADR 0092

**Files:**
- Create: `docs/decisions/0092-streaming-dictation-integration.md`

- [ ] **Step 1: Read two recent ADRs for the house format**

Read `docs/decisions/0091-streaming-dictation-experiment.md` and `docs/decisions/0090-dictation-hotkey-opens-session.md` to match heading structure, status-line format, and cross-reference style.

- [ ] **Step 2: Write the ADR**

Create `docs/decisions/0092-streaming-dictation-integration.md`. It must cover, in the house ADR format:

- **Title:** `ADR 0092 — Streaming dictation integration (batch path removed)`
- **Status:** `Accepted`, dated `2026-05-18`.
- **Supersedes/amends line:** supersedes the transcription path of ADR 0086; amends ADR 0088 (post-processing now applies to the streamed transcript); supersedes ADR 0090's transport (lifecycle wiring unchanged); promotes ADR 0091 from isolated experiment to shipped code.
- **Context:** the batch pipeline (buffer whole utterance → HTTP POST to `/inference` → paste) is replaced by streaming each VAD utterance over a WebSocket to `/ws/transcribe`, with `LocalAgreement` stabilising words across chunk boundaries.
- **Decision:** Approach A — `DictationSession` reworked to own the WebSocket transport (asyncio-loop thread + `bridge.pump` + `stream_transcribe` + lock-guarded `LocalAgreement`); the daemon's dictation *lifecycle* wiring (`on_dictation_toggle`, bare-`dictate`, `_process_utterance` dictation branch, `_DICTATION_WAKE` sentinel, `_finalize_pending_dictation_end`, `_end_owned_session_if_needed`, close-before-finalize ordering) is unchanged; `_finalize_dictation` shrinks to `session.finish()` → corrections → commands → paste → save → publish.
- **Module changes:** `ws_client`/`local_agreement`/`bridge` moved into `dictation/`; `dictation_stream/` deleted; `remote.py` deleted; `voice-dictation-stream` script entry deleted.
- **Config:** `[dictation]` `endpoint` → `ws_url`; new `language` + `idle_timeout_seconds`; `[dictation_stream]` section deleted (a stale `[dictation_stream]` in a user's real `config.toml` is harmless — the loader ignores unknown tables).
- **Web:** re-transcribe route + `_dictation_result.html` removed; `last.txt` + vocab editor kept; `DictationStore` audio methods dropped.
- **The confirmed WS config prompt field name** (from Phase 2 / Task 5) — state it explicitly: `<CONFIRMED_NAME>`.
- **Error handling:** reproduce the spec's error table (connect fail / mid-session drop / encode fail / clipboard fail / spoken cancel) and the preserved events (`dictation.start/end/error/result`, `transcript`).
- **Consequences:** real progressive transcription, lower perceived latency, no batch fallback (fail-clean), `last.wav` re-transcribe lost.
- **Alternatives considered:** Approach B (daemon drives `StreamSession` — rejected, double VAD) and Approach C (buffer then stream at end — rejected, not real streaming).

- [ ] **Step 3: Commit**

```powershell
git add docs/decisions/0092-streaming-dictation-integration.md
git commit -m "docs: ADR 0092 — streaming dictation integration, batch path removed

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 19: Refresh `CLAUDE.md`, `technical-decisions.md`, `libraries.md`, `dictation-streaming.md`

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/agents/technical-decisions.md`
- Modify: `docs/libraries.md`
- Modify: `docs/dictation-streaming.md`

- [ ] **Step 1: Update the `CLAUDE.md` Current state section**

In `CLAUDE.md`, the **Current state** paragraph describes dictation as: "the concatenated audio is encoded to a 16 kHz mono WAV and POSTed to a remote whisper.cpp `/inference` endpoint ... `outputs/dictation/last.wav` + `last.txt` are retained for web re-transcribe". Rewrite the dictation portion to describe the streaming path:

- Each VAD utterance is encoded to a WAV chunk and streamed immediately over a WebSocket to `/ws/transcribe` (`[dictation] ws_url`); `LocalAgreement` stabilises words across chunk boundaries; on normal exit `DictationSession.finish()` returns the stabilised transcript, which is post-processed (`apply_corrections` → `apply_commands`) and pasted via clipboard.
- `outputs/dictation/last.txt` is retained (no `last.wav`; web re-transcribe removed).
- `DictationSession` owns the WebSocket transport on an asyncio-loop thread (ADR 0091 Task-9 threading pattern); the daemon's dictation lifecycle wiring is unchanged.
- Reference ADR 0092 alongside the existing ADR 0086/0088/0089/0090 dictation references.

Keep the edit surgical — only the dictation sentences change; the picker/elements/graph prose is untouched.

- [ ] **Step 2: Add the ADR 0092 row to `technical-decisions.md`**

In `docs/agents/technical-decisions.md`, add a one-line summary row for ADR 0092 in the same table/list format the file uses (read the file first to match the format). Summary: "Streaming dictation integration — `DictationSession` streams each VAD utterance to a whisper WebSocket; batch `/inference` path removed."

If the file has a row for ADR 0086/0088 mentioning batch transcription, append a note that ADR 0092 superseded the transcription path (do not delete the historical row).

- [ ] **Step 3: Update `docs/libraries.md` — the `websockets` entry**

In `docs/libraries.md`, find the `websockets` dependency entry. It currently frames `websockets` as part of the streaming *experiment* (ADR 0091). Update the rationale to say `websockets` is now a shipped runtime dependency: it powers `DictationSession`'s streaming transcription transport (`ws_client.stream_transcribe`) and the in-process mock WebSocket server used by the dictation integration tests. Remove any "experiment" qualifier.

- [ ] **Step 4: Update `docs/dictation-streaming.md`**

`docs/dictation-streaming.md` currently documents the ADR 0091 standalone experiment. Rewrite it to document the **shipped** streaming dictation path:
- Overview: dictation is a voice-session sub-state; each VAD utterance is streamed to `/ws/transcribe`.
- Architecture: `DictationSession` (asyncio-loop thread, `bridge.pump`, `stream_transcribe`, lock-guarded `LocalAgreement`), the daemon's `_finalize_dictation`, the `[dictation]` config keys.
- Remove all references to `MicCapture`/`Chunker`/`StreamSession`/`TextSink`/`python -m voice_commander.dictation_stream` (deleted).
- Cross-reference ADR 0092.

If the doc is small and entirely experiment-specific, a full rewrite is fine. Keep `docs/index.md`'s link to it valid (the filename is unchanged).

- [ ] **Step 5: Commit**

```powershell
git add CLAUDE.md docs/agents/technical-decisions.md docs/libraries.md docs/dictation-streaming.md
git commit -m "docs: refresh CLAUDE.md, technical-decisions, libraries, dictation-streaming

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 20: Add the streaming E2E harness

**Files:**
- Create: `scripts/dictation_streaming_e2e.py`

This is the spec's required human-validated E2E harness — real mic via the daemon, real `/ws/transcribe` server. It replaces the deleted `scripts/dictation_stream_e2e.py`.

- [ ] **Step 1: Read the visual-E2E protocol and a reference harness**

Read `docs/agents/visual-e2e-testing.md` (mandatory per `CLAUDE.md`) and read `scripts/dictation_hotkey_end_e2e.py` as the closest existing dictation lifecycle harness to copy structure from.

- [ ] **Step 2: Write the harness**

Create `scripts/dictation_streaming_e2e.py`. It must:
- Be a human-validated harness (not a pytest test). Print clear step-by-step prompts to the operator.
- Build a real daemon (`build_streaming_daemon` or the minimal subset) wired to the real `[dictation] ws_url` from `config.toml`. The server at `192.168.4.200:8765` must be reachable.
- Drive the streaming dictation flow the way a real user would: open a session (or simulate the Right Ctrl `on_dictation_toggle`), prompt the operator to speak a known sentence, then end with the end-word "done" or a second hotkey press.
- Capture evidence: the pasted transcript text, the sequence of `dictation.*` + `transcript` events from the `EventBus`, and the daemon log.
- Assert on that evidence: a non-empty transcript was produced, `dictation.start` then `dictation.end {reason:"done"}` then `dictation.result` were published in order, and the pasted text matches the operator-confirmed spoken sentence (fuzzy/substring match is acceptable — whisper is not deterministic).
- Print a clear PASS/FAIL summary and exit non-zero on failure.
- Include a docstring stating it requires the live `/ws/transcribe` server and a working microphone, and is run manually.

Follow the eight rules in `docs/agents/visual-e2e-testing.md`. Mirror the evidence-capture + assertion structure of `scripts/dictation_hotkey_end_e2e.py`.

- [ ] **Step 3: Smoke-check the script parses and imports**

Run: `python -c "import ast; ast.parse(open('scripts/dictation_streaming_e2e.py', encoding='utf-8').read()); print('parse ok')"`
Expected: prints `parse ok`.

> The harness itself is human-run against live hardware — do not attempt to execute its full flow in CI. A parse/import check is the automated gate; a human runs it for real validation at the phase boundary.

- [ ] **Step 4: Commit**

```powershell
git add scripts/dictation_streaming_e2e.py
git commit -m "test: add human-validated streaming dictation E2E harness

Replaces the deleted standalone dictation_stream_e2e.py; drives the daemon's
streaming dictation against the live /ws/transcribe server (ADR 0092).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 21: Final full-suite gate + cleanup sweep

**Files:** (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -q`
Expected: all tests PASS, zero failures/errors/collection errors.

- [ ] **Step 2: Grep for any leftover references to deleted symbols**

Run: `python -c "import pathlib; bad=['dictation_stream','dictation.remote','post_audio','save_audio','read_audio','take_and_finish','take_audio','dictation_endpoint','retranscribe','_dictation_result']; hits=[(str(p),t) for p in list(pathlib.Path('src').rglob('*.py'))+list(pathlib.Path('docs').rglob('*.md'))+list(pathlib.Path('scripts').rglob('*.py')) for t in bad if t in p.read_text(encoding='utf-8',errors='ignore')]; print('\n'.join(f'{p}: {t}' for p,t in hits) or 'CLEAN')"`

Expected: `CLEAN`. Any hit in a *non-historical* file (i.e. not an old ADR that legitimately references the batch path as superseded history) must be fixed. Old ADRs 0086/0088/0090/0091 mentioning the batch path are fine — they are historical record.

- [ ] **Step 3: Verify the daemon builds end-to-end (import-level)**

Run: `python -c "import voice_commander.daemon; import voice_commander.web.app; import voice_commander.config; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit any cleanup**

If Step 2 surfaced fixes:

```powershell
git add -A
git commit -m "chore: clean up leftover references to removed batch dictation symbols

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If nothing needed fixing, skip the commit.

---

## Plan summary

**Total tasks:** 21, across 8 phases.

| Phase | Tasks | Outcome |
|---|---|---|
| 1 — Move transport | 1–4 | `ws_client`/`local_agreement`/`bridge` in `dictation/`; `prompt` config field added; transport tests green |
| 2 — Verify WS field | 5 | Server's config prompt field name confirmed (provisionally `initial_prompt`) |
| 3 — Rework session | 6–8 | Streaming `DictationSession`; session unit + integration tests green |
| 4 — Rewire daemon | 9–11 | `_finalize_dictation` shrunk; constructor params swapped; daemon dictation tests green; full suite green (minus Phase 6/7 stale files) |
| 5 — Config | 12 | `DictationConfig` + `config.toml.example` updated; `[dictation_stream]` deleted (executed between Task 10 steps 5 and 6) |
| 6 — Web UI | 13–14 | Re-transcribe route/template/button removed; `DictationStore` audio methods dropped; web tests green |
| 7 — Delete dead code | 15–17 | `dictation_stream/`, `remote.py`, script entry, dead scripts + tests deleted; full suite green, no ignores |
| 8 — Docs | 18–21 | ADR 0092; `CLAUDE.md`/`technical-decisions`/`libraries`/`dictation-streaming` refreshed; streaming E2E harness; final gate |

**Critical execution-ordering note:** Phases 3 and 4 must be reviewed as a pair (the daemon does not run between Task 8 and Task 11). Phase 5 (Task 12) must be executed *between* Task 10's Steps 5 and 6 because `build_streaming_daemon` references the new config fields. Recommended task order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10(steps 1–5) → 12 → 10(steps 6–9) → 11 → 13 → … → 21.
