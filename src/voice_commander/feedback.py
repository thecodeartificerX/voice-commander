from __future__ import annotations

import logging
import winsound
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from windows_toasts import InteractableWindowsToaster, Toast

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
        toast_enabled: bool = True,
        toast_show_transcript: bool = True,
    ) -> None:
        self._start = sounds_dir / start_sound
        self._stop = sounds_dir / stop_sound
        self._miss = sounds_dir / miss_sound
        self._toast_enabled = toast_enabled
        self._toast_show_transcript = toast_show_transcript
        self._toaster = InteractableWindowsToaster("Voice Commander") if toast_enabled else None

    def _play(self, path: Path) -> None:
        if not path.exists():
            logger.warning("Sound file missing: %s", path)
            return
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            logger.exception("winsound.PlaySound failed for %s", path)

    def on_recording_start(self) -> None:
        self._play(self._start)

    def on_recording_stop(self) -> None:
        self._play(self._stop)

    def on_transcript(self, text: str, confidence: float) -> None:
        logger.info("transcript (conf=%.2f): %s", confidence, text)

    def on_match(self, tool: str, phrase: str, score: float) -> None:
        logger.info("MATCH %s \u2190 '%s' (%.0f)", tool, phrase, score)
        self._toast(f"\u2713 {tool}", f"'{phrase}' ({score:.0f})")

    def on_miss(self, transcript: str, candidates: Sequence[tuple[str, str, float]]) -> None:
        self._play(self._miss)
        top = candidates[0] if candidates else None
        logger.info("MISS '%s' top=%s", transcript, list(candidates)[:3])
        if top is not None:
            self._toast("\u2717 no match", f"'{transcript}' \u2192 top: {top[0]} ({top[2]:.0f})")
        else:
            self._toast("\u2717 no match", f"'{transcript}'")

    def _toast(self, title: str, body: str) -> None:
        if not self._toast_enabled or self._toaster is None:
            return
        try:
            t = Toast()
            t.text_fields = [title, body]
            self._toaster.show_toast(t)
        except Exception:
            logger.exception("toast dispatch failed")

    def on_error(self, subsystem: str, err: BaseException) -> None:
        logger.exception("Error in %s: %s", subsystem, err)
