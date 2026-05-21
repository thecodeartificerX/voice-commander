# ADR 0003: sounddevice over PyAudio for Microphone Capture

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander must capture microphone audio in real time on Windows 11, at 16 kHz mono (the format required by `faster-whisper` / Whisper models), and write the result to a temporary WAV file for transcription. The capture must start and stop on-demand (triggered by the Scroll Lock hotkey — see ADR 0001), with low latency on the first frame. The solution must be installable via `uv`/PyPI without requiring the user to install system-level audio SDKs or compile native extensions. Two Python audio libraries dominate the ecosystem for this use case: `sounddevice` and `PyAudio`.

## Decision

Use **`sounddevice`** for microphone capture, paired with **`soundfile`** for WAV file I/O. `sounddevice` wraps PortAudio via a pre-compiled binary wheel (`sounddevice` ships `_sounddevice.pyd` and the PortAudio DLL on Windows), making `pip install sounddevice` / `uv add sounddevice` a one-step process with no separate SDK install. Its NumPy-native API (`sd.rec(...)` returning `np.ndarray`) integrates cleanly with downstream audio processing. `soundfile` similarly ships pre-compiled wheels and provides a one-call WAV write (`sf.write(path, data, samplerate)`). Recording is performed with `sd.rec(frames, samplerate=16000, channels=1, dtype='int16')` followed by `sd.wait()`, or via an `InputStream` callback for streaming capture.

## Consequences

### Positive
- Binary wheels for Windows are published on PyPI for both `sounddevice` and `soundfile`; installation requires no compiler, no separate PortAudio SDK download, and no Visual C++ build tools.
- NumPy-native return values eliminate an explicit buffer-unpacking step; the `int16` array can be passed directly to `soundfile.write` or preprocessed (normalisation, VAD) with standard NumPy operations.
- `sounddevice` supports both blocking (`sd.rec` + `sd.wait`) and streaming (`InputStream` callback) APIs, giving flexibility to switch to a streaming approach in a later phase without changing dependencies.
- PortAudio (the underlying C library) is battle-tested across platforms and audio backends (WASAPI, DirectSound, MME on Windows).

### Negative
- PortAudio device indices are not stable across USB reconnects or reboots; hardcoding a device index in `config.toml` is fragile. The config must store the device name alongside any index, and the startup code must look up the device by name and fall back to the system default (`device=-1`) when the named device is not found. (Documented in `docs/gotchas.md`.)
- `sounddevice` inherits PortAudio's occasional latency on first open on WASAPI exclusive mode; in shared mode (the default) first-open latency is acceptable but not zero.
- The pre-compiled PortAudio DLL bundled with `sounddevice` wheels may lag behind the latest PortAudio release; users with exotic audio hardware may encounter issues that a system-installed PortAudio would fix.

### Neutral
- `soundfile` is a separate package (not bundled with `sounddevice`) and must be listed explicitly in `pyproject.toml`.
- Both libraries are cross-platform (Windows/macOS/Linux), consistent with the cross-platform portability noted in ADR 0002 for `pynput`.

## Alternatives considered

### PyAudio
`PyAudio` is the most widely referenced Python audio library and wraps PortAudio just as `sounddevice` does. However, installing `PyAudio` on Windows is notoriously painful: the official PyPI wheel is often out of date, the recommended install path involves downloading an unofficial binary from Christoph Gohlke's repository or compiling from source with Visual C++. This friction is unacceptable for a project that targets easy `uv add` installation. Additionally, `PyAudio`'s API returns raw bytes that must be manually unpacked (e.g. `struct.unpack` or `numpy.frombuffer`), whereas `sounddevice` returns NumPy arrays directly.

### `ffmpeg` subprocess (via `subprocess` or `ffmpeg-python`)
Spawning an `ffmpeg` subprocess for microphone capture (`ffmpeg -f dshow -i audio="Microphone" ...`) can work but introduces several problems for a real-time daemon: process startup latency is high (hundreds of milliseconds), inter-process communication adds complexity, error handling across process boundaries is fragile, and it requires `ffmpeg.exe` to be installed and on `PATH` separately. `ffmpeg` is an appropriate tool for offline audio file conversion but is too heavy for on-demand real-time start/stop capture.

### `pyaudiowpatch` (WASAPI loopback variant)
`pyaudiowpatch` is a fork of PyAudio with WASAPI loopback support for recording system audio. It is not needed here (microphone input, not loopback) and carries the same wheel-availability problems as upstream PyAudio.

### `soundcard`
`soundcard` is a newer Python audio library with a clean API and no PortAudio dependency (uses OS-native APIs directly). It is less mature than `sounddevice`, has a smaller user base, and its Windows WASAPI implementation has had intermittent issues with certain sample rates. `sounddevice`'s larger community and more thorough Windows testing made it the safer choice.

## References

- sounddevice documentation: https://python-sounddevice.readthedocs.io/
- sounddevice PyPI: https://pypi.org/project/sounddevice/
- soundfile PyPI: https://pypi.org/project/soundfile/
