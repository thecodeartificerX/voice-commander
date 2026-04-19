# faster-whisper Reference

> Cited from: https://github.com/SYSTRAN/faster-whisper (2026-04-19)
> Cited from: https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py (2026-04-19)
> Cited from: https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py (2026-04-19)

---

## Overview

faster-whisper is a reimplementation of OpenAI's Whisper model using
[CTranslate2](https://github.com/OpenNMT/CTranslate2), which is up to 4× faster
than the original OpenAI Whisper on the same hardware while using less memory.
It supports CPU and CUDA inference, quantised compute types (int8, float16, int8_float16),
and includes built-in Silero VAD filtering to skip silent audio segments.

## Installation

```bash
pip install faster-whisper
```

### CUDA (GPU) Installation

For GPU inference you need matching NVIDIA runtime libraries.

**CUDA 12 (recommended — ctranslate2 ≥ 4.5.0):**
```bash
pip install faster-whisper
# Then install system-level or conda CUDA 12 libraries:
# cuBLAS for CUDA 12  (libcublas)
# cuDNN 9 for CUDA 12 (libcudnn9)
```

> **Important:** The latest versions of ctranslate2 support **CUDA 12 + cuDNN 9** only.
> For CUDA 11, downgrade: `pip install ctranslate2==3.24.0`
> For CUDA 12 + cuDNN 8, downgrade: `pip install ctranslate2==4.4.0`

Typical conda install:
```bash
conda install -c conda-forge cudatoolkit=12.x cudnn=9.x
pip install faster-whisper
```

## Minimal Working Example (Voice Commander)

```python
from faster_whisper import WhisperModel
import numpy as np

# Load model once at startup (Voice Commander uses "small.en" with CUDA)
model = WhisperModel(
    model_size_or_path="small.en",
    device="cuda",          # or "cpu"
    compute_type="float16", # int8 for CPU
    num_workers=1,
    cpu_threads=0,          # 0 = auto
)

# Transcribe 16 kHz mono float32 audio buffer
audio: np.ndarray  # shape (N,), dtype float32, 16 kHz

segments, info = model.transcribe(
    audio,
    beam_size=5,
    language="en",
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500),
    initial_prompt="voice command",
)

# CRITICAL: segments is a generator — iteration triggers actual transcription
full_text = " ".join(seg.text.strip() for seg in segments)
print(f"Detected language: {info.language} ({info.language_probability:.2f})")
print(f"Transcript: {full_text}")
```

---

## API Reference

### `WhisperModel.__init__`

```python
WhisperModel(
    model_size_or_path: str,
    device: str = "auto",
    device_index: Union[int, List[int]] = 0,
    compute_type: str = "default",
    cpu_threads: int = 0,
    num_workers: int = 1,
    download_root: Optional[str] = None,
    local_files_only: bool = False,
    files: dict = None,
    revision: Optional[str] = None,
    use_auth_token: Optional[Union[str, bool]] = None,
    **model_kwargs,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model_size_or_path` | str | — | Model name (`"tiny"`, `"base"`, `"small"`, `"medium"`, `"large-v1/v2/v3"`, `"turbo"`, `"distil-large-v3"`) or path to local model directory |
| `device` | str | `"auto"` | `"cuda"`, `"cpu"`, or `"auto"` (auto-selects CUDA if available) |
| `device_index` | int or list[int] | `0` | GPU device index or list of indices for multi-GPU |
| `compute_type` | str | `"default"` | `"float16"`, `"int8_float16"`, `"int8"`, `"float32"`, `"int16"`. `"float16"` recommended for CUDA, `"int8"` for CPU |
| `cpu_threads` | int | `0` | Number of threads for CPU inference; `0` = use all available |
| `num_workers` | int | `1` | Number of parallel workers for inference |
| `download_root` | str | None | Directory for downloaded model files (defaults to `~/.cache/huggingface/hub`) |
| `local_files_only` | bool | False | If True, do not download; only use local files |
| `files` | dict | None | Override specific model files |
| `revision` | str | None | HuggingFace model revision/commit |
| `use_auth_token` | str or bool | None | HuggingFace token for private models |

**Model name shortcuts:**
- `"small.en"` — English-only small model (~244 MB)
- `"turbo"` — Large-v3 Turbo (speed/quality balance)
- `"distil-large-v3"` — Distilled large-v3 (fast)

---

### `WhisperModel.transcribe`

```python
model.transcribe(
    audio: Union[str, BinaryIO, np.ndarray],
    language: Optional[str] = None,
    task: str = "transcribe",
    log_progress: bool = False,
    beam_size: int = 5,
    best_of: int = 5,
    patience: float = 1,
    length_penalty: float = 1,
    repetition_penalty: float = 1,
    no_repeat_ngram_size: int = 0,
    temperature: Union[float, List[float], Tuple[float, ...]] = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    compression_ratio_threshold: Optional[float] = 2.4,
    log_prob_threshold: Optional[float] = -1.0,
    no_speech_threshold: Optional[float] = 0.6,
    condition_on_previous_text: bool = True,
    prompt_reset_on_temperature: float = 0.5,
    initial_prompt: Optional[Union[str, Iterable[int]]] = None,
    prefix: Optional[str] = None,
    suppress_blank: bool = True,
    suppress_tokens: Optional[List[int]] = [-1],
    without_timestamps: bool = False,
    max_initial_timestamp: float = 1.0,
    word_timestamps: bool = False,
    prepend_punctuations: str = "\"'¿([{-",
    append_punctuations: str = "\"'.。,，!！?？:：\")]}、",
    multilingual: bool = False,
    vad_filter: bool = False,
    vad_parameters: Optional[Union[dict, VadOptions]] = None,
    max_new_tokens: Optional[int] = None,
    chunk_length: Optional[int] = None,
    clip_timestamps: Union[str, List[float]] = "0",
    hallucination_silence_threshold: Optional[float] = None,
    hotwords: Optional[str] = None,
    language_detection_threshold: Optional[float] = 0.5,
    language_detection_segments: int = 1,
) -> Tuple[Iterable[Segment], TranscriptionInfo]
```

**Key parameters for Voice Commander:**

| Parameter | Default | Notes |
|-----------|---------|-------|
| `audio` | — | File path, file object, or numpy float32 array (16 kHz mono) |
| `language` | None | `"en"` to skip language detection; None auto-detects |
| `beam_size` | 5 | Higher = more accurate but slower. 5 is the sweet spot |
| `vad_filter` | False | Enable Silero VAD — skips silent chunks, speeds up transcription |
| `vad_parameters` | None | Dict or `VadOptions`; see VadOptions section below |
| `initial_prompt` | None | Text prompt prepended to guide the model's vocabulary/style |
| `condition_on_previous_text` | True | Use prior segment text as context |
| `no_speech_threshold` | 0.6 | Segments with no-speech probability above this are skipped |
| `word_timestamps` | False | Return word-level timestamps |
| `temperature` | [0,0.2,...1.0] | Fallback temperatures list; uses first successful one |
| `task` | "transcribe" | `"transcribe"` or `"translate"` (translate to English) |
| `hotwords` | None | Comma-separated words to boost in recognition |

**Return value:**
```python
segments: Iterable[Segment]   # generator — lazy evaluation!
info: TranscriptionInfo
```

Segment fields:
```python
segment.id          # int
segment.seek        # int
segment.start       # float (seconds)
segment.end         # float (seconds)
segment.text        # str
segment.tokens      # List[int]
segment.avg_logprob # float
segment.compression_ratio  # float
segment.no_speech_prob     # float
segment.words       # Optional[List[Word]] (if word_timestamps=True)
```

TranscriptionInfo fields:
```python
info.language               # str, e.g. "en"
info.language_probability   # float 0-1
info.duration               # float (seconds)
info.duration_after_vad     # float (seconds after VAD filtering)
info.all_language_probs     # Optional[List[Tuple[str, float]]]
info.transcription_options  # TranscriptionOptions
info.vad_options            # Optional[VadOptions]
```

---

### `VadOptions` Dataclass

Used as `vad_parameters` in `transcribe()`. Can pass as dict or `VadOptions(...)`.

```python
@dataclass
class VadOptions:
    threshold: float = 0.5
    neg_threshold: Optional[float] = None      # defaults to max(threshold - 0.15, 0.01)
    min_speech_duration_ms: int = 0
    max_speech_duration_s: float = float("inf")
    min_silence_duration_ms: int = 2000
    speech_pad_ms: int = 400
    min_silence_at_max_speech: int = 98
    use_max_poss_sil_at_max_speech: bool = True
```

| Field | Default | Description |
|-------|---------|-------------|
| `threshold` | 0.5 | Speech probability cutoff; above = speech |
| `neg_threshold` | auto | Silence floor; used to determine speech end |
| `min_speech_duration_ms` | 0 | Minimum speech segment duration in ms |
| `max_speech_duration_s` | inf | Maximum speech segment duration in seconds |
| `min_silence_duration_ms` | 2000 | Minimum silence to split segments |
| `speech_pad_ms` | 400 | Padding added to both sides of speech chunks |

**Voice Commander recommended:**
```python
vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=200)
```

---

## CUDA Setup Notes

### Windows (cuDNN + cuBLAS)

1. Install CUDA Toolkit 12.x from NVIDIA
2. Install cuDNN 9.x (for CUDA 12) — place DLLs in CUDA bin path
3. Verify: `python -c "import ctranslate2; print(ctranslate2.__version__)"`

### Required DLLs on Windows (CUDA 12 + cuDNN 9)
- `cublas64_12.dll`
- `cublasLt64_12.dll`
- `cudnn_ops64_9.dll`
- `cudnn_cnn_infer64_9.dll` ⚠️ verify current cuDNN 9 naming

### Compute type selection
| Hardware | Recommended compute_type |
|----------|------------------------|
| NVIDIA GPU (CUDA) | `"float16"` or `"int8_float16"` |
| CPU only | `"int8"` |
| CPU high accuracy | `"float32"` |

---

## Known Gotchas

1. **Segments are a generator** — the transcription does NOT run until you iterate.
   Always consume the generator immediately or collect into a list:
   ```python
   segments, info = model.transcribe(audio, ...)
   result = list(segments)  # forces full transcription NOW
   ```

2. **Audio format** — must be float32 numpy array, shape `(N,)`, sample rate 16000 Hz.
   If your buffer is int16: `audio = audio.astype(np.float32) / 32768.0`

3. **`vad_filter=True` changes duration** — `info.duration_after_vad` will differ from
   `info.duration` as silent sections are removed.

4. **`condition_on_previous_text=True`** can cause hallucination loops on long silences.
   Consider setting `False` for push-to-talk use cases.

5. **CUDA + ctranslate2 version mismatch** — most common install failure. Always check:
   ```bash
   python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"
   ```

6. **`initial_prompt` is prepended text** — it does NOT prevent the model from producing
   other output. Use it to bias towards expected vocabulary (command names).

7. **Multi-GPU** — set `device_index=[0, 1]` to distribute; requires `num_workers > 1`.

8. **Thread safety** — `WhisperModel` is NOT thread-safe. Create one instance per thread
   or use a queue pattern.

---

## Cited from

- https://github.com/SYSTRAN/faster-whisper (README.md) — fetched 2026-04-19
- https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py — fetched 2026-04-19
- https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py — fetched 2026-04-19
