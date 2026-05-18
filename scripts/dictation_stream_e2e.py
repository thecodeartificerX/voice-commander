"""End-to-end harness for streaming dictation.

Drives the real pipeline: real microphone, real WebSocket server, real
clipboard. The operator speaks a scripted paragraph; the harness captures the
clipboard contents and prints a PASS/FAIL verdict.

Usage:
    python scripts/dictation_stream_e2e.py

Protocol: docs/agents/visual-e2e-testing.md
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from voice_commander.dictation.clipboard import read_clipboard_text
from voice_commander.dictation_stream.config import load as load_config
from voice_commander.dictation_stream.session import StreamSession

SCRIPT = (
    "The quick brown fox jumps over the lazy dog while the streaming "
    "dictation pipeline transcribes every word."
)


def main() -> int:
    config = load_config(Path("config.toml"))
    print(f"Endpoint: {config.ws_url}")
    print("=" * 70)
    print("Read this sentence aloud, clearly, at a normal pace:")
    print()
    print(f"  {SCRIPT}")
    print()
    input("Press Enter, then immediately start speaking...")

    session = StreamSession(config)
    session.start()
    print("Recording — speak now. Press Enter when finished.")
    input()
    pasted = session.stop()

    time.sleep(0.3)  # let the clipboard paste settle
    clipboard = read_clipboard_text() or ""

    print("=" * 70)
    print(f"Pipeline returned : {pasted!r}")
    print(f"Clipboard contents: {clipboard!r}")
    print()

    ok = bool(pasted.strip()) and pasted.strip() in clipboard
    if ok:
        print("PASS — transcript was produced and landed on the clipboard.")
        print("Human check: does the transcript match what you said?")
        return 0
    print("FAIL — no transcript, or it did not reach the clipboard.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
