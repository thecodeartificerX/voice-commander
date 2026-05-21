# ADR 0008: winsound.PlaySound for Audio Chimes

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander needs short, distinct audio cues to confirm state transitions without requiring the user to look at the screen:

- **Recording start**: a brief tone played immediately after the hotkey press.
- **Recording stop / dispatch**: a tone played when audio capture ends and processing begins.
- **Miss / no match**: a different tone when the transcript does not match any registered phrase.

These cues must be:
1. **Asynchronous** — the sound must not block the worker thread or delay subsequent processing.
2. **Low-latency** — playback should begin within a few tens of milliseconds of the triggering event.
3. **Self-contained** — the solution should not pull in additional dependencies beyond those already required for the project's core functionality.

The project already depends on `sounddevice` for audio *capture*. However, mixing capture and playback concerns through the same library creates lifecycle coupling: pausing or stopping the `InputStream` during playback coordination, or accidentally sharing the same device handle, can introduce subtle bugs. It is cleaner to keep input and output concerns in separate subsystems.

## Decision

Use Python's stdlib `winsound.PlaySound` with the flags `winsound.SND_FILENAME | winsound.SND_ASYNC`. This combination loads a `.wav` file from disk and plays it on the Windows default audio output device without blocking the calling thread. The call returns immediately; playback continues in a Windows-managed background thread.

Three `.wav` files (start, stop, miss) are shipped in `voice_commander/assets/sounds/` and referenced by their absolute paths at runtime. File loading at each call is acceptable because Windows caches recently accessed files and the files are small (<10 KB each).

```python
import winsound

def play_chime(path: str) -> None:
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
```

## Consequences

### Positive
- Zero additional dependencies: `winsound` is part of the Python standard library on Windows.
- `SND_ASYNC` guarantees the call returns before playback completes — the worker thread is never blocked.
- Simple, auditable implementation: one function, two arguments.
- No device handle management, no stream lifecycle, no teardown required.
- Works with any `.wav` file; chime assets can be swapped without code changes.

### Negative
- Windows-only: `winsound` is not available on macOS or Linux. Acceptable — the spec targets Windows 11 exclusively.
- Only `.wav` format is supported natively; MP3 or OGG chimes are not possible without conversion.
- No volume control API: playback volume follows the system application volume for Python. This is unlikely to be a problem for short chimes but rules out programmatic volume adjustment.
- Concurrent `SND_ASYNC` calls may overlap if chimes are triggered in rapid succession. In practice the state machine prevents this (recording start and stop are mutually exclusive), but no explicit guard is provided.

### Neutral
- Playback device is determined by Windows, not the application; the chime will follow the user's default audio output (speakers, headset, etc.).
- The `winsound` module is documented but minimally maintained; its API has been stable since Python 2.x and is unlikely to change.

## Alternatives considered

### playsound
A cross-platform convenience wrapper. On Windows it uses `winmm` (same underlying Windows Multimedia API as `winsound`) or `pygame`. The library has a history of maintenance lapses, open bugs on Windows (file handle not released after playback, `PlaysoundException` on some audio drivers), and adds a dependency with no benefit over stdlib `winsound`. Rejected.

### pygame.mixer
Full-featured audio playback with channel mixing, volume control, and format support. Brings a ~10 MB binary dependency for the sole purpose of playing three short beeps. Initialising `pygame.mixer` also occupies the audio device, which could interfere with `sounddevice` capture on systems with limited audio routing. Overkill. Rejected.

### sounddevice playback
`sounddevice` can play NumPy arrays via `sd.play()` with `blocking=False`. This is technically viable, but it couples playback to the same library (and potentially the same device) used for capture. Keeping input and output in separate subsystems reduces the chance of device-contention bugs and makes each subsystem independently replaceable. Rejected to maintain separation of concerns.

### Windows Text-to-Speech (SAPI)
Using `win32com` to invoke the Windows Speech API for spoken feedback ("Recording started"). More human-friendly but higher latency (100–500 ms TTS synthesis), longer utterance duration, and requires `pywin32`. Not appropriate for sub-100 ms state-change cues. Rejected for this use case; may be considered for accessibility mode in a future phase.

## References

- Python `winsound` stdlib docs: https://docs.python.org/3/library/winsound.html
- Windows Multimedia `PlaySound` function: https://learn.microsoft.com/en-us/previous-versions/dd743680(v=vs.85)
