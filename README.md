# Voice Commander

A Windows voice-command launcher: press Scroll Lock, speak a command, and the matching keyboard action fires instantly — no cloud, no latency, fully offline on your GPU.

---

## Features (MVP Toolset)

| Command phrase | Action |
|---|---|
| "copy" | Ctrl+C |
| "paste" | Ctrl+V |
| "cut" | Ctrl+X |
| "select all" | Ctrl+A |
| "focus browser" | Raise browser window |
| "focus terminal" | Raise terminal window |
| "minimize" | Minimize active window |
| "maximize" | Maximize active window |
| "new tab" | Ctrl+T |
| "close tab" | Ctrl+W |
| "reopen tab" | Ctrl+Shift+T |
| "reload" | Ctrl+R |
| "lock screen" | Win+L |
| "take screenshot" | Win+Shift+S |

---

## Prerequisites

| Requirement | Minimum version |
|---|---|
| Python | 3.11+ |
| [uv](https://docs.astral.sh/uv/) | latest |
| CUDA Toolkit | 12.x |
| cuDNN | 9.x |
| NVIDIA GPU | Any CUDA-capable card (6 GB VRAM recommended for `small.en`) |

> **Windows only.** Voice Commander uses `pynput`, `pyautogui`, and Windows-native toast notifications — it does not run on Linux or macOS.

---

## Install

```bash
# 1. Clone the repo
git clone https://github.com/sakib/voice-commander.git
cd voice-commander

# 2. Install all dependencies (creates an isolated venv automatically)
uv sync
```

Ensure your CUDA 12 DLLs are on `PATH` and that `nvidia-smi` reports a visible GPU before running.

---

## Quickstart

```bash
uv run voice-commander
```

1. The daemon starts, loads the Whisper model onto your GPU, and arms the hotkey.
2. Press **Scroll Lock** — you will hear a start chime.
3. Speak a command (e.g. "copy").
4. Press **Scroll Lock** again — you will hear a stop chime.
5. The matched tool fires. If nothing matches you will hear a miss chime and a toast notification.

---

## Configuration

All tuneable values live in [`config.toml`](config.toml) at the project root. Missing keys fall back to built-in defaults defined in `src/voice_commander/config.py`.

For the full schema and field-level documentation see **§5 — Configuration** in the design spec:
[`docs/superpowers/specs/2026-04-19-voice-commander-design.md`](docs/superpowers/specs/2026-04-19-voice-commander-design.md)

---

## Project Layout

For a guided reading order and links to every documentation file see:
[`docs/index.md`](docs/index.md)

---

## How to Add a New Tool

New tools follow the TDD pattern described in **Phase 4** of the implementation plan:
[`docs/superpowers/plans/2026-04-19-voice-commander-plan.md`](docs/superpowers/plans/2026-04-19-voice-commander-plan.md)

The short version:
1. Add a failing test in `tests/unit/test_tools_<category>.py`.
2. Implement the tool function in `src/voice_commander/tools/<category>.py`.
3. Register the tool with its trigger phrases in the `ToolRegistry`.
4. Add a fixture WAV and an integration test.

---

## Contributing

See [`CLAUDE.md`](CLAUDE.md) for project conventions, architectural decisions, and the agent-collaboration workflow used on this codebase.

---

## License

MIT License

Copyright (c) 2026 Sakib

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
