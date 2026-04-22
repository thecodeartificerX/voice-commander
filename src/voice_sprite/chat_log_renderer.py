"""Renders ChatLog entries as fading pyglet labels on a transparent overlay."""

from __future__ import annotations

import time

import pyglet

from .chat_log import ChatLog

_COLOR_OK = (143, 215, 127)  # #8fd77f — success
_COLOR_ERROR = (255, 118, 118)  # #ff7676 — error
_COLOR_MISS = (255, 181, 98)  # #ffb562 — miss


def _color_for_status(status: str) -> tuple[int, int, int]:
    if status == "error":
        return _COLOR_ERROR
    if status == "miss":
        return _COLOR_MISS
    return _COLOR_OK


class ChatLogRenderer:
    """Draws the ChatLog's current entries as pyglet.text.Label objects.

    Label pool is created lazily (on first draw call that needs more slots).
    Labels are reused across frames; text/color/position set each draw.
    """

    def __init__(
        self,
        chat_log: ChatLog,
        hud_width_px: int,
        font_size: int,
        line_gap_px: int,
    ) -> None:
        self._log = chat_log
        self._width = hud_width_px
        self._font_size = font_size
        self._line_gap = line_gap_px
        self._labels: list[pyglet.text.Label] = []

    def _ensure_labels(self, count: int) -> None:
        while len(self._labels) < count:
            self._labels.append(
                pyglet.text.Label(
                    text="",
                    font_name="Consolas",
                    font_size=self._font_size,
                    x=0,
                    y=0,
                    anchor_x="left",
                    anchor_y="bottom",
                    color=(255, 255, 255, 0),
                )
            )

    def draw(self) -> None:
        entries = self._log.entries()  # newest-first
        if not entries:
            return
        self._ensure_labels(len(entries))
        now = time.monotonic()

        # Layout — HUD region sits on the left portion of the window.
        # Newest entry at bottom of HUD region, stacking upward.
        line_h = self._font_size + self._line_gap
        bottom_y = 0  # draw at window bottom
        for i, entry in enumerate(entries):
            label = self._labels[i]
            opacity = self._log.opacity_of(entry, now)
            if opacity <= 0:
                label.text = ""
                continue
            r, g, b = _color_for_status(entry.status)
            label.text = entry.text
            label.x = 0
            label.y = bottom_y + i * line_h
            label.color = (r, g, b, int(255 * opacity))
            label.draw()
        for label in self._labels[len(entries):]:
            label.text = ""
