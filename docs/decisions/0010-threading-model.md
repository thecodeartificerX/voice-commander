# ADR 0010: Threading Model

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander has three independent I/O concerns that must operate concurrently without blocking each other:

1. **Global hotkey detection** — `pynput` delivers key events via a native OS callback that runs on a dedicated listener thread created by the library. The callback must return quickly; any blocking work on the callback thread stalls hotkey detection.

2. **Audio capture** — `sounddevice` fires a Python callback from its own internal C-level audio thread every time a buffer of PCM samples is ready (typically every 20–100 ms). This callback must also return quickly; blocking it causes audio dropouts and lost samples.

3. **Transcription, matching, and dispatch** — `faster-whisper` inference on a CUDA GPU takes 0.5–3 seconds per audio file. This work cannot share a thread with either of the above callbacks without blocking them.

The three concerns have a latency budget that rules out serialisation: recording must start and stop within ~50 ms of the keypress; audio must be captured without gaps; transcription results must arrive within ~3 seconds of the key-up event. Running everything on the main thread would violate all three budgets simultaneously.

Python's GIL is not the binding constraint here: `sounddevice` and `pynput` callbacks run in threads that release the GIL during their native I/O, and `faster-whisper`'s CUDA inference similarly releases the GIL during GPU work. CPython threads are therefore effective for this workload.

## Decision

Use **four threads** with clearly separated responsibilities:

| Thread | Created by | Role |
|--------|-----------|------|
| **Main thread** | Python interpreter | Orchestrates startup and shutdown; owns `threading.Event` flags (`_stop_event`, `_recording`); joins all other threads on exit. |
| **pynput listener thread** | `pynput.keyboard.Listener` | Delivers `on_press` / `on_release` callbacks. Callback body does only: flip the `_recording` event, log the state change, and call `sounddevice.rec()` / `sounddevice.stop()`. |
| **sounddevice callback thread** | `sounddevice` C runtime | Receives PCM buffers; callback appends raw bytes to a `bytearray` and returns immediately. On stop, the main thread (triggered by the pynput callback) saves the buffer to a `Path` and pushes it onto the queue. |
| **Worker thread** | `threading.Thread(target=_worker, daemon=True)` | Blocks on `queue.Queue[Path].get()`; for each path: runs faster-whisper inference, calls `FuzzyRouter.dispatch()`, plays feedback sound, shows toast. Loop exits when it dequeues the sentinel value `None`. |

Graceful shutdown sequence:
1. `_stop_event.set()` signals the main thread's run-loop to exit.
2. Main thread enqueues `None` (sentinel) to unblock the worker's `queue.get()`.
3. Main thread calls `listener.stop()`, then `worker_thread.join(timeout=10)`.
4. On timeout, the daemon flag ensures the worker is abandoned without hanging the process.

The `queue.Queue` is the sole cross-thread data channel; no shared mutable state exists between threads other than the `threading.Event` flags, which are write-once per recording session.

## Consequences

### Positive
- Each thread has a single, well-bounded responsibility; failures are easy to isolate and test independently.
- `queue.Queue` is thread-safe by design; no explicit locking is needed for the transcription pipeline.
- Daemon flag on the worker thread ensures the process exits cleanly even if the worker is mid-inference when shutdown is requested.
- `threading.Event` flags are the simplest possible synchronisation primitive — readable, low-overhead, and immune to priority-inversion.
- The model loads once on worker-thread startup and stays resident in GPU memory for the lifetime of the process, avoiding repeated CUDA context setup overhead on every recording.

### Negative
- The four-thread model is not trivially unit-testable; the worker thread must be started and stopped in integration tests, adding test infrastructure complexity.
- If the worker thread falls behind (e.g. GPU is busy with another process), the `queue.Queue` grows unboundedly. No back-pressure mechanism is implemented; this is acceptable because recording is gated by the human operator, not an automated loop.
- Stack traces that cross thread boundaries are harder to read; exceptions in the worker thread must be caught and re-logged explicitly or they will be silently swallowed.
- `sounddevice` callbacks cannot be unit-tested without a real (or virtual) audio device; they are integration-tested only.

### Neutral
- The worker thread is long-lived (one per process invocation), not per-recording. This avoids thread-creation overhead on each keypress but means the CUDA context is never freed until process exit.
- Thread names (`worker`, `listener`) are set explicitly via `thread.name` to improve readability in debugger and log output.
- The threading model is documented in `docs/architecture.md` with an ASCII thread-interaction diagram that should be kept in sync with any future changes.

## Alternatives considered

### asyncio event loop
Replace threads with `asyncio` coroutines and a single-threaded event loop. Rejected because neither `pynput` nor `sounddevice` are async-native; both deliver events via native OS callbacks that run on their own C-level threads regardless of the Python-side concurrency model. Bridging them to `asyncio` via `loop.call_soon_threadsafe()` adds complexity without removing threads — the same thread count would exist, just with additional asyncio plumbing wrapping them.

### multiprocessing
Run the transcription worker in a separate OS process using `multiprocessing.Process` and a `multiprocessing.Queue` for audio file paths. Rejected because the IPC overhead (pickle serialisation, pipe reads) adds latency on the critical path between recording-stop and transcription-start. The CUDA context cannot be inherited across `spawn`-mode processes on Windows; it would need to be re-initialised in the child process, adding ~1–2 s of startup latency per session. The benefit (true parallelism past the GIL) is not needed because `faster-whisper` already releases the GIL during GPU inference.

### Thread-per-recording (short-lived worker threads)
Spawn a new `threading.Thread` for each recording, run inference inside it, and let it exit. Rejected because it would require reinitialising the `WhisperModel` on every recording (or sharing the model across threads with a mutex), and thread creation overhead on Windows is non-trivial (~1–5 ms). The long-lived single worker thread with a queue achieves the same concurrency goal with lower overhead and simpler lifecycle management.

## References

- Python `threading` module: https://docs.python.org/3/library/threading.html
- Python `queue.Queue`: https://docs.python.org/3/library/queue.html
- pynput threading notes: https://pynput.readthedocs.io/en/latest/keyboard.html#monitoring-the-keyboard
- sounddevice callback thread documentation: https://python-sounddevice.readthedocs.io/en/latest/usage.html#callback-streams
