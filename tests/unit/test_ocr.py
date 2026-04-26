"""Unit tests for OCR primitive (mocked engines)."""

from __future__ import annotations

import asyncio
import builtins
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_ocr_region_tesseract_path(tmp_path, monkeypatch):
    """When winrt is unavailable and tesseract is on PATH, tesseract branch runs."""

    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    def _fake_select_engine():
        return "tesseract"

    def _fake_capture(x, y, w, h):
        return fake_img

    def _fake_run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stdout = "hello world"
        return r

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", _fake_select_engine)
    monkeypatch.setattr("voice_commander.tools.ocr._capture_region", _fake_capture)
    monkeypatch.setattr("subprocess.run", _fake_run)

    from voice_commander.tools.ocr import ocr_region

    result = ocr_region(0, 0, 100, 100)
    assert result == "hello world"


def test_ocr_region_no_engine_raises(monkeypatch):
    """When no engine is available, OcrEngineUnavailable is raised."""

    def _fake_select_engine():
        from voice_commander.tools.ocr import OcrEngineUnavailable

        raise OcrEngineUnavailable("no engine")

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", _fake_select_engine)

    from voice_commander.tools.ocr import OcrEngineUnavailable, ocr_region

    with pytest.raises(OcrEngineUnavailable):
        ocr_region(0, 0, 100, 100)


def test_ocr_tesseract_nonzero_exit_logs_warning(tmp_path, monkeypatch, caplog):
    """When tesseract exits non-zero, warning is logged but text still returned."""
    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    def _fake_select_engine():
        return "tesseract"

    def _fake_capture(x, y, w, h):
        return fake_img

    def _fake_run(cmd, **kw):
        r = MagicMock()
        r.returncode = 1
        r.stdout = "partial"
        r.stderr = "Error opening data file"
        return r

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", _fake_select_engine)
    monkeypatch.setattr("voice_commander.tools.ocr._capture_region", _fake_capture)
    monkeypatch.setattr("subprocess.run", _fake_run)

    from voice_commander.tools.ocr import ocr_region

    with caplog.at_level(logging.WARNING, logger="voice_commander.tools.ocr"):
        result = ocr_region(0, 0, 100, 100)

    assert result == "partial"
    assert "exited with code 1" in caplog.text
    assert "Error opening data file" in caplog.text


def test_select_engine_resolves_config_from_package_root(tmp_path, monkeypatch):
    """_select_engine resolves config.toml relative to package root, not CWD."""
    # Change CWD to a temp directory that has no config.toml
    monkeypatch.chdir(tmp_path)

    # _select_engine should NOT raise FileNotFoundError just because CWD changed.
    # We mock winrt import to fail so we can test the tesseract fallback path.
    real_import = builtins.__import__

    def _block_winrt(name, *args, **kwargs):
        if "winrt" in name:
            raise ImportError("mocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_winrt)
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/tesseract" if cmd == "tesseract" else None
    )

    from voice_commander.tools.ocr import _REPO_ROOT, _select_engine

    captured_paths: list[Path] = []

    def _spy_config_load(path: Path, *args, **kwargs):
        captured_paths.append(path)
        raise FileNotFoundError("spy: no config")

    with patch("voice_commander.config.Config.load", side_effect=_spy_config_load):
        result = _select_engine()

    assert result == "tesseract"

    # Key assertion: Config.load must be called with _REPO_ROOT-relative path,
    # NOT a CWD-relative path — this is the M12 fix being validated.
    assert len(captured_paths) == 1, "Config.load should be called exactly once"
    expected = _REPO_ROOT / "config.toml"
    assert captured_paths[0] == expected, (
        f"Config.load called with {captured_paths[0]!r}, expected {expected!r} "
        f"(CWD was {tmp_path!r})"
    )


async def test_ocr_winrt_from_async_context(tmp_path, monkeypatch):
    """ocr_region dispatches to _ocr_winrt correctly when called from an async context."""
    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", lambda: "winrt")
    monkeypatch.setattr("voice_commander.tools.ocr._capture_region", lambda x, y, w, h: fake_img)

    import voice_commander.tools.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "_ocr_winrt", lambda img_path: "async ocr text")

    from voice_commander.tools.ocr import ocr_region

    # This runs inside an async context (pytest-asyncio gives us a running loop)
    result = ocr_region(0, 0, 100, 100)
    assert result == "async ocr text"


def test_ocr_winrt_sync_context_still_works(tmp_path, monkeypatch):
    """_ocr_winrt still works from plain sync context (regression guard)."""
    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", lambda: "winrt")
    monkeypatch.setattr("voice_commander.tools.ocr._capture_region", lambda x, y, w, h: fake_img)

    import voice_commander.tools.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "_ocr_winrt", lambda img_path: "sync ocr text")

    from voice_commander.tools.ocr import ocr_region

    result = ocr_region(0, 0, 100, 100)
    assert result == "sync ocr text"


async def test_ocr_winrt_loop_detection():
    """Smoke test: pytest-asyncio provides a running loop (validates test infra)."""
    # We're inside a running loop (pytest-asyncio).
    # Verify get_running_loop succeeds — this is the branch _ocr_winrt takes.
    loop = asyncio.get_running_loop()
    assert loop is not None  # Confirms we're in the async branch


