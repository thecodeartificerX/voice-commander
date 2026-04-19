# winsound Reference

> Cited from: https://docs.python.org/3/library/winsound.html (2026-04-19)

---

## Overview

`winsound` is a Python standard library module (Windows only) providing access to the
basic sound-playing machinery provided by Windows. Voice Commander uses it to play
audio feedback sounds: a start sound when recording begins, a stop sound when recording
ends, and a miss sound when no command matches.

## Installation

No installation required — `winsound` is part of Python's standard library on Windows.

```python
import winsound  # available on Windows only
```

On non-Windows platforms this import will fail. Guard with:
```python
import sys
if sys.platform == "win32":
    import winsound
```

## Minimal Working Example (Voice Commander)

```python
import winsound
import os

SOUNDS_DIR = "assets/sounds"

def play_start():
    """Play recording-started feedback sound (async so it doesn't block)."""
    path = os.path.abspath(os.path.join(SOUNDS_DIR, "start.wav"))
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

def play_stop():
    """Play recording-stopped feedback sound."""
    path = os.path.abspath(os.path.join(SOUNDS_DIR, "stop.wav"))
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

def play_miss():
    """Play no-match feedback sound."""
    path = os.path.abspath(os.path.join(SOUNDS_DIR, "miss.wav"))
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

def stop_all_sounds():
    """Stop any currently playing async sound."""
    winsound.PlaySound(None, winsound.SND_PURGE)

# Looping alert (requires SND_ASYNC — will loop until PlaySound(None, ...) called)
def play_looping_alert(path: str):
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
```

---

## API Reference

### `winsound.PlaySound(sound, flags)`

```python
winsound.PlaySound(sound, flags) -> None
```

Play a sound file or system sound.

| Parameter | Description |
|-----------|-------------|
| `sound` | Filename (str), system alias (str), in-memory WAV data (bytes), or `None` to stop |
| `flags` | Bitwise OR combination of `SND_*` constants |

**Returns:** `None`

**Raises:** `RuntimeError` on system error

Passing `sound=None` stops any currently playing waveform sound:
```python
winsound.PlaySound(None, winsound.SND_PURGE)
```

---

### `winsound.Beep(frequency, duration)`

```python
winsound.Beep(frequency: int, duration: int) -> None
```

Generate a simple beep tone through the PC speaker.

| Parameter | Type | Range | Description |
|-----------|------|-------|-------------|
| `frequency` | int | 37–32767 | Frequency in Hertz |
| `duration` | int | ≥1 | Duration in milliseconds |

**Raises:** `RuntimeError` if the system cannot beep

```python
winsound.Beep(440, 500)   # A4, 500ms
winsound.Beep(880, 200)   # A5, 200ms
```

---

### `winsound.MessageBeep(type=MB_OK)`

```python
winsound.MessageBeep(type=MB_OK) -> None
```

Play a sound associated with a Windows message type (configured in Control Panel).

| Parameter | Value | Sound |
|-----------|-------|-------|
| `MB_OK` | `0x00000000` | Default (OK) sound |
| `MB_ICONASTERISK` | `0x00000040` | Asterisk/Information |
| `MB_ICONEXCLAMATION` | `0x00000030` | Exclamation/Warning |
| `MB_ICONHAND` | `0x00000010` | Critical Stop / Hand |
| `MB_ICONQUESTION` | `0x00000020` | Question |
| `MB_ICONERROR` | alias | Same as `MB_ICONHAND` |
| `MB_ICONINFORMATION` | alias | Same as `MB_ICONASTERISK` |
| `MB_ICONSTOP` | alias | Same as `MB_ICONHAND` |
| `MB_ICONWARNING` | alias | Same as `MB_ICONEXCLAMATION` |
| `-1` | — | Simple beep (fallback) |

**Raises:** `RuntimeError` on system error

```python
winsound.MessageBeep(winsound.MB_OK)
winsound.MessageBeep(-1)  # always produces a sound
```

---

## SND_* Flags Reference

