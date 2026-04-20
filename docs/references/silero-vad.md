# Silero VAD Reference

**Source:** [snakers4/silero-vad](https://github.com/snakers4/silero-vad) (GitHub)  
**PyPI:** [silero-vad](https://pypi.org/project/silero-vad/)  
**PyTorch Hub:** [Silero VAD](https://pytorch.org/hub/snakers4_silero-vad_vad/)  
**Wiki:** [snakers4/silero-vad Wiki](https://github.com/snakers4/silero-vad/wiki)

---

## Installation & Dependencies

### Package Installation

```bash
pip install silero-vad
```

### What's Included

- **ONNX model**: ~2 MB, pre-trained on 6000+ languages
- **Support for both PyTorch and ONNX backends** (auto-selected based on availability)
- Model supports **8000 Hz and 16000 Hz sample rates only**

### Runtime Dependencies

```
torch ≥1.12.0
torchaudio ≥0.12.0
onnxruntime ≥1.16.1
```

**Audio backend** (choose one):
- `pip install soundfile` (recommended for Windows)
- `conda install -c conda-forge 'ffmpeg<7'`
- `apt-get install sox` (Linux)

### System Requirements

- Python 3.8+
- 1 GB+ RAM
- Modern CPU with AVX/AVX2/AVX-512 instruction sets
- ONNX Runtime: no GPU variant needed for CPU-only inference

---

## Public API

### `load_silero_vad()`

Load the pre-trained model from PyTorch Hub:

```python
import torch
from silero_vad import load_silero_vad

torch.set_num_threads(1)  # Recommended: single-thread CPU-only optimization

model = load_silero_vad(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    force_reload=False,  # Set True only on first run
    onnx=True            # Use ONNX backend if available (faster on CPU)
)
```

**Returns:** Pre-trained model object compatible with both `get_speech_timestamps()` and `VADIterator`.

### `get_speech_timestamps(wav, model, sampling_rate=16000, ...)`

Batch inference on complete audio file:

```python
from silero_vad import load_silero_vad, get_speech_timestamps, read_audio

model = load_silero_vad()
wav = read_audio('audio.wav', sampling_rate=16000)

speech_timestamps = get_speech_timestamps(
    wav,
    model,
    sampling_rate=16000,
    threshold=0.5,
    min_speech_duration_ms=250,
    min_silence_duration_ms=100,
    speech_pad_ms=30,
    return_seconds=True  # If False, timestamps are in samples
)
# Returns: [{'start': 0.5, 'end': 3.2}, {'start': 5.1, 'end': 7.8}, ...]
```

### `VADIterator` (Streaming Pattern)

For real-time/streaming audio, use `VADIterator`:

```python
from silero_vad import VADIterator

vad_iterator = VADIterator(
    model,
    sampling_rate=16000,
    threshold=0.5,
    min_speech_duration_ms=250,
    min_silence_duration_ms=100,
    speech_pad_ms=30,
    return_seconds=True
)
```

**Streaming usage:**

```python
import torch

vad_iterator.reset_states()  # Call once at stream start

while audio_chunks_available:
    # Read 32ms chunk (512 samples at 16kHz, 256 at 8kHz)
    chunk = read_next_audio_chunk()  # torch.Tensor, shape (512,), dtype float32, range [-1, 1]
    
    speech = vad_iterator(chunk)
    
    # speech is either:
    # - None (no speech event)
    # - {'start': 0.5} (speech started)
    # - {'end': 2.1} (speech ended)
    
    if speech:
        if 'start' in speech:
            print(f"Speech started at {speech['start']}s")
        if 'end' in speech:
            print(f"Speech ended at {speech['end']}s")

vad_iterator.reset_states()  # Call at stream end
```

---

## Input Format & Data Requirements

### Sample Rate
- **Only 8000 Hz or 16000 Hz** — no other rates supported
- Model will fail silently or raise error on other rates

### Frame / Chunk Size
- **Must be exactly 32 ms** of audio per inference
- At **16000 Hz**: 512 samples = 32 ms
- At **8000 Hz**: 256 samples = 32 ms

### Audio Format
- **dtype**: `float32` (PyTorch tensor or NumPy array)
- **range**: `[-1.0, 1.0]` (normalized PCM)
- **channels**: **Mono only** (1D array/tensor, shape `(512,)` or `(256,)`)

### Converting from int16 to float32

```python
import numpy as np

def int16_to_float32(audio_int16):
    """Convert int16 PCM to float32 [-1, 1] normalized."""
    audio_float32 = np.copy(audio_int16)
    abs_max = np.abs(audio_float32).max()
    audio_float32 = audio_float32.astype('float32')
    if abs_max > 0:
        audio_float32 *= 1.0 / abs_max
    return audio_float32.squeeze()  # Ensure mono
```

---

## Default Parameters & Tuning

### Parameter Defaults (from library source)

| Parameter | Default | Unit | Description |
|---|---|---|---|
| `threshold` | 0.5 | probability | Speech probability above this = speech. Range [0, 1]. |
| `min_speech_duration_ms` | 250 | ms | Minimum duration audio must be classified as speech before segment starts. |
| `min_silence_duration_ms` | 100 | ms | Duration of silence required at segment end before finalizing. |
| `speech_pad_ms` | 30 | ms | Padding added to both sides of detected speech segment. |
| `window_size_samples` | 512 | samples | Fixed chunk size (512 @ 16kHz, 256 @ 8kHz). **Do not change.** |

### Tuning Guidance

**Threshold (0.5 recommended)**
- Values closer to 0.0 = more speech detected (higher false-positive rate)
- Values closer to 1.0 = fewer false positives but may miss quiet speech
- Start at **0.5** and adjust empirically for your noise floor
- Discussion #471 shows v5 model is much more robust than earlier versions

**Min Speech Duration (default 250 ms)**
- Prevents spurious detections of < 0.25s noise bursts
- For command-and-control: **250 ms is a good minimum** (typical spoken command)
- Shorter (e.g. 100 ms) risks false positives on background noise clicks

**Min Silence Duration (default 100 ms)**
- How long to wait for silence before concluding a speech segment
- For interactive commands: **100 ms is appropriate** (conversational pause detection)
- Longer (e.g. 500 ms) delays response; shorter risks cutting off trailing speech

**Speech Pad (default 30 ms)**
- Useful for transcription pipelines to include context
- For VAD-only use: **30 ms is standard**; adjust if transcriber needs more/less context

### Voice Commander Recommendations

For the MVP (Phase 3), use:
```python
VADIterator(
    model,
    sampling_rate=16000,
    threshold=0.5,           # Start conservative
    min_speech_duration_ms=250,
    min_silence_duration_ms=100,
    speech_pad_ms=30,
    return_seconds=True
)
```

After Phase 4 (with real command phrases), collect ~50 audio samples and tune threshold/min_speech_duration_ms to minimize false positives while capturing all valid commands.

---

## Streaming Implementation Pattern

### Complete Example (PyAudio + Streaming)

```python
import torch
import pyaudio
import numpy as np
from silero_vad import load_silero_vad, VADIterator

# Load model once
torch.set_num_threads(1)
model = load_silero_vad(onnx=True)

# Initialize VAD
vad_iterator = VADIterator(
    model,
    sampling_rate=16000,
    threshold=0.5,
    min_speech_duration_ms=250,
    min_silence_duration_ms=100,
    speech_pad_ms=30,
    return_seconds=True
)

# PyAudio setup
p = pyaudio.PyAudio()
stream = p.open(
    format=pyaudio.paFloat32,
    channels=1,
    rate=16000,
    input=True,
    frames_per_buffer=512,
    device=None  # Use default device
)

# Stream loop
stream.start_stream()
vad_iterator.reset_states()

try:
    while True:
        # Read exactly 512 samples (32 ms @ 16kHz)
        audio_chunk_bytes = stream.read(512, exception_on_overflow=False)
        audio_chunk = np.frombuffer(audio_chunk_bytes, dtype=np.float32)
        
        # Feed to VAD
        speech = vad_iterator(audio_chunk)
        
        if speech:
            if 'start' in speech:
                print(f"[{speech['start']:.2f}s] Speech detected")
            if 'end' in speech:
                print(f"[{speech['end']:.2f}s] Speech ended")
finally:
    vad_iterator.reset_states()
    stream.stop_stream()
    stream.close()
    p.terminate()
```

### Key Points for Streaming

1. **Always call `reset_states()` before/after streaming** — VADIterator maintains internal state across chunks.
2. **Feed exactly frame_size samples per call** — 512 @ 16kHz, 256 @ 8kHz.
3. **Check return value each iteration**:
   - `None` = no speech transition
   - `{'start': t}` = speech segment started
   - `{'end': t}` = speech segment ended
4. **Timestamps are relative to stream start** if `return_seconds=True`.

---

## Performance Characteristics

### CPU Performance

- **Per-chunk latency**: < 1 ms per 32 ms frame on modern single-thread CPU
- **Throughput**: ~30x real-time on CPU with AVX2 (300 ms audio in ~10 ms wall-clock)
- **Extreme case**: 5071 seconds PCM audio processed in 6.4 seconds wall-clock (~800x speedup)

### Model Size & Memory

- **Model file**: ~2 MB on disk
- **Runtime memory**: ~50 MB peak (model + buffers)
- **RAM requirement**: 1 GB+ recommended

### ONNX Runtime Configuration (CPU)

Silero VAD internally uses:
```python
session_opts = onnxruntime.SessionOptions()
session_opts.inter_op_num_threads = 1
session_opts.intra_op_num_threads = 1
session_opts.execution_providers = ['CPUExecutionProvider']
```

This is **optimal for single-threaded, low-latency streaming**. Do not adjust unless benchmarking shows improvement for your hardware.

### Thread Safety

- **Model is NOT thread-safe** — load once on main thread; if multi-threaded, use thread-local or mutex
- **VADIterator is NOT thread-safe** — create one per audio stream

---

## ONNX Runtime & Windows-Specific Notes

### Installation

```bash
pip install onnxruntime  # CPU version (sufficient for Windows)
```

- `onnxruntime` package handles Windows CPU inference automatically
- No separate CUDA toolkit or native DLLs needed
- ONNX runtime automatically selects CPU providers on Windows

### ONNX vs PyTorch Backend

Silero VAD auto-selects the best backend:

1. If `onnxruntime` installed → uses ONNX (faster on CPU)
2. If only PyTorch available → uses PyTorch backend

**Recommendation**: Install both for maximum compatibility. ONNX is ~2-3x faster on CPU.

### Windows-Specific Gotchas

1. **PyAudio on Windows**: May require pre-compiled wheels from [unofficial source](https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio)
   - Alternative: Use `sounddevice` package instead (pip-installable, no wheels needed)

2. **Float32 PCM from DirectSound/WASAPI**: Ensure audio is truly float32 [-1, 1], not int16 clipped
   - Verify with: `audio.dtype == np.float32 and audio.min() >= -1.0 and audio.max() <= 1.0`

3. **Sample rate mismatch**: If device outputs 48 kHz, resample to 16 kHz before VAD
   ```python
   import torchaudio
   resampler = torchaudio.transforms.Resample(48000, 16000)
   audio_16k = resampler(torch.tensor(audio_48k))
   ```

---

## Reference Implementations

### LiveKit Plugin (Production Example)

The [LiveKit Silero VAD plugin](https://docs.livekit.io/reference/python/livekit/plugins/silero/index.html) provides:
- Async streaming wrapper with frame buffering
- Threshold hysteresis (activation vs. deactivation thresholds)
- Automatic resampling from arbitrary input rates

Recommended reading for production streaming patterns.

### ONNX-Only Wrapper

For zero PyTorch dependencies, see [py-silero-vad-lite](https://github.com/daanzu/py-silero-vad-lite) (external, not official).

---

## Known Issues & Discussions

### Issue #518: Max Speech Duration

For long-form speech (> 1 minute), VADIterator may not properly finalize segments without explicit `reset_states()` call between logical breaks. Not applicable for command-and-control (sub-5s utterances).

### Issue #376: Onnxruntime Version Compatibility

Some onnxruntime 1.16.0 versions had binding issues. Use `>=1.16.1` to avoid. Verify installation:
```python
import onnxruntime
print(onnxruntime.__version__)  # Should be ≥ 1.16.1
```

### Thread Count Tuning

For workloads with many concurrent streams, do not blindly increase `inter_op_num_threads` or `intra_op_num_threads`. Silero VAD is optimized for `threads=1`. Profile your workload first.

---

## Integration with Voice Commander

### Phase 3 Plan (Router + VAD)

1. **Load model once in `Daemon.__init__()`**:
   ```python
   self.vad_model = load_silero_vad(onnx=True)
   ```

2. **Create VADIterator in transcriber thread**:
   ```python
   self.vad = VADIterator(
       self.vad_model,
       sampling_rate=16000,
       threshold=0.5,
       min_speech_duration_ms=250,
       min_silence_duration_ms=100,
       speech_pad_ms=30,
       return_seconds=True
   )
   ```

3. **Feed audio from Recorder directly to VAD**:
   - Recorder outputs 16-bit int PCM @ 16 kHz
   - Convert to float32 [-1, 1] in 512-sample chunks
   - Pass to VAD; emit `'speech_detected'` event on `{'start': ...}` and `'speech_ended'` on `{'end': ...}`

4. **Integrate feedback**:
   - Emit winsound chime on `'speech_detected'`
   - Log segment timestamps for debugging

### Phase 4 Plan (Full MVPs)

- Fine-tune threshold/min_speech_duration_ms on actual command phrases
- Consider separate VAD thresholds per tool (e.g., lower threshold for short commands like "copy")

---

## Testing & Validation

### Unit Test Pattern

```python
import pytest
import torch
from silero_vad import load_silero_vad, VADIterator, get_speech_timestamps

@pytest.fixture
def vad_model():
    return load_silero_vad(onnx=True)

def test_vad_detects_silence(vad_model):
    silence = torch.zeros(512, dtype=torch.float32)
    vad = VADIterator(vad_model, threshold=0.5)
    vad.reset_states()
    result = vad(silence)
    assert result is None  # No speech

def test_vad_detects_speech_event(vad_model):
    # Load real speech sample; verify detection
    wav = load_speech_sample()  # Must be 16kHz
    vad = VADIterator(vad_model, threshold=0.5)
    vad.reset_states()
    
    events = []
    for chunk in chunk_audio(wav, 512):
        speech = vad(chunk)
        if speech:
            events.append(speech)
    
    assert any('start' in e for e in events)
    assert any('end' in e for e in events)
```

### Integration Test Pattern

Record 5-second test utterances (silence → "copy" → silence) and verify:
1. No false positives in silence regions
2. Speech segment detected covers actual utterance
3. Latency < 100 ms from end of speech to `{'end': ...}` event

---

## Sources

- [Silero VAD GitHub](https://github.com/snakers4/silero-vad)
- [PyPI: silero-vad](https://pypi.org/project/silero-vad/)
- [PyTorch Hub: Silero VAD](https://pytorch.org/hub/snakers4_silero-vad_vad/)
- [Silero VAD Wiki: Examples and Dependencies](https://github.com/snakers4/silero-vad/wiki/Examples-and-Dependencies)
- [LiveKit Silero Plugin](https://docs.livekit.io/reference/python/livekit/plugins/silero/index.html)
- [GitHub Discussion #471: v5 release](https://github.com/snakers4/silero-vad/discussions/471)
- [Audio Streaming Support Discussion #55](https://github.com/snakers4/silero-vad/discussions/55)
