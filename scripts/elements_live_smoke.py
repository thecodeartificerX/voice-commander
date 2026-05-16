"""Manual smoke test for elements-mode scanning (ADR 0087).

Run with a target window already in the foreground:

    python scripts/elements_live_smoke.py

It prints every clickable element the scanner found in the foreground window.
For a browser, launch Chrome/Edge with --force-renderer-accessibility to see
page content (native chrome shows regardless).
"""

from __future__ import annotations

import time

from voice_commander.elements import desktop, scanner
from voice_commander.elements.uia import uia_available


def main() -> None:
    if not uia_available():
        print("FAIL: the 'uiautomation' library is not importable.")
        return

    print("Switch to your target window — scanning in 3 seconds...")
    time.sleep(3.0)

    hwnd = desktop.foreground_window()
    if hwnd == 0:
        print("FAIL: no foreground window.")
        return

    monitor = desktop.monitor_rect(hwnd)
    started = time.monotonic()
    elements = scanner.scan_window(hwnd, max_elements=200, timeout_s=3.0)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    print(f"hwnd={hwnd}  monitor={monitor}  scan={elapsed_ms:.0f}ms")
    print(f"found {len(elements)} clickable element(s):")
    for element in elements:
        print(
            f"  [{element.index:>3}] {element.control_type:<18} "
            f"rect={element.rect} center={element.center}  {element.label!r}"
        )
    if not elements:
        print("WARNING: nothing found — for a browser, check "
              "--force-renderer-accessibility.")


if __name__ == "__main__":
    main()
