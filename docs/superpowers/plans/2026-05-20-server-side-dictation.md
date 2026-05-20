# Server-Side Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all client-side transcript assembly (LocalAgreement, DictationWindow, frame tap, WAV-per-window streaming) and replace it with a thin raw-PCM transport that accumulates on the server. The daemon becomes a thin transport + UI coordinator; the server does one Whisper decode per dictation on the final accumulated audio.

**Architecture:** The daemon streams raw 16 kHz mono float32 PCM bytes over WebSocket binary frames (no WAV headers, no config frame). On end-word / hotkey-end / spoken cancel the daemon stops sending, sends `{"type":"end"}` (or closes the connection on cancel), publishes `dictation.processing`, and awaits `{"type":"done","text":...,"raw":...}`. The server accumulates raw float32 bytes, calls `faster_whisper.WhisperModel.transcribe(audio, vad_filter=True)` once on the whole buffer, LLM-cleans the result, and returns the `done` frame. Client-side: no LocalAgreement, no DictationWindow, no frame tap, no growing-window WAVs.

**Tech Stack:** Python 3.12, `numpy`, `websockets` (existing), `pytest`. Windows-only daemon. Server work is a parallel branch (VPS engineer). All daemon phases below are testable against a mock server.

**ADR:** [ADR 0096](../../decisions/0096-server-side-dictation.md) — locked decisions for this plan.

---

## Server contract — precondition / assumption

This plan covers the **voice-commander daemon side only**. The server is implemented in a parallel branch by the VPS engineer. Phases 0–5 (unit + integration tests) run against local mock servers and do NOT block on the server being ready. Phase 7 (live cutover) requires the upgraded server.

**New server contract (raw PCM protocol, ADR 0096 D1–D2):**

- Binary frames: raw 16 kHz mono float32 PCM bytes — no WAV header per message.
- End signal: `{"type":"end"}` JSON frame from the daemon.
- Server accumulates raw bytes into a growing buffer. On end: `np.frombuffer(buf, np.float32)`, `transcribe(audio, vad_filter=True)` once, `clean_transcript(raw)`, send `{"type":"done","text":<cleaned>,"raw":<raw>}`, close.
- No per-message decode. No `partial` frames. No `segments`. No `context`. No config handshake.
- Server max-dictation cap: 600 s → `{"type":"error","message":"max duration exceeded"}` + close.
- Empty/silence: `{"type":"done","text":"","raw":""}`.

**Dropped from old protocol (no longer sent by daemon or expected from server):**

- `{"type":"config",...}` handshake frame.
- `{"type":"partial",...}` frames.
- `segments` field on any frame.
- `raw_transcript` field on the `end` frame.
- `initial_prompt` field.

---

## File structure

**Files deleted (entire modules):**

| File | Reason |
|------|--------|
| `src/voice_commander/dictation/local_agreement.py` | Client-side stabiliser — gone |
| `src/voice_commander/dictation/window.py` | Growing-window buffer — gone |
| `tests/unit/test_dictation_local_agreement.py` | Tests deleted code |
| `tests/unit/test_dictation_window.py` | Tests deleted code |
| `tests/unit/test_streaming_recorder_frame_tap.py` | Tests deleted frame tap |

**Files modified:**

| File | Change |
|------|--------|
| `src/voice_commander/dictation/ws_client.py` | Full rewrite: raw PCM, no config, no partials, no segments, no `on_partial`, no `prompt`, no `raw_transcript_fn` |
| `src/voice_commander/dictation/session.py` | Simplify: drop frame-tap fields/methods, drop LocalAgreement/DictationWindow, drop `_on_frame`/`_on_partial`/`_agreement`/`_confirmed`/`set_recorder`; audio path → PCM bytes onto chunk_q per VAD utterance |
| `src/voice_commander/streaming_recorder.py` | Remove `set_frame_tap`, `_invoke_frame_tap`, `_frame_tap` field, and `_invoke_frame_tap` call in `_vad_loop` |
| `src/voice_commander/config.py` | `DictationConfig`: drop `language`, `window_step_ms`, `window_cap_ms`; add `max_dictation_s` (default 300) |
| `config.toml.example` | Drop `language`, `window_step_ms`, `window_cap_ms`; add `max_dictation_s` with comment |
| `src/voice_commander/daemon.py` | Remove `set_recorder` call, `window_step_ms`/`window_cap_ms` pass-through, `language` pass-through; add `max_dictation_s` pass-through; publish `dictation.processing` event |
| `src/voice_commander/voice_sprite/__main__.py` | Add `processing` state to sprite state machine |
| `tests/integration/_dictation_ws.py` | Rewrite mock server: accumulate binary frames, no partial replies, send done on `end` frame |
| `tests/integration/test_dictation_streaming_session.py` | Restructure for new protocol |
| `docs/dictation-streaming.md` | Rewrite transport/architecture sections |
| `docs/transcription-pipeline.md` | Update §4 dictation path |
| `docs/agents/technical-decisions.md` | Add ADR 0096 row; mark 0091/0092/0095 superseded |
| `CLAUDE.md` | Update "Current state" dictation paragraph |

