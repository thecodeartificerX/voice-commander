"""Visual end-to-end harness for dictation custom vocabulary (ADR 0088).

Checkpoints
-----------
CP 1  stub HTTP server reachable; local test helper returns the canned text
CP 2  VocabStore.save + load round-trips the known vocabulary
CP 3  build_prompt output reaches the stub's prompt field, contains the vocab word
CP 4  apply_corrections turns "supa base" into "Supabase"
CP 5  apply_commands turns "next line" into a newline char
CP 6  DictationStore.save_text / read_text round-trips the processed text
CP 7  in-memory retranscribe simulation applies the same corrections + commands

Usage:
  python scripts/dictation_vocab_e2e.py
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / "outputs" / "dictation_vocab_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = OUT_DIR / "dictation_vocab_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_vocab_e2e")

# ---------------------------------------------------------------------------
# Checkpoint registry
# ---------------------------------------------------------------------------

_checkpoints: list[tuple[int, str, bool | None, bool]] = []


def _record(
    n: int,
    label: str,
    result: bool | None,
    *,
    skip_msg: str = "",
    blocking: bool = True,
) -> None:
    _checkpoints.append((n, label, result, blocking))
    if result is None:
        log.info("CHECKPOINT %d: SKIPPED — %s", n, skip_msg)
    elif result:
        log.info("CHECKPOINT %d: PASS — %s", n, label)
    else:
        log.error("CHECKPOINT %d: FAIL — %s", n, label)


# ---------------------------------------------------------------------------
# Stub whisper.cpp /inference server
# ---------------------------------------------------------------------------

# Text the stub returns — contains a known mistranscription and command phrase
_STUB_RAW_TEXT = "I use supa base next line world"


class _WhisperStubHandler(BaseHTTPRequestHandler):
    """Returns a canned JSON response imitating the whisper.cpp /inference endpoint."""

    def log_message(self, *_args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        # Read and discard the request body (multipart form-data)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        # Record the raw prompt field for CP 3
        prompt_value = ""
        content_type = self.headers.get("Content-Type", "")
        if "boundary=" in content_type:
            boundary = content_type.split("boundary=")[-1].strip()
            try:
                decoded = body.decode("utf-8", errors="replace")
                for part in decoded.split("--" + boundary):
                    if 'name="prompt"' in part:
                        # Value is after the blank line
                        value_part = part.split("\r\n\r\n", 1)
                        if len(value_part) > 1:
                            # rstrip trims trailing CRLF + boundary dashes; fine for this test-only stub.
                            prompt_value = value_part[1].rstrip("\r\n--")
                            break
            except Exception:
                pass

        self.server.last_prompt = prompt_value  # type: ignore[attr-defined]
        log.info("stub: received POST, prompt=%r", prompt_value)

        response = json.dumps({"text": _STUB_RAW_TEXT}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def _start_stub_server() -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _WhisperStubHandler)
    port = srv.server_address[1]
    srv.last_prompt = ""  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="stub-whisper")
    t.start()
    return srv, port


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------

_KNOWN_VOCAB_WORD = "Supabase"
_KNOWN_CORRECTION_WRONG = "supa base"
_KNOWN_CORRECTION_RIGHT = "Supabase"
_KNOWN_COMMAND_PHRASE = "next line"
_KNOWN_COMMAND_ACTION = "newline"


def run() -> int:  # noqa: C901, PLR0912, PLR0915
    """Run all checkpoints. Returns 0 if all hard checkpoints pass."""
    _checkpoints.clear()
    from voice_commander.dictation.postprocess import (
        apply_commands,
        apply_corrections,
        build_prompt,
    )
    from voice_commander.dictation.store import DictationStore, encode_wav
    from voice_commander.dictation.vocab import (
        Command,
        Correction,
        Vocabulary,
        VocabStore,
    )

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------
    stub_srv, stub_port = _start_stub_server()
    stub_endpoint = f"http://127.0.0.1:{stub_port}/inference"
    log.info("stub whisper server on port %d", stub_port)

    dictation_dir = OUT_DIR / "dictation"
    dictation_dir.mkdir(parents=True, exist_ok=True)
    vocab_path = dictation_dir / "vocab.json"
    dictation_store = DictationStore(dictation_dir)
    vocab_store = VocabStore(vocab_path)

    # Shared test audio + WAV — created here so every checkpoint can rely on it.
    # If encode_wav fails the whole harness can't run; let it raise visibly.
    test_audio = np.zeros(16000, dtype=np.float32)
    test_wav = encode_wav(test_audio)

    # Safe defaults so a failing early checkpoint can't trigger UnboundLocalError
    # cascades in CP 4/5/7 (which would misattribute the real failure).
    vocab = Vocabulary()
    prompt = ""

    # ---------------------------------------------------------------------------
    # Local helper: send a WAV to the stub whisper.cpp /inference endpoint.
    # The batch HTTP POST client (remote.py) was removed by ADR 0092.
    # Uses urllib (stdlib) to avoid extra deps.
    # ---------------------------------------------------------------------------
    import email.generator
    import email.mime.multipart
    import io
    import urllib.request

    def _post_audio_stub(wav_bytes: bytes, endpoint: str, *, prompt: str = "") -> str:
        """POST *wav_bytes* to the stub /inference server; return the transcribed text."""
        boundary = "boundary_vocab_e2e_test"
        parts: list[bytes] = []
        # file part
        parts.append(
            f"--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n"
            "Content-Type: audio/wav\r\n\r\n".encode()
            + wav_bytes
            + b"\r\n"
        )
        if prompt:
            parts.append(
                f"--{boundary}\r\n"
                "Content-Disposition: form-data; name=\"prompt\"\r\n\r\n".encode()
                + prompt.encode()
                + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        import json as _json
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
        return str(data.get("text", "")).strip()

    try:
        # --------------------------------------------------------------
        # CP 1: stub server is reachable
        # --------------------------------------------------------------
        result = ""
        try:
            result = _post_audio_stub(test_wav, stub_endpoint)
            cp1 = bool(result)
            log.info("stub returned: %r", result)
        except Exception as exc:
            log.error("stub server unreachable: %s", exc)
            cp1 = False
        _record(1, f"stub whisper server reachable, returned: {result!r}", cp1)

        # --------------------------------------------------------------
        # CP 2: VocabStore.save + load round-trips the known vocabulary
        # --------------------------------------------------------------
        known_vocab = Vocabulary(
            vocab=(_KNOWN_VOCAB_WORD,),
            corrections=(Correction(wrong=_KNOWN_CORRECTION_WRONG, right=_KNOWN_CORRECTION_RIGHT),),
            commands=(Command(phrase=_KNOWN_COMMAND_PHRASE, action=_KNOWN_COMMAND_ACTION),),
        )
        try:
            vocab_store.save(known_vocab)
            loaded_back = vocab_store.load()
            cp2 = loaded_back == known_vocab
            log.info("vocab.json round-trip: %s", "PASS" if cp2 else "FAIL")
        except Exception as exc:
            log.error("vocab save/load failed: %s", exc)
            cp2 = False
        _record(2, "VocabStore.save + load round-trips the known vocabulary", cp2)

        # --------------------------------------------------------------
        # CP 3: prompt sent to stub contains vocab word
        # --------------------------------------------------------------
        stub_srv.last_prompt = ""  # type: ignore[attr-defined]
        try:
            vocab = vocab_store.load()
            prompt = build_prompt(vocab)
            log.info("built prompt: %r", prompt)
            # Re-send with prompt to capture server-side
            _post_audio_stub(test_wav, stub_endpoint, prompt=prompt)
            cp3 = _KNOWN_VOCAB_WORD in stub_srv.last_prompt  # type: ignore[attr-defined]
            log.info(
                "stub last_prompt: %r — contains %r: %s",
                stub_srv.last_prompt,  # type: ignore[attr-defined]
                _KNOWN_VOCAB_WORD,
                cp3,
            )
        except Exception as exc:
            log.error("prompt test failed: %s", exc)
            cp3 = False
        _record(3, f"prompt contains vocab word {_KNOWN_VOCAB_WORD!r}", cp3)

        # --------------------------------------------------------------
        # CP 4: correction applied: "supa base" → "Supabase"
        # --------------------------------------------------------------
        corrected = ""
        try:
            raw_text = _STUB_RAW_TEXT  # "I use supa base next line world"
            corrected = apply_corrections(raw_text, vocab.corrections)
            cp4 = _KNOWN_CORRECTION_RIGHT in corrected and _KNOWN_CORRECTION_WRONG not in corrected
            log.info(
                "after corrections: %r (expected %r absent, %r present) → %s",
                corrected,
                _KNOWN_CORRECTION_WRONG,
                _KNOWN_CORRECTION_RIGHT,
                "PASS" if cp4 else "FAIL",
            )
        except Exception as exc:
            log.error("apply_corrections failed: %s", exc)
            cp4 = False
        _record(
            4, f"correction {_KNOWN_CORRECTION_WRONG!r} → {_KNOWN_CORRECTION_RIGHT!r} applied", cp4
        )

        # --------------------------------------------------------------
        # CP 5: command applied: "next line" → newline
        # --------------------------------------------------------------
        after_commands = ""
        try:
            after_commands = apply_commands(corrected, vocab.commands)
            cp5 = "\n" in after_commands and _KNOWN_COMMAND_PHRASE not in after_commands.lower()
            log.info(
                "after commands: %r — contains newline: %s, phrase absent: %s → %s",
                after_commands,
                "\n" in after_commands,
                _KNOWN_COMMAND_PHRASE not in after_commands.lower(),
                "PASS" if cp5 else "FAIL",
            )
        except Exception as exc:
            log.error("apply_commands failed: %s", exc)
            cp5 = False
        _record(5, "command 'next line' → newline applied", cp5)

        # --------------------------------------------------------------
        # CP 6: last.txt on disk matches final pasted text
        # --------------------------------------------------------------
        try:
            dictation_store.save_text(after_commands)
            on_disk = dictation_store.read_text()
            cp6 = on_disk == after_commands
            log.info("last.txt matches: %s", "PASS" if cp6 else "FAIL")
        except Exception as exc:
            log.error("last.txt save/read failed: %s", exc)
            cp6 = False
        _record(6, "last.txt on disk matches processed text", cp6)

        # --------------------------------------------------------------
        # CP 7: re-transcribe applies same post-processing
        # --------------------------------------------------------------
        try:
            # Simulate retranscribe: post WAV bytes with prompt, apply corrections+commands.
            # (ADR 0092: DictationStore no longer persists audio; we use the in-memory
            # test_wav bytes directly.)
            retranscribe_raw = _post_audio_stub(test_wav, stub_endpoint, prompt=prompt)
            retranscribe_corrected = apply_corrections(retranscribe_raw, vocab.corrections)
            retranscribe_final = apply_commands(retranscribe_corrected, vocab.commands)
            cp7 = (
                _KNOWN_CORRECTION_RIGHT in retranscribe_final
                and "\n" in retranscribe_final
                and _KNOWN_CORRECTION_WRONG not in retranscribe_final
            )
            log.info("retranscribe result: %r → %s", retranscribe_final, "PASS" if cp7 else "FAIL")
        except Exception as exc:
            log.error("retranscribe simulation failed: %s", exc)
            cp7 = False
        _record(7, "re-transcribe path applies corrections + commands identically", cp7)
    finally:
        # ------------------------------------------------------------------
        # Cleanup — always shut the stub down, even on an unexpected failure.
        # ------------------------------------------------------------------
        stub_srv.shutdown()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("DICTATION VOCAB E2E — CHECKPOINT SUMMARY")
    print("=" * 60)
    hard_fail = 0
    for n, label, result, blocking in _checkpoints:
        if result is None:
            status = "SKIPPED"
        elif result:
            status = "PASS"
        else:
            status = "FAIL"
            if blocking:
                hard_fail += 1
        nb_tag = " [non-blocking]" if not blocking else ""
        print(f"  CP {n:>2}: {status:<8}  {label}{nb_tag}")
    print("=" * 60)
    print(f"Evidence dir: {OUT_DIR}")
    print(f"Log:          {LOG_PATH}")
    print("=" * 60)
    if hard_fail == 0:
        print("RESULT: ALL HARD CHECKPOINTS PASSED")
    else:
        print(f"RESULT: {hard_fail} HARD CHECKPOINT(S) FAILED")
    print("=" * 60 + "\n")
    return 0 if hard_fail == 0 else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
