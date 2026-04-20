import socket
import time

import httpx
import pytest
from fastapi import FastAPI

from voice_commander.web.server import WebServer


@pytest.fixture
def simple_app():
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"pong": True}

    return app


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_start_and_stop(simple_app):
    port = _free_port()
    server = WebServer(simple_app, host="127.0.0.1", port=port)
    server.start()
    try:
        assert server.bound_port == port
        # Give uvicorn a moment to start
        time.sleep(0.5)
        resp = httpx.get(f"http://127.0.0.1:{port}/ping", timeout=2.0)
        assert resp.status_code == 200
        assert resp.json() == {"pong": True}
    finally:
        server.stop(timeout=5.0)


def test_port_bump_fallback(simple_app):
    port = _free_port()
    # Occupy the port
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        server = WebServer(simple_app, host="127.0.0.1", port=port)
        server.start()
        try:
            assert server.bound_port is not None
            assert server.bound_port != port  # Should have bumped
        finally:
            server.stop(timeout=5.0)
    finally:
        blocker.close()


def test_stop_within_timeout(simple_app):
    port = _free_port()
    server = WebServer(simple_app, host="127.0.0.1", port=port)
    server.start()
    time.sleep(0.3)
    start = time.monotonic()
    server.stop(timeout=5.0)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0  # Should stop well within timeout
