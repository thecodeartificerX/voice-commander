"""Dictation mode — streaming WebSocket transcription pipeline (ADR 0092).

Sub-modules:

* :mod:`session`        — ``DictationSession`` state machine; owns the
                          WebSocket transport for one dictation.
* :mod:`ws_client`      — async WebSocket client for the ``/ws/transcribe``
                          streaming endpoint.
* :mod:`local_agreement` — ``LocalAgreement`` word stabiliser (commits words
                          confirmed stable across chunk boundaries).
* :mod:`bridge`         — sync-queue → asyncio-queue pump.
* :mod:`store`          — WAV chunk encoding and single-slot text persistence.
* :mod:`clipboard`      — clipboard snapshot/paste/restore for result delivery.
* :mod:`postprocess`    — prompt building + corrections/commands text passes.
* :mod:`vocab`          — ``Vocabulary``/``Correction``/``Command`` dataclasses
                          and ``VocabStore`` persistence for ``vocab.json``.
"""
