from __future__ import annotations

import logging
import socket
import threading

import uvicorn
from fastapi import FastAPI

logger = logging.getLogger(__name__)


class WebServer:
    """Runs a FastAPI app on a background daemon thread via uvicorn."""

    def __init__(
        self,
        app: FastAPI,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.bound_port: int | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Spawn uvicorn on a daemon thread. Returns True on success.

        Tries ``port``, then ``port+1`` … ``port+10`` on ``EADDRINUSE``.
        Sets ``self.bound_port`` to the actual port used.
        """
        for offset in range(11):
            port = self._port + offset
            if self._try_port(port):
                self.bound_port = port
                config = uvicorn.Config(
                    self._app,
                    host=self._host,
                    port=port,
                    log_level="warning",
                )
                self._server = uvicorn.Server(config)
                self._thread = threading.Thread(
                    target=self._server.run,
                    daemon=True,
                    name="vc-webui",
                )
                self._thread.start()
                logger.info("Web UI started on http://%s:%d", self._host, port)
                return True

        logger.error(
            "Could not bind web UI to any port in range %d-%d",
            self._port,
            self._port + 10,
        )
        return False

    def stop(self, timeout: float = 5.0) -> None:
        """Signal uvicorn to shut down and wait for the thread to exit."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("WebServer thread did not exit within %.1fs", timeout)
            self._thread = None
        self._server = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_port(port: int) -> bool:
        """Return True if ``port`` is available on 127.0.0.1."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
