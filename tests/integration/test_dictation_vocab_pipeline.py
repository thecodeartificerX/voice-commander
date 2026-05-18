"""Integration test: streaming _finalize_dictation with a populated vocab.json.

vocab.json is loaded at DictationSession.start (the daemon's _load_vocab
hot-reload); the snapshot drives both the WS config prompt and the
corrections/commands applied in _finalize_dictation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules

from ._dictation_ws import MockWsServer

if TYPE_CHECKING:
    from voice_commander.daemon import StreamingDaemon


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray) -> _Transcription:
        return self.queue.pop(0)


def _make_daemon(
    transcripts: list[_Transcription],
    tmp_path: Path,
    ws_url: str,
) -> tuple["StreamingDaemon", DictationSession, CapturingFeedbackSink, EventBus]:
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()
    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(
        bus=bus, ws_url=ws_url, end_word="done", idle_timeout_s=3.0
    )
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        dictation_ws_url=ws_url,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


def test_vocab_corrections_and_commands_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A populated vocab.json shapes the streamed transcript before paste."""
    # Write vocab.json BEFORE the daemon starts dictation.
    vocab_dir = tmp_path / "dictation"
    vocab_dir.mkdir(parents=True)
    (vocab_dir / "vocab.json").write_text(
        json.dumps(
            {
                "vocab": ["Supabase"],
                "corrections": [{"wrong": "supa base", "right": "Supabase"}],
                "commands": [{"phrase": "new line", "action": "newline"}],
            }
        ),
        encoding="utf-8",
    )

    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    # Partials → LocalAgreement → raw "supa base new line then code".
    with MockWsServer(
        ["supa base", "base new line", "new line then code"]
    ) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("dictate"),
                _Transcription("supa base"),
                _Transcription("base new line"),
                _Transcription("new line then code"),
                _Transcription("done"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        audio = np.zeros(16000, dtype=np.float32)
        for _ in range(5):
            daemon._process_utterance(audio)
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    # corrections: "supa base" → "Supabase"; commands: "new line" → "\n".
    assert pasted == ["Supabase\nthen code"], f"unexpected paste: {pasted}"
