"""Live smoke test: POST tests/test-audio/test-wav.wav to the whisper.cpp endpoint.

Verifies the endpoint is reachable and the multipart contract in
voice_commander.dictation.remote is correct. Run manually — needs the
192.168.4.200 host up.

Usage:  python scripts/dictation_remote_smoke.py [--endpoint URL]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_commander.config import Config  # noqa: E402
from voice_commander.dictation.remote import DictationRemoteError, post_audio  # noqa: E402

_WAV = Path(__file__).resolve().parents[1] / "tests" / "test-audio" / "test-wav.wav"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=None)
    args = parser.parse_args()

    endpoint = args.endpoint
    if endpoint is None:
        cfg_path = Path(__file__).resolve().parents[1] / "config.toml"
        endpoint = Config.load(cfg_path).dictation.endpoint

    if not _WAV.exists():
        print(f"FAIL: reference audio not found: {_WAV}")
        return 1

    wav_bytes = _WAV.read_bytes()
    print(f"POST {len(wav_bytes)} bytes -> {endpoint}")
    try:
        text = post_audio(wav_bytes, endpoint)
    except DictationRemoteError as e:
        print(f"FAIL: {e}")
        return 1

    if not text:
        print("FAIL: endpoint returned empty transcription")
        return 1

    print("OK — endpoint reachable, contract correct.")
    print(f"Transcription ({len(text)} chars):")
    print(f"  {text!r}")
    print("\nEyeball the transcription above against the audio content.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
