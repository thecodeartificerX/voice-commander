# sounddevice Reference

> Cited from: https://python-sounddevice.readthedocs.io/en/0.3.15/api/streams.html (2026-04-19)
> Cited from: https://python-sounddevice.readthedocs.io/en/0.4.1/usage.html (2026-04-19)
> Cited from: https://pypi.org/project/sounddevice/ (2026-04-19)

---

## Overview

`sounddevice` is a Python module providing bindings for the PortAudio library. It
enables real-time audio recording and playback using NumPy arrays. Voice Commander
uses `sounddevice.InputStream` to capture microphone audio in a non-blocking
callback-driven model — filling a rolling buffer that is transcribed when the
push-to-talk hotkey is released.

## Installation

```bash
pip install sounddevice
```

Depends on PortAudio. On Windows, PortAudio is bundled in the wheel.

```bash
# For NumPy array support (required for InputStream):
pip install sounddevice numpy
```

## Minimal Working Example (Voice Commander)

```python
import sounddevice as sd
import numpy as np
from collections import deque
import threading

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"
BLOCK_SIZE = 1024  # frames per callback

# Rolling audio buffer
audio_buffer: deque = deque(maxlen=100)  # ~100 blocks ≈ 6.5s at 16kHz/1024
recording = threading.Event()

def audio_callback(indata: np.ndarray, frames: int, time, status):
    """Called by PortAudio in a background thread for each audio block."""
    if status:
        print(f"Audio status: {status}")  # underrun/overflow warnings
    if recording.is_set():
        audio_buffer.append(indata.copy())  # MUST copy — indata is reused

# List available devices
print(sd.query_devices())
print(sd.query_devices(kind="input"))  # default input device

# Create non-blocking input stream
stream = sd.InputStream(
    samplerate=SAMPLE_RATE,
    channels=CHANNELS,
    dtype=DTYPE,
    blocksize=BLOCK_SIZE,
    callback=audio_callback,
    device=None,  # None = default device
)

with stream:
    recording.set()
    import time; time.sleep(3)
    recording.clear()

# Assemble audio for transcription
if audio_buffer:
    audio = np.concatenate(list(audio_buffer), axis=0).flatten()
    print(f"Captured {len(audio)/SAMPLE_RATE:.2f}s of audio")
```

---

## API Reference

### `sounddevice.InputStream`

A non-blocking (or blocking) input-only audio stream using NumPy arrays.

```python
sd.InputStream(
    samplerate=None,
    blocksize=0,
    device=None,
    channels=None,
    dtype=None,
    latency="high",
    extra_settings=None,
    callback=None,
    finished_callback=None,
    clip_off=False,
    dither_off=False,
    never_drop_input=False,
    prime_output_buffers_using_stream_callback=False,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `samplerate` | float | None | Sample rate in Hz. None = device default. Voice Commander uses `16000` |
| `blocksize` | int | 0 | Frames per callback invocation. 0 = PortAudio chooses. Higher = more latency, fewer callbacks |
| `device` | int or str or (int,int) | None | Input device index or name. None = system default. Use `sd.query_devices()` to find |
| `channels` | int | None | Number of input channels. None = device default. Voice Commander uses `1` (mono) |
| `dtype` | str or numpy.dtype | None | Sample format: `"float32"`, `"float64"`, `"int32"`, `"int16"`, `"int8"`, `"uint8"`. `"float32"` is recommended for faster-whisper |
| `latency` | float or str | `"high"` | Suggested latency in seconds, or `"low"` / `"high"` |
| `extra_settings` | PortAudioSettings | None | Platform-specific additional settings |
| `callback` | Callable | None | Function called for each audio block. If None, use `stream.read()` |
| `finished_callback` | Callable | None | Called when stream is stopped/aborted |
| `clip_off` | bool | False | Disable clipping of out-of-range values |
| `dither_off` | bool | False | Disable dithering |
| `never_drop_input` | bool | False | Never drop input frames (for full-duplex streams) |
| `prime_output_buffers_using_stream_callback` | bool | False | Prime output buffer using stream callback |

---

### Callback Signature

```python
def audio_callback(
    indata: numpy.ndarray,   # shape (blocksize, channels), dtype as specified
    frames: int,             # number of frames in this block (same as blocksize when non-zero)
    time: CData,             # PortAudio timing info
    status: sd.CallbackFlags # flags for underrun/overflow/etc.
) -> None:
```

| Argument | Type | Description |
|----------|------|-------------|
| `indata` | `numpy.ndarray` | Input samples, shape `(frames, channels)`. Read-only view — **must `.copy()`** if you store it |
| `frames` | int | Number of audio frames in this block |
| `time` | CData | PortAudio `PaStreamCallbackTimeInfo` struct with `.inputBufferAdcTime`, `.outputBufferDacTime`, `.currentTime` |
| `status` | `sd.CallbackFlags` | Bitmask; check `status.input_overflow`, `status.input_underflow` |

**Critical:** `indata` is a **view** into PortAudio's internal buffer. It becomes invalid
after the callback returns. Always call `indata.copy()` before storing.

```python
def callback(indata, frames, time, status):
    if status.input_overflow:
        print("WARNING: input overflow")
    buffer.append(indata[:, 0].copy())  # mono: take channel 0
