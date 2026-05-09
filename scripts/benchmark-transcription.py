"""Head-to-head latency benchmark: local faster-whisper vs remote whisper.cpp.

Usage:
    python scripts/benchmark-transcription.py [path/to/audio.wav]

Workflow:
  1. Health-check both backends (load + 1 warmup call each, uncounted).
  2. Run 3 timed requests through each healthy backend.
  3. Print per-run latencies, average, and RTF.

Warmup ensures:
  - Local: WhisperModel loaded into VRAM, CUDA kernels compiled.
  - Remote: TCP connection open, server hot (no cold-start penalty).

Any backend that fails health-check is skipped with a clear error.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tomllib

from voice_commander.transcriber import RemoteTranscriber, Transcriber

# ── config ────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "config.toml"

with _CONFIG_PATH.open("rb") as _f:
    _raw = tomllib.load(_f)

_tc = _raw.get("transcription", {})

_REMOTE_ENDPOINT = _tc.get("remote_endpoint_url", "")
_REMOTE_TIMEOUT_MS = int(_tc.get("remote_timeout_ms", 5000))
_MODEL_SIZE = _tc.get("model_size", "small.en")
_DEVICE = _tc.get("device", "cuda")
_COMPUTE_TYPE = _tc.get("compute_type", "float16")

N_RUNS = 3

# ── audio file ────────────────────────────────────────────────────────────────

_DEFAULTS = [
    _ROOT / "outputs" / "last_utterance.wav",
    _ROOT / "tests" / "fixtures" / "audio" / "recorded.wav",
]

wav_path = Path(sys.argv[1]) if len(sys.argv) > 1 else next(
    (p for p in _DEFAULTS if p.exists()), None
)

if wav_path is None or not wav_path.exists():
    sys.exit(
        "ERROR: no audio file found. Pass one as an argument.\nLooked in:\n"
        + "\n".join(f"  {p}" for p in _DEFAULTS)
    )

print(f"Audio    : {wav_path.name}  ({wav_path.stat().st_size:,} bytes)")
print(f"Runs     : {N_RUNS} (+ 1 warmup each, uncounted)")
print()


# ── helpers ───────────────────────────────────────────────────────────────────

def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms"


def _run_timed(transcriber: object, path: Path, n: int) -> list[float] | None:
    """Run *n* timed transcriptions. Returns list of elapsed seconds, or None on error."""
    results = []
    for i in range(n):
        t0 = time.perf_counter()
        result = transcriber.transcribe(path)  # type: ignore[union-attr]
        elapsed = time.perf_counter() - t0
        results.append(elapsed)
        print(f"  run {i+1}: {_ms(elapsed)}  →  {result.text!r}")
    return results


def _summarise(label: str, times: list[float], audio_dur_s: float | None) -> None:
    avg = sum(times) / len(times)
    lo, hi = min(times), max(times)
    print(f"  avg {_ms(avg)}  |  min {_ms(lo)}  |  max {_ms(hi)}", end="")
    if audio_dur_s and audio_dur_s > 0:
        rtf = avg / audio_dur_s
        print(f"  |  RTF {rtf:.2f}x", end="")
    print()


# ── probe audio duration for RTF ─────────────────────────────────────────────

try:
    import soundfile as sf
    info = sf.info(str(wav_path))
    audio_dur_s: float | None = info.duration
except Exception:
    audio_dur_s = None

# ── local backend ─────────────────────────────────────────────────────────────

print(f"=== LOCAL  ({_MODEL_SIZE}, {_DEVICE}, {_COMPUTE_TYPE}) ===")

local_times: list[float] | None = None
local = Transcriber(model_size=_MODEL_SIZE, device=_DEVICE, compute_type=_COMPUTE_TYPE)

try:
    print("  loading model (may take 5–30 s on first run)…", flush=True)
    t_load = time.perf_counter()
    local.load()
    print(f"  model loaded in {_ms(time.perf_counter() - t_load)}")

    print("  warmup (uncounted)…", flush=True)
    local.transcribe(wav_path)
    print("  warmup done")

    print(f"  running {N_RUNS} timed calls…")
    local_times = _run_timed(local, wav_path, N_RUNS)
    _summarise("local", local_times, audio_dur_s)

except Exception as exc:
    print(f"  UNHEALTHY — {exc}")
finally:
    local.unload()

print()

# ── remote backend ────────────────────────────────────────────────────────────

print(f"=== REMOTE ({_REMOTE_ENDPOINT}) ===")

remote_times: list[float] | None = None

if not _REMOTE_ENDPOINT:
    print("  SKIP — remote_endpoint_url not set in config.toml")
else:
    remote = RemoteTranscriber(endpoint_url=_REMOTE_ENDPOINT, timeout_ms=_REMOTE_TIMEOUT_MS)
    try:
        remote.load()

        print("  warmup (uncounted)…", flush=True)
        warmup_result = remote.transcribe(wav_path)
        if warmup_result.confidence == 0.0 and warmup_result.no_speech_prob == 1.0:
            raise RuntimeError("warmup returned empty result — server unreachable or erroring")
        print("  warmup done")

        print(f"  running {N_RUNS} timed calls…")
        remote_times = _run_timed(remote, wav_path, N_RUNS)
        _summarise("remote", remote_times, audio_dur_s)

    except Exception as exc:
        print(f"  UNHEALTHY — {exc}")
    finally:
        remote.unload()

# ── side-by-side summary ──────────────────────────────────────────────────────

if local_times and remote_times:
    local_avg = sum(local_times) / len(local_times)
    remote_avg = sum(remote_times) / len(remote_times)
    faster, slower = ("local", "remote") if local_avg < remote_avg else ("remote", "local")
    ratio = max(local_avg, remote_avg) / min(local_avg, remote_avg)
    print()
    print("=== VERDICT ===")
    print(f"  {faster} is {ratio:.2f}x faster on average")
    if audio_dur_s:
        print(f"  local  RTF: {local_avg / audio_dur_s:.2f}x")
        print(f"  remote RTF: {remote_avg / audio_dur_s:.2f}x")
