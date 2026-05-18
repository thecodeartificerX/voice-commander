"""End-to-end harness for streaming dictation.

Drives the real pipeline: real microphone, real WebSocket server, real
clipboard paste. The operator reads a scripted sentence aloud; the harness
prints the transcript the pipeline produced and the operator confirms it
appeared at the cursor and is correct.

Why there is no automated clipboard assertion: ``paste_via_clipboard``
snapshots and *restores* the original clipboard after the Ctrl+V, so by the
time this script could read the clipboard the transcript is no longer there.
The authoritative pipeline output is the string returned by
``StreamSession.stop()``; whether it physically landed at the cursor is
confirmed visually by the operator.

Usage:
    python scripts/dictation_stream_e2e.py

Protocol: docs/agents/visual-e2e-testing.md
"""

from __future__ import annotations

import sys
from pathlib import Path

from voice_commander.dictation_stream.config import load as load_config
from voice_commander.dictation_stream.session import StreamSession

SCRIPT = (
    "The quick brown fox jumps over the lazy dog while the streaming "
    "dictation pipeline transcribes every word."
)


def _ask_yes_no(question: str) -> bool:
    return input(f"{question} [y/N]: ").strip().lower() in {"y", "yes"}


def main() -> int:
    config = load_config(Path("config.toml"))
    print(f"Endpoint: {config.ws_url}")
    print("=" * 70)
    print("Setup: click into an empty text editor (Notepad, VS Code, ...) so")
    print("the transcript has a visible paste target.")
    print()
    print("Read this sentence aloud, clearly, at a normal pace:")
    print()
    print(f"  {SCRIPT}")
    print()
    input("Press Enter, click your text editor, then immediately start speaking...")

    session = StreamSession(config)
    session.start()
    print("Recording — speak now. Press Enter when finished.")
    try:
        input()
        pasted = session.stop()
    except KeyboardInterrupt:
        session.stop()
        print("\nInterrupted.")
        return 1

    print("=" * 70)
    print(f"Pipeline transcript: {pasted!r}")
    print()

    if not pasted.strip():
        print(
            "FAIL — pipeline returned an empty transcript (mic, VAD, "
            "WebSocket, or stabiliser produced nothing)."
        )
        return 1

    landed = _ask_yes_no("Did the transcript appear at your cursor?")
    correct = _ask_yes_no("Does the transcript match what you said?")
    if landed and correct:
        print("PASS — transcript produced, pasted at the cursor, and correct.")
        return 0
    if not landed:
        print("FAIL — transcript was produced but did not reach the cursor.")
    else:
        print("FAIL — transcript reached the cursor but does not match speech.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
