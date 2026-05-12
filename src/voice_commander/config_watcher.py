from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    PatternMatchingEventHandler,
)
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _ChangeHandler(PatternMatchingEventHandler):
    """Forward modified/created/moved events to a debounced callback."""

    def __init__(
        self,
        path: Path,
        on_change: Callable[[Path], None],
        debounce_ms: int,
    ) -> None:
        # Watch only the exact filename; ignore_directories=True skips dir events.
        super().__init__(
            patterns=[path.name],
            ignore_directories=True,
            case_sensitive=False,
        )
        self._path = path
        self._on_change = on_change
        self._debounce_s = debounce_ms / 1000.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    # watchdog delivers events for any file matching the pattern, not just our
    # target.  Guard against events for other files with the same name in
    # nested directories.
    def _matches_target(self, src_path: str) -> bool:
        try:
            return Path(src_path).resolve() == self._path.resolve()
        except Exception:
            # If we can't resolve, fall back to name-only check.
            return Path(src_path).name == self._path.name

    def _schedule_callback(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        try:
            self._on_change(self._path)
        except Exception:
            logger.exception(
                "config_watcher: on_change callback raised for %s", self._path
            )

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if self._matches_target(event.src_path):
            self._schedule_callback()

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if self._matches_target(event.src_path):
            self._schedule_callback()

    def on_moved(self, event: FileMovedEvent) -> None:  # type: ignore[override]
        # Atomic-replace editors (vim, many Windows editors) write to a temp
        # file then rename/move it over the target — so we check dest_path.
        if self._matches_target(event.dest_path):
            self._schedule_callback()


class ConfigWatcher:
    """Watches a config.toml file and invokes a callback on change.

    Debounces rapid-fire FS events to a single callback per ``debounce_ms``
    quiet window.  Uses :class:`watchdog.observers.Observer` +
    :class:`watchdog.events.PatternMatchingEventHandler`.

    The callback runs on the watchdog observer thread; the consumer is
    responsible for any cross-thread dispatch needed.

    Args:
        path:        Absolute path to the config file to watch.
        on_change:   Called with *path* after a quiet window following any
                     modification, creation, or rename onto the file.
        debounce_ms: Milliseconds of quiet time before the callback fires.
                     Defaults to 500; tests use 50 for speed.
    """

    def __init__(
        self,
        path: Path,
        on_change: Callable[[Path], None],
        debounce_ms: int = 500,
    ) -> None:
        self._path = path
        self._handler = _ChangeHandler(path, on_change, debounce_ms)
        self._observer: Observer | None = None
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the file-system observer.  Idempotent."""
        with self._lock:
            if self._started:
                return
            observer = Observer()
            observer.schedule(
                self._handler,
                str(self._path.parent),
                recursive=False,
            )
            observer.start()
            self._observer = observer
            self._started = True
            logger.info("config_watcher: watching %s", self._path)

    def stop(self) -> None:
        """Stop the observer.  Idempotent.  Blocks up to 2 s for observer.join()."""
        with self._lock:
            if not self._started:
                return
            self._started = False

        # Cancel any pending debounce timer.
        with self._handler._lock:
            if self._handler._timer is not None:
                self._handler._timer.cancel()
                self._handler._timer = None

        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                logger.exception("config_watcher: error stopping observer")
            self._observer = None
        logger.info("config_watcher: stopped watching %s", self._path)
