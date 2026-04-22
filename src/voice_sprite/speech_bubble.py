from __future__ import annotations


class SpeechBubble:
    """Fading last-command label displayed near the sprite."""

    def __init__(self, fade_ms: int = 2000) -> None:
        self._fade_s = fade_ms / 1000.0
        self._text: str = ""
        self._timer: float = 0.0
        self._visible = False

    def show(self, text: str) -> None:
        """Show text and restart the fade timer."""
        self._text = text
        self._timer = self._fade_s
        self._visible = True

    def tick(self, dt: float) -> None:
        """Advance the fade timer by dt seconds."""
        if not self._visible:
            return
        self._timer -= dt
        if self._timer <= 0:
            self._visible = False
            self._text = ""

    @property
    def text(self) -> str:
        return self._text if self._visible else ""

    @property
    def opacity(self) -> float:
        if not self._visible or self._fade_s == 0:
            return 0.0
        return max(0.0, min(1.0, self._timer / self._fade_s))

    @property
    def visible(self) -> bool:
        return self._visible