```

---

### `InputStream` Methods

```python
stream.start()   # Start the stream (called automatically with context manager)
stream.stop()    # Stop the stream (waits for pending callbacks to finish)
stream.close()   # Close and release resources
stream.abort()   # Abort immediately, discarding pending buffers
stream.read(frames)  # Blocking read (only when callback=None)
```

Context manager support:
```python
with sd.InputStream(...) as stream:
    # stream.active == True inside block
    ...
# stream.stopped == True after block
```

### `InputStream` Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `stream.samplerate` | float | Actual sample rate |
| `stream.channels` | int | Number of channels |
| `stream.dtype` | str | Data type string |
| `stream.latency` | float | Actual input latency in seconds |
| `stream.device` | int | Device index |
| `stream.active` | bool | True if stream is running |
| `stream.stopped` | bool | True if stream is stopped |
| `stream.closed` | bool | True if stream is closed |
| `stream.blocksize` | int | Frames per block |

---

### `sounddevice.query_devices`

```python
sd.query_devices(device=None, kind=None)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `device` | int or str | Device index or name. None = list all devices |
| `kind` | str | `"input"` or `"output"` — returns default device of that kind |

**Returns:**
- If `device=None`: `DeviceList` (printable table of all devices)
- If device specified: `dict` with device info

Device info dict keys:
```python
{
    "name": str,
    "index": int,
    "hostapi": int,
    "max_input_channels": int,
    "max_output_channels": int,
    "default_low_input_latency": float,
    "default_high_input_latency": float,
    "default_samplerate": float,
}
```

**Usage:**
```python
# Print all devices
print(sd.query_devices())

# Get default input device info
info = sd.query_devices(kind="input")
print(info["name"], info["default_samplerate"])

# Get specific device by index
info = sd.query_devices(0)

# Find device by name substring
# (no built-in search — iterate manually)
for i, dev in enumerate(sd.query_devices()):
    if "microphone" in dev["name"].lower():
        print(i, dev["name"])
```

---

### `sounddevice.default`

Global defaults for all streams:

```python
sd.default.samplerate = 16000
sd.default.channels = 1
sd.default.dtype = "float32"
sd.default.device = "Microphone"  # or device index
```

---

## Sample Rate Conversion Notes

faster-whisper requires **16000 Hz** audio. If the device does not support 16 kHz,
you must resample:

```python
import numpy as np

def resample(audio: np.ndarray, orig_sr: int, target_sr: int = 16000) -> np.ndarray:
    """Simple linear resample using numpy. For production use librosa or resampy."""
    if orig_sr == target_sr:
        return audio
    factor = target_sr / orig_sr
    n_samples = int(len(audio) * factor)
    return np.interp(
        np.linspace(0, len(audio) - 1, n_samples),
        np.arange(len(audio)),
        audio,
    )
```

Preferred: `samplerate=16000` directly avoids resampling overhead.

---

## Known Gotchas

1. **`indata` is a view** — never store without `.copy()`. This is the most common bug.

2. **Callback runs in a PortAudio thread** — never do I/O, print statements, or heavy
   computation in the callback. Use a `queue.Queue` or `deque` to pass data to main thread.

3. **`dtype="float32"` range is -1.0 to 1.0** — faster-whisper expects this range.
   `int16` range is -32768 to 32767; must divide by 32768.

4. **`blocksize=0`** — PortAudio chooses block size; may vary per call. Use fixed
   `blocksize=1024` or `blocksize=4096` for deterministic buffer sizing.

5. **Device index -1** — not a valid device index. Use `None` for default device.

6. **Stream must not be garbage-collected while active** — keep a reference alive.

7. **`status.input_overflow`** — common on Windows if the callback is too slow. 
   Use a simple buffer append in callback; do processing in a separate thread.

8. **`channels=1` vs shape** — indata shape is always `(frames, channels)`, so for
   mono: `indata.shape == (frames, 1)`. Flatten with `indata[:, 0]` or `indata.flatten()`.

---

## Cited from

- https://python-sounddevice.readthedocs.io/en/0.3.15/api/streams.html — fetched 2026-04-19
- https://python-sounddevice.readthedocs.io/en/0.4.1/usage.html — fetched 2026-04-19
- Web search results for "python-sounddevice InputStream constructor" — 2026-04-19
