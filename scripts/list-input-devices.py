"""list-input-devices.py — print PortAudio input devices as JSON.

Usage:
    uv run python scripts/list-input-devices.py

Prints a JSON array to stdout.  Each element:
    {"index": int, "name": str, "hostapi": str, "max_input_channels": int}
Only devices with max_input_channels > 0 are included.

On any error prints {"error": "..."} and exits with code 1.
"""

import json
import sys


def main() -> int:
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:
        print(json.dumps({"error": f"import sounddevice failed: {exc}"}))
        return 1

    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        result = []
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] <= 0:
                continue
            hostapi_idx = dev.get("hostapi", 0)
            try:
                hostapi_name = hostapis[hostapi_idx]["name"]
            except (IndexError, KeyError):
                hostapi_name = str(hostapi_idx)

            result.append(
                {
                    "index": idx,
                    "name": dev["name"],
                    "hostapi": hostapi_name,
                    "max_input_channels": dev["max_input_channels"],
                }
            )

        print(json.dumps(result, ensure_ascii=False))
        return 0

    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