---

## Phases and gates

Each phase ends in **(A)** an automated test gate and **(B)** a human validation gate. Do not start phase N+1 until phase N's gates are both green.

| Phase | Gate A (automated) | Gate B (human) |
|-------|--------------------|----------------|
| 0 — Pre-flight | Hand-crafted PCM smoke test passes | Sakib confirms VPS engineer progress |
| 1 — `ws_client` rewrite | `pytest tests/unit/test_dictation_ws_client.py tests/integration/test_dictation_ws_client.py` | Sakib reviews new signature |
| 2 — `DictationSession` simplification | `pytest tests/unit/test_dictation_session.py tests/integration/test_dictation_streaming_session.py` | Sakib reviews deleted code list |
| 3 — Sprite processing state | `pytest tests/unit/` | Sakib confirms sprite shows "processing" badge |
| 4 — Dead code removal | `pytest tests/unit/` all green, no import of deleted modules | Sakib confirms no regression in command mode |
| 5 — Tests cleanup + mock server | Full `pytest tests/` green | Sakib reviews mock server |
| 6 — Docs sweep | `pytest tests/` clean | Sakib confirms CLAUDE.md is accurate |
| 7 — Live cutover | Manual mic test | Sakib at mic, all five exit paths confirmed |

---

# Phase 0 — Pre-flight verification

**Goal:** Confirm the server speaks the new protocol before touching daemon code. Catch protocol mismatches early against a real server (not just mocks).

### Task 0.1: Confirm VPS engineer progress

- [ ] **Step 1:** Message the VPS engineer. Confirm the new server is implemented
  (or has a known ETA) and is accepting connections at `ws://192.168.4.200:8767/ws/transcribe`
  under the new raw-PCM protocol.
- [ ] **Step 2:** Agree on a cutover date / branch name so daemon and server land together.

**Gate:** Engineer confirms server branch status (timing; does not block Phases 1–5).

### Task 0.2: Smoke-test the new server from a Python REPL

Once the server branch is deployed to the VPS, run this from a local REPL before touching daemon code:

```python
import asyncio, numpy as np
from websockets.asyncio.client import connect

async def smoke():
    audio = np.zeros(16000 * 3, dtype=np.float32)  # 3 s silence
    async with connect("ws://192.168.4.200:8767/ws/transcribe") as ws:
        # Send raw PCM in one shot (no config, no WAV header)
        await ws.send(audio.tobytes())
        # Send end frame
        import json
        await ws.send(json.dumps({"type": "end"}))
        # Await done
        import asyncio as aio
        frame = json.loads(await aio.wait_for(ws.recv(), timeout=60))
        print("frame:", frame)
        assert frame.get("type") == "done", f"unexpected: {frame}"
        print("PASS — done.text:", repr(frame.get("text")))

asyncio.run(smoke())
```

- [ ] **Step 1:** Run the smoke test. Confirm `done` frame arrives with `text` field.
- [ ] **Step 2:** Repeat with ~5 s of real microphone audio (record via `sounddevice`
  and play it back). Confirm transcribed text is plausible.
- [ ] **Step 3:** Confirm no `partial` frames arrive (i.e. `ws.recv()` with a short
  timeout before the `end` frame returns nothing).

**Gate:** Smoke test returns `{"type":"done","text":"...","raw":"..."}` for silence and real audio. No `partial` frames. This gate does NOT block Phases 1–5 (those use mocks); it blocks Phase 7 only.

