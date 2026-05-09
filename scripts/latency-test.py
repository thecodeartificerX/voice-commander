"""One-shot latency probe for the remote whisper.cpp transcription server.

Usage:
    python scripts/latency-test.py [path/to/audio.wav]

Defaults to outputs/last_utterance.wav, then tests/fixtures/audio/recorded.wav.
Prints round-trip latency, audio duration, and the transcript returned.
"""

import sys
import time
from pathlib import Path

import httpx

# ── config ────────────────────────────────────────────────────────────────────

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-reuse-import]

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "config.toml"

with _CONFIG_PATH.open("rb") as _f:
    _cfg = tomllib.load(_f)

_ENDPOINT = _cfg["transcription"].get("remote_endpoint_url", "")
_TIMEOUT_MS = int(_cfg["transcription"].get("remote_timeout_ms", 5000))

if not _ENDPOINT:
    sys.exit("ERROR: transcription.remote_endpoint_url is empty in config.toml")

# ── audio file ────────────────────────────────────────────────────────────────

_DEFAULTS = [
    _ROOT / "outputs" / "last_utterance.wav",
    _ROOT / "tests" / "fixtures" / "audio" / "recorded.wav",
]

if len(sys.argv) > 1:
    wav_path = Path(sys.argv[1])
else:
    wav_path = next((p for p in _DEFAULTS if p.exists()), None)

if wav_path is None or not wav_path.exists():
    sys.exit(f"ERROR: no audio file found. Pass one as an argument.\nLooked in:\n" +
             "\n".join(f"  {p}" for p in _DEFAULTS))

wav_bytes = wav_path.read_bytes()

# ── probe ─────────────────────────────────────────────────────────────────────

read_s = max(0.5, _TIMEOUT_MS / 1000.0 - 0.5)
timeout = httpx.Timeout(connect=0.5, read=read_s, write=2.0, pool=2.0)

print(f"Endpoint : {_ENDPOINT}")
print(f"Payload  : {wav_path.name}  ({len(wav_bytes):,} bytes)")
print(f"Timeout  : {_TIMEOUT_MS} ms")
print()

with httpx.Client(timeout=timeout, http2=False) as client:
    files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    data = {"response_format": "verbose_json", "language": "en"}

    t0 = time.perf_counter()
    try:
        resp = client.post(_ENDPOINT, files=files, data=data)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        resp.raise_for_status()
        payload = resp.json()
    except httpx.TimeoutException as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        sys.exit(f"TIMEOUT after {elapsed_ms:.0f} ms — {exc}")
    except httpx.ConnectError as exc:
        sys.exit(f"CONNECT ERROR — {exc}")
    except httpx.HTTPStatusError as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        sys.exit(f"HTTP {exc.response.status_code} after {elapsed_ms:.0f} ms\n{exc.response.text[:400]}")

text = payload.get("text", "").strip()
duration = payload.get("duration", None)

print(f"Latency  : {elapsed_ms:.0f} ms")
if duration is not None:
    print(f"Audio dur: {float(duration)*1000:.0f} ms")
    print(f"RTF      : {elapsed_ms / (float(duration)*1000):.2f}x  (latency / audio_duration)")
print(f"Transcript: {text!r}")
