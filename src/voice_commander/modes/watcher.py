"""Hot-reload watcher for ``modes/*.toml`` files.

Mirrors :class:`voice_commander.config_watcher.ConfigWatcher` — same debounce
mechanism (a :class:`threading.Timer` reset on every FS event), same
:class:`watchdog.observers.Observer` setup, same ``start``/``stop`` semantics.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    PatternMatchingEventHandler,
)
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _Handler(PatternMatchingEventHandler):
    """Forward created/modified/deleted/moved events on ``*.toml`` files to a
    debounced callback.

    Mirrors ``_ChangeHandler`` in ``config_watcher.py`` — the timer is cancelled
    and reset on every event so only one callback fires per quiet window.
    """

    def __init__(self, on_change: Callable[[Path], None], debounce_ms: int) -> None:
        super().__init__(patterns=["*.toml"], ignore_directories=True, case_sensitive=False)
        self._on_change = on_change
        self._debounce_s = debounce_ms / 1000.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule(self, path: Path) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._fire, args=(path,))
            self._timer.daemon = True
            self._timer.start()

    def _fire(self, path: Path) -> None:
        try:
            self._on_change(path)
        except Exception:
            logger.exception("modes_watcher: on_change callback raised for %s", path)

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        self._schedule(Path(str(event.src_path)))

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        self._schedule(Path(str(event.src_path)))

    def on_deleted(self, event: FileDeletedEvent) -> None:  # type: ignore[override]
        self._schedule(Path(str(event.src_path)))

    def on_moved(self, event: FileMovedEvent) -> None:  # type: ignore[override]
        # Atomic-replace editors write a temp file then rename it over the
        # target — check dest_path, exactly like ConfigWatcher.
        self._schedule(Path(str(event.dest_path)))


class ModesWatcher:
    """Watches a modes directory for ``*.toml`` changes and fires *on_change*.

    Debounces rapid-fire FS events to a single callback per ``debounce_ms``
    quiet window.  Uses :class:`watchdog.observers.Observer` +
    :class:`watchdog.events.PatternMatchingEventHandler`.

    The callback runs on the watchdog observer thread; the consumer is
    responsible for any cross-thread dispatch needed.

    ``start()`` is a no-op when the directory does not exist or the watcher
    has already been started.  ``stop()`` is a no-op when never started.

    Args:
        modes_dir:   Directory containing ``*.toml`` mode files to watch.
        on_change:   Called with the changed :class:`~pathlib.Path` after a
                     quiet window following any ``*.toml`` modification,
                     creation, deletion, or rename.
        debounce_ms: Milliseconds of quiet time before the callback fires.
                     Defaults to 500; tests use 50 for speed.
    """

    def __init__(
        self,
        modes_dir: Path,
        on_change: Callable[[Path], None],
        debounce_ms: int = 500,
    ) -> None:
        self._dir = Path(modes_dir)
        self._handler = _Handler(on_change, debounce_ms)
        self._observer: Any = None
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the file-system observer.  Idempotent."""
        with self._lock:
            if self._started:
                return
            if not self._dir.is_dir():
                logger.warning(
                    "modes_watcher: directory %s does not exist — watcher not started",
                    self._dir,
                )
                return
            observer = Observer()
            observer.schedule(self._handler, str(self._dir), recursive=False)
            observer.start()
            self._observer = observer
            self._started = True
            logger.info("modes hot-reload: watching %s", self._dir)

    def stop(self) -> None:
        """Stop the observer.  Idempotent.  Blocks up to 2 s for observer.join()."""
        with self._lock:
            if not self._started:
                return
            self._started = False

        # Cancel any pending debounce timer (mirror ConfigWatcher.stop()).
        with self._handler._lock:
            if self._handler._timer is not None:
                self._handler._timer.cancel()
                self._handler._timer = None

        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                logger.exception("modes_watcher: error stopping observer")
            self._observer = None
        logger.info("modes hot-reload: stopped watching %s", self._dir)
