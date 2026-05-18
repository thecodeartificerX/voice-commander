"""Client for the remote whisper.cpp /inference endpoint.

Mirrors the request contract of the local-transcribe skill: a multipart POST
with the audio under the ``file`` field and form fields
``response_format=json`` + ``temperature=0.0``. The transcription is read
from the top-level ``text`` key of the JSON response.

``json`` is deliberate over ``verbose_json``: dictation only needs the
``text`` field, and ``verbose_json`` makes whisper.cpp additionally compute
per-segment confidence + token timestamps — measured at ~+1.2s on a 36s clip
on the reference box. Switching to ``json`` drops that work entirely.

When a non-empty *prompt* string is provided, it is sent as the
``prompt`` form field and ``carry_initial_prompt=true`` is added so whisper
re-applies the initial prompt to every decode window, not only the first 30 s.
See ``docs/references/whisper-cpp-server-inference.md`` for the full parameter
reference.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# 30 s total timeout (connect + read).  This is intentionally bounded:
# post_audio runs on _dictation_executor (a FIFO single-worker executor shared
# by all dictation operations).  A hung POST at the old 300 s default would
# block _dictation_executor for up to 5 minutes, stalling all subsequent
# dictation — including the auto-close submitted by _end_owned_session_if_needed
# (ADR 0090 §4).  Long dictations on the reference hardware transcribe in ~1.2 s
# for a 36 s clip; 30 s gives ample headroom without allowing an indefinite stall.
# On timeout httpx raises TimeoutException, which the except-Exception block
# wraps as DictationRemoteError → _finalize_dictation publishes dictation.error
# and sounds a miss chime, leaving the executor worker free for the next call.
_TIMEOUT_S = 30.0


class DictationRemoteError(Exception):
    """Raised when the remote endpoint is unreachable or returns a bad response."""


def post_audio(
    wav_bytes: bytes,
    endpoint: str,
    prompt: str = "",
    timeout: float = _TIMEOUT_S,
) -> str:
    """POST a 16 kHz mono WAV to *endpoint*; return the transcribed text.

    Parameters
    ----------
    wav_bytes:
        16 kHz mono 16-bit PCM WAV bytes.
    endpoint:
        Full URL of the whisper.cpp ``/inference`` endpoint.
    prompt:
        Optional initial-prompt string built by ``postprocess.build_prompt``.
        When non-empty, the ``prompt`` and ``carry_initial_prompt`` form fields
        are added to the POST so the decoder is biased toward user vocabulary
        across *all* decode windows. When empty (default), both fields are
        omitted and behaviour is identical to the pre-vocabulary baseline.
    timeout:
        HTTP read timeout in seconds (default 30 s — bounded to prevent an
        indefinite executor stall; see _TIMEOUT_S comment, ADR 0090 §4).

    Raises
    ------
    DictationRemoteError
        On any network, HTTP, or JSON-parse failure.
    """
    data: dict[str, str] = {"response_format": "json", "temperature": "0.0"}
    if prompt:
        data["prompt"] = prompt
        data["carry_initial_prompt"] = "true"

    try:
        resp = httpx.post(
            endpoint,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data=data,
            timeout=timeout,
        )
    except Exception as e:
        raise DictationRemoteError(f"endpoint request failed: {e}") from e

    if resp.status_code != 200:
        raise DictationRemoteError(
            f"HTTP {resp.status_code} from {endpoint}: {resp.text[:200]}"
        )

    try:
        resp_data = resp.json()
    except ValueError as e:
        raise DictationRemoteError(f"could not parse JSON response: {e}") from e

    text = resp_data.get("text")
    if not isinstance(text, str):
        raise DictationRemoteError("response missing top-level 'text' field")
    # whisper.cpp emits a newline at every segment boundary, so a multi-segment
    # clip arrives with spurious mid-sentence line breaks. Dictation inserts
    # free-form prose at the cursor — collapse every whitespace run (newlines
    # included) to a single space so the paste reads as one continuous block.
    return " ".join(text.split())