def _make_winrt_mocks() -> tuple[dict, str]:
    """Build minimal WinRT module stubs. Returns (modules_dict, expected_text)."""
    expected_text = "mocked ocr output"

    mock_result = MagicMock()
    mock_result.text = expected_text

    mock_engine = MagicMock()
    mock_engine.recognize_async = AsyncMock(return_value=mock_result)

    mock_bitmap = MagicMock()
    mock_decoder = MagicMock()
    mock_decoder.get_software_bitmap_async = AsyncMock(return_value=mock_bitmap)

    mock_stream = MagicMock()
    mock_file = MagicMock()
    mock_file.open_async = AsyncMock(return_value=mock_stream)

    mock_ocr_engine_cls = MagicMock()
    mock_ocr_engine_cls.try_create_from_user_profile_languages = MagicMock(return_value=mock_engine)

    mock_bitmap_decoder_cls = MagicMock()
    mock_bitmap_decoder_cls.create_async = AsyncMock(return_value=mock_decoder)

    mock_storage_file_cls = MagicMock()
    mock_storage_file_cls.get_file_from_path_async = AsyncMock(return_value=mock_file)

    modules = {
        "winrt": MagicMock(),
        "winrt.windows": MagicMock(),
        "winrt.windows.graphics": MagicMock(),
        "winrt.windows.graphics.imaging": MagicMock(BitmapDecoder=mock_bitmap_decoder_cls),
        "winrt.windows.media": MagicMock(),
        "winrt.windows.media.ocr": MagicMock(OcrEngine=mock_ocr_engine_cls),
        "winrt.windows.storage": MagicMock(StorageFile=mock_storage_file_cls),
        "winrt.windows.storage.streams": MagicMock(),
    }
    return modules, expected_text


async def test_ocr_winrt_async_branch_exercises_threadpool(tmp_path, monkeypatch):
    """Real _ocr_winrt called from async context — loop detection routes to ThreadPoolExecutor.

    Unlike test_ocr_winrt_from_async_context, this test does NOT replace _ocr_winrt itself.
    WinRT modules are mocked at sys.modules level so the real loop-detection +
    ThreadPoolExecutor branch executes. A regression that reverts to bare asyncio.run()
    in the async branch would raise RuntimeError and fail this test.
    """
    import voice_commander.tools.ocr as ocr_mod

    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    winrt_modules, expected_text = _make_winrt_mocks()
    for mod_name, mock_mod in winrt_modules.items():
        monkeypatch.setitem(sys.modules, mod_name, mock_mod)

    # We're inside an async test — asyncio.get_running_loop() will succeed.
    # The real _ocr_winrt must detect this and dispatch via ThreadPoolExecutor.
    result = ocr_mod._ocr_winrt(fake_img)
    assert result == expected_text


def test_ocr_winrt_sync_branch_exercises_asyncio_run(tmp_path, monkeypatch):
    """Real _ocr_winrt called from sync context — no loop detected, asyncio.run() used directly.

    Verifies the sync path still works after the async-context fix was added.
    A regression that breaks the sync branch would raise here.
    """
    import voice_commander.tools.ocr as ocr_mod

    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    # Guard: this test must run outside a running event loop
    try:
        asyncio.get_running_loop()
        pytest.skip("test must run outside a running event loop")
    except RuntimeError:
        pass

    winrt_modules, expected_text = _make_winrt_mocks()
    for mod_name, mock_mod in winrt_modules.items():
        monkeypatch.setitem(sys.modules, mod_name, mock_mod)

    result = ocr_mod._ocr_winrt(fake_img)
    assert result == expected_text


def test_select_engine_winrt_available(monkeypatch):
    """When winrt.windows.media.ocr is importable, _select_engine returns 'winrt'."""
    real_import = builtins.__import__

    def _allow_winrt(name, *args, **kwargs):
        if name == "winrt.windows.media.ocr":
            return MagicMock()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _allow_winrt)

    with patch("voice_commander.config.Config.load", side_effect=FileNotFoundError):
        from voice_commander.tools.ocr import _select_engine

        result = _select_engine()

    assert result == "winrt"


def test_select_engine_config_forces_winrt():
    """Config with ocr_engine='winrt' makes _select_engine return 'winrt' without import check."""
    mock_cfg = MagicMock()
    mock_cfg.perception.ocr_engine = "winrt"

    with patch("voice_commander.config.Config.load", return_value=mock_cfg):
        from voice_commander.tools.ocr import _select_engine

        result = _select_engine()

    assert result == "winrt"


def test_select_engine_config_forces_tesseract():
    """Config ocr_engine='tesseract' → _select_engine returns 'tesseract' without import check."""
    mock_cfg = MagicMock()
    mock_cfg.perception.ocr_engine = "tesseract"

    with patch("voice_commander.config.Config.load", return_value=mock_cfg):
        from voice_commander.tools.ocr import _select_engine

        result = _select_engine()

    assert result == "tesseract"
