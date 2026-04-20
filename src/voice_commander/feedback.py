from __future__ import annotations

import logging
import winsound
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class FeedbackSink(Protocol):
    def on_recording_start(self) -> None: ...
    def on_recording_stop(self) -> None: ...
    def on_transcript(self, text: str, confidence: float) -> None: ...
    def on_match(self, tool: str, phrase: str, score: float) -> None: ...
    def on_miss(
        self, transcript: str, candidates: Sequence[tuple[str, str, float]]
    ) -> None: ...
    def on_error(self, subsystem: str, err: BaseException) -> None: ...


class NullFeedbackSink:
    def on_recording_start(self) -> None: pass
    def on_recording_stop(self) -> None: pass
    def on_transcript(self, text: str, confidence: float) -> None: pass
    def on_match(self, tool: str, phrase: str, score: float) -> None: pass
    def on_miss(
        self, transcript: str, candidates: Sequence[tuple[str, str, float]]
    ) -> None: pass
    def on_error(self, subsystem: str, err: BaseException) -> None: pass


class CapturingFeedbackSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def on_recording_start(self) -> None:
        self.calls.append(("on_recording_start", ()))

    def on_recording_stop(self) -> None:
        self.calls.append(("on_recording_stop", ()))

    def on_transcript(self, text: str, confidence: float) -> None:
        self.calls.append(("on_transcript", (text, confidence)))

    def on_match(self, tool: str, phrase: str, score: float) -> None:
        self.calls.append(("on_match", (tool, phrase, score)))

    def on_miss(
        self, transcript: str, candidates: Sequence[tuple[str, str, float]]
    ) -> None:
        self.calls.append(("on_miss", (transcript, tuple(candidates))))

    def on_error(self, subsystem: str, err: BaseException) -> None:
        self.calls.append(("on_error", (subsystem, err)))


class WindowsFeedbackSink:
    def __init__(
        self,
        sounds_dir: Path,
        start_sound: str = "start.wav",
        stop_sound: str = "stop.wav",
        miss_sound: str = "miss.wav",
    ) -> None:
        self._start = sounds_dir / start_sound
        self._stop = sounds_dir / stop_sound
        self._miss = sounds_dir / miss_sound

    def _play(self, path: Path) -> None:
        if not path.exists():
            logger.warning("Sound file missing: %s", path)
            return
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            logger.exception("winsound.PlaySound failed for %s", path)

    def on_recording_start(self) -> None:
        pass

    def on_recording_stop(self) -> None:
        pass

    def on_transcript(self, text: str, confidence: float) -> None:
        logger.info("transcript (conf=%.2f): %s", confidence, text)

    def on_match(self, tool: str, phrase: str, score: float) -> None:
        logger.info("MATCH %s <- '%s' (%.0f)", tool, phrase, score)

    def on_miss(self, transcript: str, candidates: Sequence[tuple[str, str, float]]) -> None:
        self._play(self._miss)
        logger.info("MISS '%s' top=%s", transcript, list(candidates)[:3])

    def on_error(self, subsystem: str, err: BaseException) -> None:
        logger.error("Error in %s: %s", subsystem, err, exc_info=err)
        self._play(self._miss)