| Flag | Value | Description | Notes |
|------|-------|-------------|-------|
| `SND_FILENAME` | — | `sound` is a path to a `.wav` file | Mutually exclusive with `SND_ALIAS` |
| `SND_ALIAS` | — | `sound` is a system sound registry alias | Mutually exclusive with `SND_FILENAME` |
| `SND_MEMORY` | — | `sound` is bytes-like WAV data in memory | Cannot combine with `SND_ASYNC` |
| `SND_ASYNC` | — | Return immediately; play asynchronously | Cannot combine with `SND_MEMORY` |
| `SND_SYNC` | — | Play synchronously (blocks until done). Default behaviour. Added in Python 3.14 | Default when no SND_ASYNC |
| `SND_LOOP` | — | Loop sound repeatedly | **Requires `SND_ASYNC`**; cannot use with `SND_MEMORY` |
| `SND_NODEFAULT` | — | Don't play system default sound if specified sound not found | |
| `SND_NOSTOP` | — | Don't interrupt currently playing sounds; return False if busy | |
| `SND_PURGE` | — | Stop all instances of the specified sound | Not supported on modern Windows ⚠️ verify |
| `SND_NOWAIT` | — | Return immediately if sound driver is busy | Not supported on modern Windows ⚠️ verify |
| `SND_APPLICATION` | — | Use application-specific registry alias | Combinable with `SND_ALIAS` |
| `SND_SENTRY` | — | Trigger SoundSentry accessibility event. Added in Python 3.14 | |
| `SND_SYSTEM` | — | Assign to system notification audio session. Added in Python 3.14 | |

---

## Common System Sound Aliases (SND_ALIAS)

Use with `SND_ALIAS` flag:

```python
winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS)
winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
winsound.PlaySound("SystemExit", winsound.SND_ALIAS)
winsound.PlaySound("SystemHand", winsound.SND_ALIAS)
winsound.PlaySound("SystemQuestion", winsound.SND_ALIAS)
winsound.PlaySound(".Default", winsound.SND_ALIAS)  # default sound
```

| Alias | Control Panel Name |
|-------|-------------------|
| `"SystemAsterisk"` | Asterisk |
| `"SystemExclamation"` | Exclamation |
| `"SystemExit"` | Exit Windows |
| `"SystemHand"` | Critical Stop |
| `"SystemQuestion"` | Question |
| `".Default"` | Default Beep |

---

## Flag Combination Examples

```python
# Play WAV file synchronously (blocks)
winsound.PlaySound("C:/sounds/beep.wav", winsound.SND_FILENAME)

# Play WAV file asynchronously (non-blocking)
winsound.PlaySound("C:/sounds/beep.wav", winsound.SND_FILENAME | winsound.SND_ASYNC)

# Play WAV file async; don't play default sound if file missing
winsound.PlaySound("C:/sounds/beep.wav",
                   winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

# Loop a sound until stopped (must be async)
winsound.PlaySound("C:/sounds/alert.wav",
                   winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
# ... later, to stop:
winsound.PlaySound(None, winsound.SND_PURGE)

# Play in-memory WAV bytes
with open("beep.wav", "rb") as f:
    data = f.read()
winsound.PlaySound(data, winsound.SND_MEMORY)  # synchronous only

# Play system alias
winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
```

---

## Known Gotchas

1. **`SND_MEMORY` + `SND_ASYNC` = RuntimeError** — asynchronous in-memory playback
   is not supported. Use a file path with `SND_FILENAME | SND_ASYNC` instead.

2. **`SND_LOOP` requires `SND_ASYNC`** — calling `SND_LOOP` without `SND_ASYNC` will
   block forever (infinite loop in the calling thread).

3. **`SND_FILENAME` and `SND_ALIAS` are mutually exclusive** — using both causes
   undefined behaviour or an error.

4. **Only WAV files supported** — `winsound` does NOT support MP3, OGG, FLAC, etc.
   Convert all sounds to WAV format.

5. **`SND_PURGE` deprecated on modern Windows** — docs note it is not supported on
   modern Windows versions. Use `PlaySound(None, winsound.SND_ASYNC)` to stop. ⚠️ verify

