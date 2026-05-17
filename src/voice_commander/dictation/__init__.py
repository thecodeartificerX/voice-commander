"""Dictation mode — remote transcription pipeline (ADR 0086).

Sub-packages:

* :mod:`session`   — ``DictationSession`` state machine (buffer, end-word and
                     hotkey-end exit paths).
* :mod:`remote`    — HTTP client for the whisper.cpp ``/inference`` endpoint.
* :mod:`store`     — WAV encoding and single-slot on-disk persistence.
* :mod:`clipboard` — Clipboard snapshot/paste/restore helper for result delivery.
* :mod:`vocab`     — ``Vocabulary``/``Correction``/``Command`` dataclasses and
                     ``VocabStore`` persistence for ``vocab.json``.
"""
