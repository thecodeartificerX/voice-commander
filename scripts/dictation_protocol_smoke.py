"""Smoke test for the ADR 0096 server-side dictation WebSocket protocol.

Connects to ws://192.168.4.200:8767/ws/transcribe, sends raw 16 kHz mono
float32 PCM in ~1-second binary chunks, sends {"type":"end"}, then awaits
exactly one {"type":"done",...} frame within 60 s.

Protocol under test (ADR 0096 D1/D2):
  - NO config frame, NO partial frames, NO segments.
  - Binary frames: raw float32 PCM at 16 kHz mono (64 kB per second).
  - End signal: JSON {"type":"end"}.
  - Server response: ONE JSON {"type":"done","text":"<cleaned>","raw":"<raw>"}.
  - Empty/silence: {"type":"done","text":"","raw":""}.
  - Cap exceeded: {"type":"error","message":"max duration exceeded"}.

Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import wave

import numpy as np
import soxr

WS_URL = "ws://192.168.4.200:8767/ws/transcribe"
TARGET_SR = 16_000
CHUNK_SAMPLES = 16_000  # 1-second chunks at 16 kHz
DONE_TIMEOUT_S = 60.0

# ---------------------------------------------------------------------------
# Audio source resolution (priority order per spec)
# ---------------------------------------------------------------------------

CANDIDATE_WAVS = [
    # Real speech candidates — prefer longer / higher-quality ones first.
    "tests/test-audio/test-wav.wav",       # 12.03 s, 44100 Hz — best candidate
    "outputs/dictation/last.wav",           # 8.83 s, 16 kHz — already target SR
    "tests/fixtures/audio/recorded.wav",    # 3.52 s, 44100 Hz — short but real speech
    "outputs/recorded.wav",                 # 1.72 s — too short but usable
]

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..")
)
SMOKE_DIR = os.path.join(REPO_ROOT, "outputs", "smoke")
SAPI_WAV = os.path.join(SMOKE_DIR, "sample.wav")


def _load_wav_as_float32(path: str) -> tuple[np.ndarray, int]:
    """Read a WAV file and return (float32_samples, sample_rate).

    Converts integer PCM to float32 in [-1.0, 1.0]. Raises on failure.
    """
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # Decode PCM bytes to numpy int array.
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    if sampwidth not in dtype_map:
        raise ValueError(f"Unsupported sample width {sampwidth} bytes in {path}")
    samples = np.frombuffer(raw, dtype=dtype_map[sampwidth])

    # Mix down to mono if stereo.
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)

    # Normalise to float32 [-1, 1].
    max_val = float(2 ** (sampwidth * 8 - 1))
    audio = samples.astype(np.float32) / max_val
    return audio, sr


def _resample_to_16k(audio: np.ndarray, src_sr: int) -> np.ndarray:
    """Resample mono float32 audio to 16 000 Hz using soxr (high quality)."""
    if src_sr == TARGET_SR:
        return audio
    return soxr.resample(audio, src_sr, TARGET_SR, quality="HQ").astype(np.float32)


def _find_repo_wav() -> tuple[str, np.ndarray] | None:
    """Return (path, float32_16k_audio) for the first usable repo WAV, or None."""
    MIN_DURATION_S = 1.5  # must be at least this long to be worth sending

    for rel in CANDIDATE_WAVS:
        abs_path = os.path.join(REPO_ROOT, rel)
        if not os.path.isfile(abs_path):
            continue
        try:
            audio, sr = _load_wav_as_float32(abs_path)
        except Exception as exc:
            print(f"  [warn] skipping {rel}: {exc}")
            continue

        duration = len(audio) / sr
        if duration < MIN_DURATION_S:
            print(f"  [warn] skipping {rel}: only {duration:.2f}s, too short")
            continue

        audio_16k = _resample_to_16k(audio, sr)
        return abs_path, audio_16k

    return None


def _generate_sapi_wav() -> bool:
    """Generate outputs/smoke/sample.wav via Windows SAPI. Returns True on success."""
    os.makedirs(SMOKE_DIR, exist_ok=True)
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f'$synth.SetOutputToWaveFile("{SAPI_WAV}"); '
        "$synth.Speak('The quick brown fox jumps over the lazy dog. "
        "This is a test of the new dictation server.'); "
        "$synth.Dispose()"
    )
    import subprocess

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"  [warn] SAPI failed: {result.stderr.strip()}")
        return False
    return os.path.isfile(SAPI_WAV) and os.path.getsize(SAPI_WAV) > 0


def resolve_audio() -> tuple[str, np.ndarray, int, float]:
    """Return (source_label, float32_mono_16k, sample_rate, duration_s).

    Priority:
      1. Real speech WAV from repo.
      2. Windows SAPI-generated WAV.
      3. 3 s of silence (last resort — protocol-only validation).
    """
    # 1. Repo WAV
    print("Searching for suitable WAV files in repo...")
    result = _find_repo_wav()
    if result is not None:
        path, audio = result
        dur = len(audio) / TARGET_SR
        return path, audio, TARGET_SR, dur

    # 2. SAPI
    print("No repo WAV found. Generating via Windows SAPI...")
    if _generate_sapi_wav():
        try:
            raw, sr = _load_wav_as_float32(SAPI_WAV)
            audio = _resample_to_16k(raw, sr)
            dur = len(audio) / TARGET_SR
            return SAPI_WAV, audio, TARGET_SR, dur
        except Exception as exc:
            print(f"  [warn] Failed to load generated WAV: {exc}")

    # 3. Silence fallback
    print("Falling back to 3 s of silence.")
    audio = np.zeros(TARGET_SR * 3, dtype=np.float32)
    return "<generated silence>", audio, TARGET_SR, 3.0


# ---------------------------------------------------------------------------
# WebSocket smoke test
# ---------------------------------------------------------------------------

async def run_smoke_test(audio: np.ndarray) -> dict:
    """Connect, stream PCM chunks, send end, await done. Return result dict."""
    # Slice into ~1-second chunks of CHUNK_SAMPLES float32 samples.
    total_samples = len(audio)
    chunks = []
    for offset in range(0, total_samples, CHUNK_SAMPLES):
        chunk = audio[offset : offset + CHUNK_SAMPLES]
        chunks.append(chunk)

    n_chunks = len(chunks)
    total_bytes = total_samples * 4  # float32 = 4 bytes
    print(f"Connecting to {WS_URL}")

    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    try:
        async with connect(WS_URL, open_timeout=15) as ws:
            print(f"Connected. Sending {n_chunks} chunk(s) "
                  f"({total_bytes / 1024:.1f} kB total)...")
            t0 = time.monotonic()
            for i, chunk in enumerate(chunks):
                await ws.send(chunk.tobytes())  # raw float32 bytes, no WAV header
                print(f"  chunk {i + 1}/{n_chunks}: {len(chunk)} samples "
                      f"({len(chunk) * 4} bytes)")
            t_sent = time.monotonic() - t0
            print(f"All chunks sent in {t_sent:.2f}s. Sending end frame.")

            await ws.send(json.dumps({"type": "end"}))
            print(f"Awaiting done frame ({DONE_TIMEOUT_S:.0f}s timeout)...")

            t1 = time.monotonic()
            try:
                raw_reply = await asyncio.wait_for(ws.recv(), timeout=DONE_TIMEOUT_S)
            except asyncio.TimeoutError:
                return {
                    "ok": False,
                    "error": f"Timed out after {DONE_TIMEOUT_S:.0f}s waiting for done frame.",
                    "t_send": t_sent,
                    "t_done": None,
                    "frame": None,
                }
            t_done = time.monotonic() - t1
            print(f"Done frame received in {t_done:.2f}s after end-send.")

            # Decode
            if isinstance(raw_reply, bytes):
                raw_reply = raw_reply.decode("utf-8")
            try:
                frame = json.loads(raw_reply)
            except json.JSONDecodeError as exc:
                return {
                    "ok": False,
                    "error": f"Reply is not valid JSON: {exc}. Raw: {raw_reply!r}",
                    "t_send": t_sent,
                    "t_done": t_done,
                    "frame": None,
                    "raw_reply": raw_reply,
                }

            return {
                "ok": True,
                "t_send": t_sent,
                "t_done": t_done,
                "frame": frame,
            }

    except ConnectionClosed as exc:
        return {
            "ok": False,
            "error": f"Connection closed unexpectedly: {exc}",
            "t_send": None,
            "t_done": None,
            "frame": None,
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"Connection failed (OSError): {exc}",
            "t_send": None,
            "t_done": None,
            "frame": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"Unexpected exception: {type(exc).__name__}: {exc}",
            "t_send": None,
            "t_done": None,
            "frame": None,
        }


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def check_assertions(result: dict) -> list[str]:
    """Return list of failure reasons (empty = all pass)."""
    failures = []

    if not result.get("ok"):
        failures.append(f"Connection/send/receive failed: {result.get('error')}")
        return failures  # can't assert on frame if we never got one

    frame = result.get("frame")
    if frame is None:
        failures.append("No frame received despite ok=True (internal error in smoke script).")
        return failures

    # 1. frame["type"] must be "done"
    frame_type = frame.get("type")
    if frame_type != "done":
        failures.append(
            f'Expected frame["type"] == "done", got {frame_type!r}. '
            f"Full frame: {json.dumps(frame)}"
        )

    # 2. "text" key must be present (may be empty string)
    if "text" not in frame:
        failures.append('frame["text"] key is absent — protocol violation (ADR 0096 D2).')

    # 3. No extra unexpected frame types as first response
    unexpected = {k: v for k, v in frame.items() if k not in ("type", "text", "raw")}
    if unexpected and frame_type == "done":
        # Extra keys are allowed (non-breaking), just warn
        print(f"  [info] done frame has extra keys (non-fatal): {list(unexpected.keys())}")

    return failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("ADR 0096 dictation protocol smoke test")
    print(f"Target: {WS_URL}")
    print("=" * 70)

    # Step 1: Resolve audio
    source_label, audio, sr, duration = resolve_audio()
    print(f"\nusing audio source: {source_label}, sample_rate={sr}, duration={duration:.2f}s")
    print(f"Total float32 samples: {len(audio):,}  ({len(audio) * 4 / 1024:.1f} kB)")

    # Step 2: Run async smoke test
    print()
    result = asyncio.run(run_smoke_test(audio))

    # Step 3: Print results
    print()
    print("-" * 70)
    print("RESULTS")
    print("-" * 70)

    if result.get("t_send") is not None:
        print(f"  Chunks sent in:        {result['t_send']:.3f}s")
    if result.get("t_done") is not None:
        print(f"  End-to-done latency:   {result['t_done']:.3f}s")

    frame = result.get("frame")
    if frame is not None:
        print(f"  Type:                  {frame.get('type')!r}")
        print(f"  Text (cleaned):        {frame.get('text')!r}")
        print(f"  Raw  (whisper):        {frame.get('raw')!r}")
    elif result.get("error"):
        print(f"  Error:                 {result['error']}")

    # Step 4: Assertions
    print()
    failures = check_assertions(result)
    if failures:
        print("ASSERTIONS FAILED:")
        for i, f in enumerate(failures, 1):
            print(f"  {i}. {f}")
        print()
        print("VERDICT: FAIL")
        return 1
    else:
        print("All assertions passed.")
        print()
        print("VERDICT: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