---

# Phase 1 — `ws_client.py` rewrite

**Goal:** Replace the old `stream_transcribe` (config frame, WAV chunks, partial routing, segments parsing, `raw_transcript_fn`) with a minimal raw-PCM sender.

### Task 1.1: Write the new `ws_client.stream_transcribe` (TDD)

**Files:**
- Modify: `src/voice_commander/dictation/ws_client.py`
- Test: `tests/unit/test_dictation_ws_client.py` (rewrite)
- Test: `tests/integration/test_dictation_ws_client.py` (update or create)

**New signature (ADR 0096 D8):**

```python
async def stream_transcribe(
    ws_url: str,
    chunk_q: asyncio.Queue[bytes | None],
    cap_timeout_s: float = 300.0,
    done_timeout_s: float = 60.0,
) -> str | None:
```

**Behaviour:**
- Open WebSocket to `ws_url`. No config frame sent.
- Loop: `await asyncio.wait_for(chunk_q.get(), timeout=cap_timeout_s)`.
  - On END sentinel (`None`): break the upload loop, proceed to end-frame send.
  - On timeout: log warning, break the upload loop (cap hit path).
  - On PCM bytes: `await ws.send(chunk)` as binary. If `ConnectionClosed`: log, return `None`.
- Send `{"type":"end"}` JSON frame. If `ConnectionClosed` before send: return `None`.
- Read until `done`/`error`/`done_timeout_s` expiry/`ConnectionClosed`:
  - `done` frame → return `frame.get("text", "")`.
  - `error` frame → log error, return `None`.
  - `asyncio.TimeoutError` → log warning, return `None`.
  - `ConnectionClosed` → log warning, return `None`.
- Any frame type other than `done`/`error` while waiting: log and discard (defensive).
- Propagate connect-time `OSError`/`InvalidURI`/`InvalidHandshake` to caller.

**What is NOT in the new function:**
- No `json.loads(await ws.recv())` in the upload loop (no per-chunk reply).
- No `on_partial` callback.
- No config frame.
- No `language`, `prompt`, `raw_transcript_fn` parameters.
- No segments parsing.

- [ ] **Step 1: Write failing unit tests**

In `tests/unit/test_dictation_ws_client.py`, cover:

```
test_sends_raw_binary_frames
test_sends_end_frame_after_sentinel
test_returns_done_text_on_done_frame
test_returns_none_on_error_frame
test_returns_none_on_done_timeout
test_returns_none_on_connection_closed_during_done_wait
test_returns_none_on_connection_closed_during_send
test_returns_empty_string_on_done_with_empty_text
test_cap_timeout_sends_end_frame_then_awaits_done
test_no_config_frame_sent
```

