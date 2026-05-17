"""Integration tests: _finalize_dictation with populated VocabStore.

Mirrors tests/integration/test_dictation_pipeline.py scaffolding.
Only the network (post_audio), OS clipboard (paste_via_clipboard), and
VocabStore.load are touched. Everything else is real production code.
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
) -> tuple[StreamingDaemon, DictationSession, CapturingFeedbackSink, EventBus]:
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
    dictation_session = DictationSession(bus=bus, end_word="done")

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


def test_finalize_dictation_passes_built_prompt_to_post_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_finalize_dictation must call post_audio with the prompt built from vocab.json."""
    vocab_path = tmp_path / "dictation" / "vocab.json"
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    vocab_path.write_text(
        json.dumps({"vocab": ["Supabase", "n8n"], "corrections": [], "commands": []}),
        encoding="utf-8",
    )

    posted_prompts: list[str] = []

    def _fake_post(wav_bytes: bytes, endpoint: str, prompt: str = "", **kw: Any) -> str:
        posted_prompts.append(prompt)
        return "raw transcription text"

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _fake_post)
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, _, _ = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("hello"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert len(posted_prompts) == 1
    assert "Supabase" in posted_prompts[0]
    assert "n8n" in posted_prompts[0]


def test_finalize_dictation_applies_corrections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Corrections in vocab.json must be applied to the remote transcription before paste."""
    vocab_path = tmp_path / "dictation" / "vocab.json"
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    vocab_path.write_text(
        json.dumps(
            {
                "vocab": [],
                "corrections": [{"wrong": "supa base", "right": "Supabase"}],
                "commands": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, prompt="", **kw: "I use supa base daily",
    )
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, _, _, _ = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("I use supa base daily"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert pasted == ["I use Supabase daily"]


def test_finalize_dictation_applies_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Commands in vocab.json must replace spoken phrases with control chars."""
    vocab_path = tmp_path / "dictation" / "vocab.json"
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    vocab_path.write_text(
        json.dumps(
            {
                "vocab": [],
                "corrections": [],
                "commands": [{"phrase": "next line", "action": "newline"}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, prompt="", **kw: "hello next line world",
    )
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, _, _, _ = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("hello next line world"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert pasted == ["hello\nworld"]


def test_finalize_dictation_no_vocab_file_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When vocab.json is absent, post_audio is called with prompt='' and text is pasted unchanged."""
    posted_prompts: list[str] = []

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, prompt="", **kw: (
            posted_prompts.append(prompt), "plain text"
        )[1],
    )
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, _, _, _ = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("plain text"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert pasted == ["plain text"]
    assert posted_prompts == [""]
