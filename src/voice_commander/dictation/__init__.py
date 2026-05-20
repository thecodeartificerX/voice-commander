"""Dictation mode — raw-PCM WebSocket transcription pipeline (ADR 0096).

Sub-modules:

* :mod:`session`        — ``DictationSession`` state machine; owns the
                          WebSocket transport for one dictation.
* :mod:`ws_client`      — async WebSocket client for the ``/ws/transcribe``
                          raw-PCM streaming endpoint.
* :mod:`bridge`         — sync-queue → asyncio-queue pump.
* :mod:`store`          — WAV chunk encoding and single-slot text persistence.
* :mod:`clipboard`      — clipboard snapshot/paste/restore for result delivery.
* :mod:`postprocess`    — prompt building + corrections/commands text passes.
* :mod:`vocab`          — ``Vocabulary``/``Correction``/``Command`` dataclasses
                          and ``VocabStore`` persistence for ``vocab.json``.
"""
