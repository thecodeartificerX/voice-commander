"""Client for the remote whisper.cpp /inference endpoint.

Mirrors the request contract of the local-transcribe skill: a multipart POST
with the audio under the ``file`` field and form fields
``response_format=verbose_json`` + ``temperature=0.0``. The transcription is
read from the top-level ``text`` key of the JSON response.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Generous read timeout — a long dictation can be minutes of audio.
_TIMEOUT_S = 300.0


class DictationRemoteError(Exception):
    """Raised when the remote endpoint is unreachable or returns a bad response."""


def post_audio(wav_bytes: bytes, endpoint: str, timeout: float = _TIMEOUT_S) -> str:
    """POST a 16 kHz mono WAV to *endpoint*; return the transcribed text.

    Raises :class:`DictationRemoteError` on any network, HTTP, or parse failure.
    """
    try:
        resp = httpx.post(
            endpoint,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"response_format": "verbose_json", "temperature": "0.0"},
            timeout=timeout,
        )
    except Exception as e:
        raise DictationRemoteError(f"endpoint request failed: {e}") from e

    if resp.status_code != 200:
        raise DictationRemoteError(
            f"HTTP {resp.status_code} from {endpoint}: {resp.text[:200]}"
        )

    try:
        data = resp.json()
    except ValueError as e:
        raise DictationRemoteError(f"could not parse JSON response: {e}") from e

    text = data.get("text")
    if not isinstance(text, str):
        raise DictationRemoteError("response missing top-level 'text' field")
    return text.strip()