- [ ] **Step 2:** Run `pytest tests/unit/test_dictation_ws_client.py -v`. All tests must FAIL (old implementation doesn't match new contract).

- [ ] **Step 3: Rewrite `ws_client.py`**

Replace the entire module body. Keep only what is still needed: the `END: None = None` sentinel, the `connect` import, the `stream_transcribe` function. Remove all references to `on_partial`, `prompt`, `raw_transcript_fn`, `language`, config-frame send, per-chunk `ws.recv()`, segments parsing, stray-partial forwarding.

Update the module docstring to describe the new raw-PCM protocol.

- [ ] **Step 4:** Run `pytest tests/unit/test_dictation_ws_client.py -v`. All tests must PASS.

- [ ] **Step 5:** Run `pytest tests/` to confirm no regressions.

**Gate A:** `pytest tests/unit/test_dictation_ws_client.py` all green.
**Gate B:** Sakib reviews the new function signature and confirms it matches ADR 0096 D8.

---

# Phase 2 — `DictationSession` simplification

**Goal:** Strip out all LocalAgreement, DictationWindow, frame-tap, and partial-routing code. The session becomes: `start` opens WS thread + chunk_q; `handle_utterance` classifies and enqueues raw PCM bytes; `finish` sends end sentinel and awaits done text; `cancel` closes immediately.

### Task 2.1: Write failing tests for simplified session (TDD)

**Files:**
- Modify: `tests/unit/test_dictation_session.py` (rewrite test cases)
- Modify: `tests/integration/test_dictation_streaming_session.py` (restructure)

New test cases:
```
test_handle_utterance_enqueues_pcm_bytes_on_chunk_q
test_handle_utterance_classifies_end_word
test_handle_utterance_classifies_cancel_word
test_finish_sends_end_sentinel_joins_thread_returns_done_text
test_finish_returns_empty_string_on_no_done_frame (None fallback)
test_cancel_closes_without_end_frame
test_start_does_not_send_config_frame
test_session_has_no_on_partial_method
test_session_has_no_set_recorder_method
test_session_has_no_agreement_attribute
```

- [ ] **Step 1:** Write the failing tests. Run `pytest tests/unit/test_dictation_session.py -v`. Tests for deleted methods/attributes must FAIL with `AttributeError`.

### Task 2.2: Rewrite `DictationSession`

**Files:**
- Modify: `src/voice_commander/dictation/session.py`

**Fields to remove (entire):**
- `_agreement: LocalAgreement`
- `_agreement_lock: threading.Lock`
- `_confirmed: list[str]`
- `_vocab` (retain only for post-processing in daemon — move vocab to daemon if simpler, or keep a thin snapshot for `start()`)
- `_recorder: Any | None`
- `_window: DictationWindow | None`
- `_warned_no_segments: bool`
- `_language: str`
- `_window_step_ms: int`
- `_window_cap_ms: int`

**Constructor parameters to remove:**
- `language`, `window_step_ms`, `window_cap_ms`

**Constructor parameters to add:**
- `max_dictation_s: float = 300.0` — passed through to `stream_transcribe` as `cap_timeout_s`

**Methods to remove entirely:**
- `set_recorder()`
- `_on_frame()`
- `_on_partial()`
- `_build_raw_transcript()`
- `_segments_to_timed_words()` (module-level helper)

**`handle_utterance` — new body:**
```python
def handle_utterance(self, audio: npt.NDArray[np.float32], text: str) -> UtteranceKind:
    with self._lock:
        if not self._active:
            return "buffered"
        normalized = _normalize_spoken(text)
        if normalized == self._end_word:
            return "end"
        if self._cancel_word is not None and normalized == self._cancel_word:
            return "cancel"
        chunk_q = self._chunk_q
    # Active, non-end, non-cancel: stream the PCM bytes.
    if chunk_q is not None:
        chunk_q.put(audio.tobytes())
    return "buffered"
```

**`start()` — changes:**
- Remove: `self._agreement = LocalAgreement()`, `self._confirmed = []`, `self._warned_no_segments = False`, `DictationWindow(...)` construction, `self._recorder.set_frame_tap(...)`.
- Remove: `prompt = build_prompt(vocab)` and pass-through to `_run_asyncio` (no prompt on wire).
- `_run_asyncio` thread now takes only `chunk_q` (no `prompt`).

**`finish()` — changes:**
- Remove: `self._recorder.set_frame_tap(None)`, `window.flush()`, `window` handling.
- Remove: `LocalAgreement.finalize()` call and `fallback_text` construction.
- After `loop_thread.join()`: if `self._final_text is not None`, return it; else return `""`.
- Remove: the `_final_text is None` fallback log ("no done frame — falling back to LocalAgreement").
- Add log: "no done frame received — returning empty string".

**`cancel()` — changes:**
- Remove: `self._recorder.set_frame_tap(None)`, `self._window = None`, `LocalAgreement()` reset.

**`_async_main()` — changes:**
- Remove `prompt` param and `_on_partial` arg to `stream_transcribe`.
- Call: `self._final_text = await stream_transcribe(self._ws_url, async_q, cap_timeout_s=self._max_dictation_s)`.

**Imports to remove from `session.py`:**
- `from .local_agreement import LocalAgreement, TimedWord`
- `from .window import DictationWindow`
- Remove `build_prompt` import if vocab is no longer used in session (check if daemon handles it).

- [ ] **Step 2: Implement the simplified session**. Edit `session.py` per the spec above.

- [ ] **Step 3:** Run `pytest tests/unit/test_dictation_session.py -v`. All new tests must PASS.

- [ ] **Step 4:** Run `pytest tests/integration/test_dictation_streaming_session.py -v`. All tests must PASS.

- [ ] **Step 5:** Run `pytest tests/` to confirm no regressions outside dictation.

**Gate A:** `pytest tests/unit/test_dictation_session.py tests/integration/test_dictation_streaming_session.py` all green.
**Gate B:** Sakib reviews the deleted field/method list and confirms it matches the ADR 0096 D7 deletion targets.

---

# Phase 3 — Sprite "processing" state + HUD-quiet dictation

**Goal:** Publish a new `dictation.processing` SSE event when the end frame is sent. Add a `processing` state to the sprite state machine. Stop emitting `transcript` events during dictation (there are no partials to show).

### Task 3.1: Publish `dictation.processing` from the daemon

**Files:**
- Modify: `src/voice_commander/daemon.py`

The `dictation.processing` event must be published by the daemon's dictation finalization path — specifically on the `_finalize_dictation` call that runs on `_dictation_executor` — **after `session.finish()` begins its end-sentinel handoff but before `loop_thread.join()` returns**. Since `finish()` is a blocking call that includes the join, the cleanest wiring is: **publish `dictation.processing` immediately before calling `session.finish()`**, in `_finalize_dictation`.

Locate `_finalize_dictation` in `daemon.py`. Before the `text = session.finish()` call, add:

```python
self._bus.publish("dictation.processing", {})
```

- [ ] **Step 1:** Locate `_finalize_dictation` in `daemon.py` and add the publish call.
- [ ] **Step 2:** Verify the event is published on the end-word, hotkey-end, and spoken-cancel paths by reading the code paths. The cancel path calls `session.cancel()` (not `finish()`), so `dictation.processing` is NOT published on cancel — this is correct (no processing state on cancel, just the cancel badge).

### Task 3.2: Add `processing` state to sprite state machine

**Files:**
- Modify: `src/voice_commander/voice_sprite/__main__.py`

The sprite state machine currently handles:

```
dictation.start → dictating state
dictation.end   → idle/done state
```

Add:

```
dictation.processing → processing state (new)
dictation.end / error → exit processing state
```

The `processing` state must:
- Display a visual indicator that the daemon has captured audio and is processing (e.g. a spinner frame or a "processing" badge label — match existing HUD/badge patterns).
- Auto-exit on `dictation.end` (any reason) or `dictation.error`.
- Not interfere with the cancel badge (cancel badge fires on `dictation.end {"reason":"cancel"}` via `StateMachine.cancelled_cue`, which is set before `dictation.end` arrives from the sprite's perspective — existing ordering is fine).

- [ ] **Step 1:** Locate the SSE event handler for `dictation.start`/`dictation.end` in `voice_sprite/__main__.py`. Map the state transitions.
- [ ] **Step 2:** Add `dictation.processing` handler that sets a new `processing` state. Implement the visual badge/indicator (match the `_apply_cancelled_cue` pattern from the cancelled badge).
- [ ] **Step 3:** Add `processing → idle` transition on `dictation.end` and `dictation.error`.

### Task 3.3: Remove `transcript` event emission from dictation path

**Files:**
- Modify: `src/voice_commander/dictation/session.py` (if any remnant remains after Phase 2)
- Verify: `src/voice_commander/daemon.py` — confirm `_finalize_dictation` does NOT emit `transcript` during the wait (it emits it after `finish()` returns, which is correct and unchanged).

Under the old streaming-window model, `_on_partial` called `self._bus.publish("transcript", ...)` after every LA-2 commit. With `_on_partial` deleted in Phase 2, there should be no in-session `transcript` emissions from dictation. Verify:

- [ ] **Step 1:** `grep -n "transcript" src/voice_commander/dictation/session.py`. Confirm zero hits after Phase 2 changes.
- [ ] **Step 2:** Confirm `transcript` event still fires in `_finalize_dictation` after the `session.finish()` call (post-paste, unchanged since ADR 0092 D4). This is correct — the post-paste `transcript` event is not a dictation partial, it is the final result.

**Gate A:** `pytest tests/unit/` all green. Verify no `transcript` event in the dictation streaming path via unit test assertion.
**Gate B:** Sakib runs the daemon in command mode, dictates one phrase. Confirms sprite shows "processing" badge after end-word and disappears after paste.

---

# Phase 4 — Remove dead modules and plumbing

**Goal:** Delete `local_agreement.py`, `window.py`, and the frame-tap plumbing from `StreamingRecorder`. Remove deleted config keys and add `max_dictation_s`.

### Task 4.1: Delete dead modules

**Files to delete:**
- `src/voice_commander/dictation/local_agreement.py`
- `src/voice_commander/dictation/window.py`

- [ ] **Step 1:** Confirm no imports of `local_agreement` or `window` remain in any non-test file:

```powershell
Select-String -Recurse -Path src -Pattern "local_agreement|DictationWindow|from .window" | Where-Object { $_.Path -notmatch "\.pyc$" }
```

Expected: zero hits (Phase 2 removed the session imports).

- [ ] **Step 2:** Delete both files.

### Task 4.2: Remove frame-tap plumbing from `StreamingRecorder`

**File:** `src/voice_commander/streaming_recorder.py`

Remove:
- `self._frame_tap: Callable[[...], None] | None = None` field in `__init__` (~line 414).
- `set_frame_tap(self, callback)` public method (~lines 438–452).
- `_invoke_frame_tap(self, frame)` private method (~lines 454–460).
- The `self._invoke_frame_tap(frame)` call in `_vad_loop` (~line 794).

- [ ] **Step 1:** Remove the four items listed above from `streaming_recorder.py`.
- [ ] **Step 2:** Confirm no remaining reference to `frame_tap` in any non-test file:

```powershell
Select-String -Recurse -Path src -Pattern "frame_tap" | Where-Object { $_.Path -notmatch "\.pyc$" }
```

Expected: zero hits.

### Task 4.3: Remove deleted config keys; add `max_dictation_s`

**Files:**
- `src/voice_commander/config.py` (`DictationConfig` dataclass)
- `config.toml.example`

In `DictationConfig`:
- Remove fields: `language: str`, `window_step_ms: int`, `window_cap_ms: int`
- Add field: `max_dictation_s: int = 300`

In `config.toml.example`, under `[dictation]`:
- Remove: `language`, `window_step_ms`, `window_cap_ms`
- Add: `max_dictation_s = 300  # daemon-side hard cap per dictation; server cap is 600 s`
- Remove the ADR 0095 inline comment on `window_step_ms`/`window_cap_ms`

In `daemon.py`:
- Remove: `language=cfg.dictation.language`, `window_step_ms=cfg.dictation.window_step_ms`, `window_cap_ms=cfg.dictation.window_cap_ms` from `DictationSession(...)` constructor call.
- Add: `max_dictation_s=cfg.dictation.max_dictation_s`
- Remove: `dictation_session.set_recorder(daemon._recorder)` call (~line 1573).

- [ ] **Step 1:** Update `config.py`.
- [ ] **Step 2:** Update `config.toml.example`.
- [ ] **Step 3:** Update `daemon.py`.

### Task 4.4: Update config unit tests

**File:** `tests/unit/test_config.py`

- Remove test assertions for `language`, `window_step_ms`, `window_cap_ms`.
- Add test assertions for `max_dictation_s` default value (300).

- [ ] **Step 1:** Update `test_config.py`.
- [ ] **Step 2:** Run `pytest tests/unit/test_config.py -v`. Must PASS.

**Gate A:** `pytest tests/unit/` all green. No imports of deleted modules anywhere in `src/`.
**Gate B:** Sakib runs the daemon in command mode and confirms no regressions. Dictates a phrase end-to-end with the mock server (or integration test run).

---

# Phase 5 — Tests cleanup + new mock server

**Goal:** Delete test files for deleted code. Rewrite the mock WS server and integration test suite for the new protocol. Add tests for cap-timeout, empty-silence, and disconnect-mid-stream paths.

### Task 5.1: Delete obsolete test files

**Files to delete:**
- `tests/unit/test_dictation_local_agreement.py` — tests deleted `LocalAgreement`
- `tests/unit/test_dictation_window.py` — tests deleted `DictationWindow`
- `tests/unit/test_streaming_recorder_frame_tap.py` — tests deleted frame tap

- [ ] **Step 1:** Confirm no other test file imports from these modules.
- [ ] **Step 2:** Delete all three files.
- [ ] **Step 3:** Run `pytest tests/unit/` to confirm clean run.

### Task 5.2: Rewrite `tests/integration/_dictation_ws.py`

Replace the old mock server (which replied with `partial` frames per chunk) with a new mock that:

1. Accumulates binary frames into a `bytearray`.
2. On `{"type":"end"}` JSON frame: reads `done_text` from the test's `__init__` arg; sends `{"type":"done","text":<done_text>,"raw":"<raw>"}` and closes.
3. Tracks `bytes_received` (total bytes accumulated) and `chunk_count` (number of binary frames received) for test assertions.
4. Does NOT send any `partial` frame at any point.
5. Does NOT expect a `{"type":"config"}` frame (any JSON frame before `end` is logged and ignored, not added to audio buffer).
6. Optional: `error_after_bytes: int | None` — if set, sends `{"type":"error","message":"max duration exceeded"}` and closes when accumulated bytes exceed this threshold (tests the server-cap path).

New `MockWsServer` interface:

```python
class MockWsServer:
    def __init__(self, done_text: str = "", error_after_bytes: int | None = None) -> None: ...
    def start(self) -> str: ...  # returns ws:// URL
    def stop(self) -> None: ...
    def __enter__(self) -> "MockWsServer": ...
    def __exit__(self, *exc) -> None: ...
    @property
    def bytes_received(self) -> int: ...
    @property
    def chunk_count(self) -> int: ...
```

- [ ] **Step 1:** Rewrite `tests/integration/_dictation_ws.py` per the spec above.
- [ ] **Step 2:** Confirm `configs` attribute is removed (old mock stored config frames; new mock has no config frame concept).

### Task 5.3: Rewrite `tests/integration/test_dictation_streaming_session.py`

Restructure for the new protocol:

**Tests to keep (update for new mock):**
- End-word path: `DictationSession.finish()` returns `done.text` from mock server.
- Hotkey-end path: `request_end()` + pipeline drain → `finish()` returns `done.text`.
- Cancel path: session cancelled, no done frame requested, no paste.
- Connect failure: `finish()` returns `""`.

**New tests to add:**
- `test_cap_timeout_sends_end_and_awaits_done`: set `max_dictation_s=0.1`; mock replies with `done`; session returns `done.text`.
- `test_empty_silence_done_text`: mock returns `done.text="""`; `finish()` returns `""`.
- `test_disconnect_mid_stream`: mock closes connection during binary frame receive; session returns `""`.
- `test_no_partial_frames_expected`: assert mock's `chunk_count > 0` and that `done` frame was the only JSON reply.
- `test_bytes_received_matches_audio_sent`: send N utterances of known length; assert `mock.bytes_received == sum(len(a.tobytes()) for a in utterances)`.

**Tests to remove:**
- Any test involving `partial` frames, `segments`, config frame assertions (`mock.configs`), LocalAgreement fallback, `raw_transcript_fn`, or `window_step_ms`/`window_cap_ms`.

- [ ] **Step 1:** Rewrite `test_dictation_streaming_session.py`.
- [ ] **Step 2:** Run `pytest tests/integration/test_dictation_streaming_session.py -v`. All new tests must PASS.

**Gate A:** Full `pytest tests/` green. Zero references to `local_agreement`, `DictationWindow`, `frame_tap`, `on_partial`, `segments`, or `raw_transcript_fn` in any test file.
**Gate B:** Sakib reviews the new mock server logic and confirms it accurately represents the new server contract from ADR 0096 D2.

---

# Phase 6 — Docs sweep

**Goal:** Mark superseded ADRs, rewrite the dictation docs, update CLAUDE.md and the technical-decisions table.

### Task 6.1: Mark ADRs 0091, 0092, 0095 superseded

**Files:**
- `docs/decisions/0091-streaming-dictation-experiment.md`
- `docs/decisions/0092-streaming-dictation-integration.md`
- `docs/decisions/0095-streaming-window-dictation.md`

At the top of each file (below the `#` heading), change the Status line to:

```
**Status:** Superseded by [ADR 0096](0096-server-side-dictation.md)
```

- [ ] **Step 1:** Edit all three files.

### Task 6.2: Update `docs/dictation-streaming.md`

Rewrite the transport and architecture sections to describe the raw-PCM server-side accumulation model. Remove references to:
- `DictationWindow`, `LocalAgreement`, frame tap, growing-window WAVs.
- `partial` frames, `segments`, `raw_transcript` on end frame.
- `window_step_ms`, `window_cap_ms`, `language` config keys.

Add description of:
- Raw float32 PCM binary frames.
- `{"type":"end"}` → `{"type":"done"}` protocol.
- `max_dictation_s` daemon cap + 600 s server cap.
- `dictation.processing` sprite state.
- HUD silence during dictation.

- [ ] **Step 1:** Edit `docs/dictation-streaming.md`.

### Task 6.3: Update `docs/transcription-pipeline.md`

Update the dictation path section (§4 or equivalent) to reflect the server-side accumulation model. Remove LocalAgreement, DictationWindow, frame tap. Add: raw PCM transport, single server decode, LLM cleanup in `done` frame.

- [ ] **Step 1:** Edit `docs/transcription-pipeline.md`.

### Task 6.4: Update `docs/agents/technical-decisions.md`

Add a row for ADR 0096. Mark ADRs 0091, 0092, 0095 as superseded in their rows.

- [ ] **Step 1:** Edit `docs/agents/technical-decisions.md`.

### Task 6.5: Update `CLAUDE.md` "Current state" section

Rewrite the dictation paragraph in the "Current state" section of `CLAUDE.md` to reflect server-side dictation (ADR 0096). Remove all mentions of: `LocalAgreement-2`, `DictationWindow`, `StreamingRecorder.set_frame_tap`, `window_step_ms`, `window_cap_ms`, `_on_partial`, `_on_frame`, `partial` frames, `segments`, whole-window WAV streaming. Add: raw PCM WebSocket frames, single-decode-at-end, `dictation.processing` event, `max_dictation_s`, HUD-quiet dictation.

- [ ] **Step 1:** Edit `CLAUDE.md`.

**Gate A:** `pytest tests/` clean. All ADR references in docs are consistent with the new model.
**Gate B:** Sakib reads the updated CLAUDE.md "Current state" dictation paragraph and confirms it accurately describes what was built.

---

# Phase 7 — Live cutover validation (manual, Sakib at mic)

**Precondition:** Phase 0 Gate B is green (server upgraded and smoke-tested).

**Goal:** Confirm all five dictation exit paths work end-to-end against the real server. Confirm no command-mode regression.

### Task 7.1: Restart daemon against upgraded server

- [ ] **Step 1:** Confirm `[dictation] ws_url` in `config.toml` points to the new server
  (`ws://192.168.4.200:8767/ws/transcribe`). Confirm the server is running.
- [ ] **Step 2:** Restart the daemon. Watch the startup logs for connection errors.

### Task 7.2: Five exit-path tests

For each test below, dictate the phrase, observe sprite state, check pasted text.

**Test A — End-word ("done"):**
- [ ] Dictate: "This is a dictation test, done."
- [ ] Observe: sprite shows "processing" badge after "done" is recognised.
- [ ] Confirm: pasted text is a clean, punctuated version of the spoken sentence.
  `done` is not included in the paste.

**Test B — Hotkey-end (Right Ctrl):**
- [ ] Dictate: "Another sentence here." then press Right Ctrl.
- [ ] Observe: sprite shows "processing" badge.
- [ ] Confirm: pasted text is clean and correct.

**Test C — Long dictation (>30 s):**
- [ ] Dictate continuously for ~45 seconds, then say "done".
- [ ] Observe: sprite shows "processing" badge during the wait (expected ~5–20 s latency).
- [ ] Confirm: full pasted text matches what was spoken (no truncation).

**Test D — Cancel mid-dictation (spoken):**
- [ ] Dictate two sentences, then say "cancel".
- [ ] Observe: sprite shows cancel badge ("✕ CANCELLED"). No "processing" badge.
- [ ] Confirm: nothing is pasted.

**Test E — Hotkey cancel (Right Ctrl during active dictation started by Right Ctrl):**
- [ ] Press Right Ctrl to open dictation. Speak one sentence. Press Right Ctrl to end.
- [ ] Observe: sprite shows "processing" badge, then clears after paste.
- [ ] Confirm: pasted text matches the spoken sentence.

### Task 7.3: Command-mode regression check

- [ ] Issue three voice commands in command mode (e.g. "copy", "open spotify", "focus").
- [ ] Confirm each command executes correctly with no dictation-related errors in the log.

**Gate A:** All five exit-path tests pass.
**Gate B:** Sakib signs off: "dictation works, command mode unaffected, good to merge."
