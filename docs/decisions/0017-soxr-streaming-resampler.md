# ADR 0017: soxr for Streaming Audio Resampling

**Status:** Accepted
**Date:** 2026-04-20

## Context

The audio capture layer (ADR 0003) opens a `sounddevice` stream at the device's native sample rate — typically 48 kHz on WASAPI devices. silero-vad (ADR 0016) requires 16 kHz mono input. faster-whisper (ADR 0004) also expects 16 kHz internally.

In the single-shot model this was not a concern: `sounddevice.rec()` captured a complete buffer, and faster-whisper's internal resampler handled the 48→16 kHz conversion at inference time. In streaming mode the VAD worker must process audio frame-by-frame as it arrives; it cannot wait for a complete recording to resample in batch.

A streaming resampler is therefore needed on the path between the sounddevice callback and the VAD worker. Requirements:

- True streaming API: accepts arbitrary-length input chunks, maintains internal state between calls, emits correctly-sized output chunks.
- Deterministic output: no aliasing, no jitter from chunk-boundary effects.
- Low latency: <1 ms per 20 ms input chunk.
- Pre-built Windows wheels for Python 3.12.

Three candidates were evaluated.

**scipy.signal.resample_poly**
Stateless polyphase resampler. Correct output but stateless: each call is independent. To process a stream without seam artifacts, the caller must manage overlap-save buffering manually — adding ~30 lines of stateful wrapper code and requiring careful alignment of chunk sizes. Not a streaming API by design.

**librosa.resample**
Batch-oriented. Requires the entire signal to be resident in memory, uses `scipy` or `samplerate` under the hood. No chunk-wise API; unsuitable for streaming. Also pulls in a large transitive dependency tree (audioread, soundfile, numba) for a use case that needs only resampling.

**soxr (python-soxr)**
Python bindings for libsoxr (Secret Rabbit Code), the resampling library used in SoX, FFmpeg, and libsamplerate. Exposes `soxr.ResampleStream` — a stateful object with a `process(chunk) -> output` method that maintains internal filter state across calls. Quality presets from `QUICK` to `VHQ`; `HQ` gives alias suppression > 100 dB with < 0.5 ms latency per 20 ms chunk on modern CPUs. Pre-built Windows wheels on PyPI for Python 3.9–3.12.

## Decision

Use **`soxr.ResampleStream`** at `HQ` quality for all 48 kHz → 16 kHz conversion on the streaming path.

```python
import soxr
_resampler = soxr.ResampleStream(
    in_rate=device_samplerate,   # e.g. 48000
    out_rate=16000,
    num_channels=1,
    quality="HQ",
)
# In the audio callback:
resampled = _resampler.process(chunk, last=False)
```

The resampler instance is created once when the session opens and discarded when the session closes. One instance per session; not shared across threads.

## Consequences

### Positive
- True streaming API with internal state: no manual overlap management, no chunk-alignment constraints.
- `HQ` quality: > 100 dB alias suppression, flat passband to 7.9 kHz (sufficient for speech at 16 kHz Nyquist).
- <1 ms per 20 ms chunk on CPU — well within the VAD worker's time budget.
- Pre-built wheels; zero native build step for users.

### Negative
- Adds `soxr` / `python-soxr` to the dependency set (~300 KB installed, libsoxr bundled in the wheel).
- `ResampleStream` is not thread-safe; one instance per session/thread (already the design).
- If the device sample rate is already 16 kHz (unusual but possible), an identity resample wastes ~0.1 ms per chunk. Mitigation: skip instantiation when `in_rate == out_rate`.

### Neutral
- faster-whisper's internal resampler is still invoked for the final ndarray handoff (ADR 0018); soxr handles the streaming path for VAD only. They operate at different stages of the pipeline and do not conflict.

## Alternatives considered

### scipy.signal.resample_poly
Rejected: stateless, requires manual overlap-save wrapper, not designed for streaming.

### librosa.resample
Rejected: batch-only API, heavy dependency tree, no streaming path.

## References
- python-soxr: https://github.com/dofuuz/python-soxr
- soxr PyPI: https://pypi.org/project/soxr/
- libsoxr: https://sourceforge.net/projects/soxr/
- ADR 0016 (silero-vad): `0016-silero-vad-over-webrtcvad.md`
- ADR 0018 (ndarray handoff to Whisper): `0018-ndarray-handoff-to-whisper.md`
