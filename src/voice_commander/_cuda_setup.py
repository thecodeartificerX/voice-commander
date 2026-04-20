from __future__ import annotations

import glob
import logging
import os
import sys
from importlib.resources import files

logger = logging.getLogger(__name__)

_NVIDIA_PIP_PACKAGES = ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc")

_registered = False


def register() -> None:
    """Make CUDA runtime DLLs from nvidia-* pip packages findable by native code.

    On Windows, ctranslate2's native LoadLibraryExW uses the process env block
    snapshotted at process start, so mutating os.environ['PATH'] after the
    interpreter is running does not help. os.add_dll_directory() only affects
    Python-level DLL loading. The working trick is to preload each DLL by
    absolute path via ctypes.WinDLL — once mapped, subsequent short-name
    LoadLibrary calls resolve from the process module list.

    No-op on non-Windows and when the nvidia pip packages are not installed.
    Idempotent.
    """
    global _registered
    if _registered or sys.platform != "win32":
        _registered = True
        return

    import ctypes

    loaded: list[str] = []
    skipped: list[tuple[str, str]] = []
    for pkg in _NVIDIA_PIP_PACKAGES:
        try:
            bin_dir = str(files(pkg).joinpath("bin"))
        except (ModuleNotFoundError, FileNotFoundError) as e:
            logger.info("cuda_setup: package %s unavailable (%s)", pkg, e)
            continue
        if not os.path.isdir(bin_dir):
            logger.info("cuda_setup: %s bin dir not found at %s", pkg, bin_dir)
            continue
        logger.info("cuda_setup: registering %s", bin_dir)
        os.add_dll_directory(bin_dir)
        for dll_path in sorted(glob.glob(os.path.join(bin_dir, "*.dll"))):
            try:
                ctypes.WinDLL(dll_path)
                loaded.append(os.path.basename(dll_path))
            except OSError as e:
                skipped.append((os.path.basename(dll_path), str(e)[:80]))
    logger.info("cuda_setup: preloaded %d DLLs, skipped %d", len(loaded), len(skipped))
    logger.debug("cuda_setup: loaded=%s", loaded)
    for name, err in skipped:
        logger.debug("cuda_setup: skipped %s: %s", name, err)

    _registered = True