6. **File path must be absolute or accessible from CWD** — relative paths are resolved
   from the process working directory. Use `os.path.abspath()` to be safe.

7. **Only one async sound at a time** — `SND_ASYNC` replaces any currently playing
   async sound. Multiple simultaneous sounds are not supported.

8. **Windows only** — `import winsound` raises `ModuleNotFoundError` on Linux/macOS.
   Guard with `sys.platform == "win32"`.

9. **No volume control** — `winsound` has no volume API. Volume is set by system settings.

---

## Voice Commander Sound Design

Recommended WAV specs for minimum latency:
- Format: PCM WAV
- Sample rate: 44100 Hz (or match system)
- Bit depth: 16-bit
- Channels: Mono or stereo
- Duration: 0.1–0.5 seconds for feedback sounds

```python
# assets/sounds/ directory structure:
# start.wav  — brief ascending chime
# stop.wav   — brief descending chime
# miss.wav   — short error/buzz tone
```

---

## Complete Module Constants Summary

```python
import winsound

# Playback flags
winsound.SND_FILENAME    # sound is a WAV filename
winsound.SND_ALIAS       # sound is a registry alias
winsound.SND_MEMORY      # sound is bytes in memory
winsound.SND_ASYNC       # play asynchronously
winsound.SND_LOOP        # loop (requires SND_ASYNC)
winsound.SND_NODEFAULT   # no default sound if missing
winsound.SND_NOSTOP      # don't interrupt playing sounds
winsound.SND_PURGE       # stop named sound (deprecated on modern Windows)

# MessageBeep constants
winsound.MB_OK                # 0x00000000
winsound.MB_ICONASTERISK      # 0x00000040
winsound.MB_ICONEXCLAMATION   # 0x00000030
winsound.MB_ICONHAND          # 0x00000010
winsound.MB_ICONQUESTION      # 0x00000020
winsound.MB_ICONERROR         # alias for MB_ICONHAND
winsound.MB_ICONINFORMATION   # alias for MB_ICONASTERISK
winsound.MB_ICONSTOP          # alias for MB_ICONHAND
winsound.MB_ICONWARNING       # alias for MB_ICONEXCLAMATION
```

---

## Asynchronous vs Synchronous Comparison

| Mode | Blocks caller | Use case |
|------|--------------|----------|
| Synchronous (default) | Yes | Simple scripts, tests |
| `SND_ASYNC` | No | Real-time apps like Voice Commander |
| `SND_LOOP \| SND_ASYNC` | No | Alert loop until dismissed |

For Voice Commander, **always use `SND_ASYNC`** so the audio callback thread is
not blocked while processing transcription results.

---

## Error Handling

```python
import winsound

def safe_play(path: str) -> bool:
    """Play a WAV file, return False if it fails."""
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except RuntimeError as e:
        print(f"winsound error: {e}")
        return False

def stop_async_sound():
    """Stop any currently playing async waveform."""
    try:
        winsound.PlaySound(None, winsound.SND_ASYNC)
    except RuntimeError:
        pass
```

---

## WAV File Requirements

`winsound.PlaySound` only supports **standard PCM WAV** files:
- Container: RIFF WAV
- Encoding: PCM (uncompressed)
- Sample rate: any (44100, 22050, 16000 Hz all work)
- Bit depth: 8-bit or 16-bit
- Channels: mono or stereo

**Does NOT support:** MP3, OGG, FLAC, ADPCM-compressed WAV, or other formats.

Generate compatible WAV files:
```bash
# Using ffmpeg
ffmpeg -i input.mp3 -acodec pcm_s16le -ar 44100 output.wav

# Using Python (scipy)
from scipy.io import wavfile
import numpy as np
rate = 44100
data = np.sin(2 * np.pi * 440 * np.linspace(0, 0.5, rate // 2)).astype(np.float32)
wavfile.write("beep.wav", rate, data)
```

---

## Cited from

- https://docs.python.org/3/library/winsound.html — fetched 2026-04-19
