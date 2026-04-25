# Voice Commander

> Press a key. Say the command. Ship.

A **local-first, GPU-accelerated voice-command launcher for Windows.** You say the actual command — "copy", "new tab", "focus browser", "click" — and Voice Commander fires the matching keystroke or action instantly. No cloud. No latency. No wake word. No memorizing cryptic shortcuts.

It is to Talon Voice what a utility knife is to a Swiss Army knife: smaller, sharper, and entirely yours to reshape.

![MIT License](https://img.shields.io/badge/license-MIT-blue.svg) ![Platform Windows](https://img.shields.io/badge/platform-Windows-lightgrey) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue) ![CUDA](https://img.shields.io/badge/CUDA-12.x-green)

---

## Why this exists

Most voice tools fall into two camps:

- **Cloud dictation** (Google, Siri, Dragon). Fast, accurate, and a hot mic sent to someone else's server.
- **Programmable voice frameworks** (Talon, Dragonfly). Powerful, but you learn a whole grammar before you say your first word.

Voice Commander is the boring middle ground. Push-to-talk, speak plain English, get a keystroke. Everything runs on your GPU. Adding a new command is a six-line Python file. The entire codebase is small enough to read in an afternoon and fork in a weekend.

---

## Features

- **Push-to-talk session model.** Tap Scroll Lock to open a session → speak one or many commands back-to-back → tap again to close. Silero VAD auto-segments utterances on silence, so you never press a key between commands.
- **Sub-second latency.** [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) running `small.en` on CUDA, with an ndarray hand-off (no temp-file I/O on the hot path), puts the speech-end → keystroke budget at ~700 ms.
- **LLM-powered routing.** Every utterance is dispatched through a local LLM (LM Studio, default model: Gemma 4 E4B) which returns a typed, ordered plan of tool calls. Chained commands like *"new tab then paste"* work out of the box. If LM Studio is offline the daemon degrades to a miss chime and keeps running.
- **Mute hotkey for dictation coexistence.** Secondary key (default Right Ctrl) suspends the mic so Voice Commander does not fight your other dictation software. See [ADR 0025](docs/decisions/0025-mute-hotkey-for-external-dictation.md).
- **Web UI.** Open `http://127.0.0.1:8765` while the daemon runs to edit phrases, toggle tools, and hot-reload without restarting. HTMX + FastAPI, no SPA build step. See [ADR 0022](docs/decisions/0022-htmx-over-spa.md).
- **Sidecar TOML metadata.** Phrases and descriptions live in `.toml` files beside each tool module, so config and code evolve independently. See [ADR 0021](docs/decisions/0021-sidecar-toml-per-tool.md).
- **Audio + visual feedback.** A miss chime on low confidence, silence on success ([ADR 0014](docs/decisions/0014-miss-only-chimes.md)). The sprite companion provides continuous visual state ([ADR 0049](docs/decisions/0049-miss-chimes-retained.md)). No toast notifications.
- **On-screen sprite companion.** A pixel-art desktop pet mirrors daemon state — idle, listening, thinking, success, miss. Runs as a separate process (`voice_sprite`) via SSE, so a sprite crash never affects voice recognition. Configurable corner, size, and art via `config.toml [sprite]`. See [ADR 0045](docs/decisions/0045-sprite-separate-process-via-sse.md).
- **Command HUD.** A persistent RPG-style chat log renders one line per voice command directly next to the sprite — colour-coded by outcome (green = ok, red = error, amber = miss). Entries hold full opacity for 4 s, then fade over 3 s. The HUD lives inside the same pyglet window as the sprite, so no second overlay process is needed. Configure via `config.toml [hud]`. See [ADR 0051](docs/decisions/0051-command-hud-overlay.md).
- **Cursor-follow across monitors.** The sprite (and its attached HUD) docks to the bottom-right of whichever monitor holds the mouse cursor, sitting above the taskbar regardless of which edge the taskbar is on. 30 Hz polling — no mouse hook, no elevated rights required. See [ADR 0053](docs/decisions/0053-sprite-follows-cursor-monitor.md).
- **CUDA preloading shim.** cuBLAS and cuDNN are preloaded via `ctypes` before `faster_whisper` imports, so venv-local pip wheels resolve cleanly regardless of shell `PATH` state. You still need CUDA Toolkit and cuDNN installed system-wide (see [CUDA setup](#cuda-setup) below). See [ADR 0012](docs/decisions/0012-cuda-dll-bundling.md).

---

## Built-in command set

| Group | Commands |
|---|---|
| Clipboard | `copy`, `paste`, `cut`, `select all` |
| Window | `focus browser`, `focus terminal`, `minimize`, `maximize` |
| Browser | `new tab`, `close tab`, `reopen tab`, `reload`, `email` (Gmail), `messenger` (FB) |
| System | `lock screen`, `take screenshot`, `cancel` |
| Mouse | `click`, `right click` |

Every phrase is editable in the web UI or the sidecar TOML next to the tool.

> **Note.** The browser-focus group currently targets [Comet](https://comet.perplexity.ai) specifically (my daily driver). If you use a different browser, change two lines in `src/voice_commander/tools/_win32.py` or open an issue and we will land a config-driven lookup.

---

## Requirements

| Requirement | Minimum | Notes |
|---|---|---|
| OS | **Windows 10/11** | Linux/macOS will not run — we use `pynput`, `pyautogui`, `winsound`, `pywin32`. |
| Python | 3.11+ | 3.11 is the floor; 3.12 tested. |
| [uv](https://docs.astral.sh/uv/) | latest | Replaces `pip`/`venv`/`poetry`. |
| NVIDIA GPU | any CUDA-capable card | 6 GB VRAM recommended for `small.en`. CPU inference works but is ~5× slower. |
| CUDA Toolkit | 12.x | Install via NVIDIA's official MSI, add to `PATH`. See [CUDA setup](#cuda-setup). |
| cuDNN | 9.x | Install via NVIDIA's official MSI, add to `PATH`. See [CUDA setup](#cuda-setup). |
| Microphone | any input device | The start script ships a device picker. |

---

## Install

```powershell
# 1. Clone
git clone https://github.com/thecodeartificerX/voice-commander.git
cd voice-commander

# 2. Let uv build the venv and pull every dependency
uv sync

# 3. Pick your microphone (writes to config.toml)
uv run python scripts/set-audio-device.py

# 4. Launch
uv run voice-commander
# — or, on Windows, use the bundled wrapper with device-picker
.\start.ps1
```

> **Restart daemon button.** The **Restart daemon** button in the web UI requires `start.ps1` (or `voice-commander-supervisor` directly). Running `uv run voice-commander` standalone is supported but the Restart button will return 503 in that mode.

**CPU-only install.** Edit `config.toml`:

```toml
[transcription]
device = "cpu"
compute_type = "int8"
```

---

## CUDA setup

Voice Commander's transcription layer is [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper), which wraps [CTranslate2](https://github.com/OpenNMT/CTranslate2) and expects real CUDA + cuDNN libraries available at load time. The one-time setup is where **every** install headache lives — nail this and everything else is just `uv sync`.

### The happy path

1. **Install CUDA Toolkit 12.x** from NVIDIA's official MSI: <https://developer.nvidia.com/cuda-downloads>. Pick the Windows installer, run it, accept the defaults. This puts `nvcc`, driver runtime, and core CUDA DLLs in `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.x\bin`.
2. **Install cuDNN 9.x** from NVIDIA's cuDNN downloads page: <https://developer.nvidia.com/cudnn-downloads>. Use the **MSI installer for Windows** (not the zip). It registers cuDNN into the same Toolkit layout automatically.
3. **Verify both are on `PATH`.** The CUDA MSI adds its `bin` directory automatically; cuDNN's MSI places its DLLs alongside. Open a fresh PowerShell and confirm:

   ```powershell
   nvcc --version        # should print CUDA 12.x
   nvidia-smi            # should show your GPU and a CUDA 12.x runtime version
   where cudnn_ops64_9.dll   # should resolve to a real path
   ```

   If any command fails, re-run the installer or append the CUDA `bin` folder to your system `PATH` environment variable and start a new shell.
4. **Install Voice Commander.**

   ```powershell
   uv sync
   uv run voice-commander
   ```

   `uv` pulls `faster-whisper` (and the `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels the shim uses) into the venv. On first run, the Whisper `small.en` weights download (~500 MB, cached under `%USERPROFILE%\.cache\huggingface`).

### If it breaks

The failure mode is almost always a CUDA / cuDNN loading issue surfacing inside `CTranslate2`. Read [the faster-whisper README](https://github.com/SYSTRAN/faster-whisper) end-to-end — especially the *"GPU support"* and *"Installation"* sections — because the library's own docs cover the Windows gotchas in more detail than this README can.

Common symptoms and fixes:

- `Library cublas64_12.dll is not found` → CUDA Toolkit not on `PATH`. Reinstall or fix `PATH`, new shell.
- `Could not load library cudnn_ops_infer64_9.dll` → cuDNN 9.x not installed, or mismatched major version (9.x required, not 8.x).
- `CUDA driver version is insufficient for CUDA runtime version` → upgrade your NVIDIA GPU driver.
- `Cuda failure at … out of memory` → model too large for your VRAM. Switch `model_size = "base.en"` or `"tiny.en"` in `config.toml`, or drop to `compute_type = "int8_float16"`.
- All else fails → set `device = "cpu"` in `config.toml` to rule out GPU issues, confirm the daemon runs, then debug CUDA separately.

Once `uv run voice-commander` prints `Model loaded on cuda` and you get a successful command dispatch, you are done — the CUDA chapter of your life is closed.

---

## Quickstart

1. Run `uv run voice-commander`. The daemon loads Whisper into VRAM and arms Scroll Lock. First boot downloads the `small.en` model (~500 MB, cached).
2. Press **Scroll Lock**. *(No chime — by design; see [ADR 0014](docs/decisions/0014-miss-only-chimes.md).)*
3. Speak one or more commands: *"copy... new tab... paste..."* The VAD splits them on silence.
4. Press **Scroll Lock** again to close the session.
5. You only hear audio when something goes wrong — a miss chime on low-confidence transcripts or no-match phrases. Success is silent.

**Optional mute:** press **Right Ctrl** during a session to suspend the mic without ending the session. Press again to resume. Useful when another app (Windows Voice Access, browser dictation) also wants Right Ctrl.

---

## Configuration

All runtime settings live in [`config.toml`](config.toml) — single source of truth, tracked in git. Per-field `VC_LLM_*` environment variables override `[llm]` fields at daemon start; see `docs/agents/technical-decisions.md` for the precedence chain.

| Section | Key | Default | Purpose |
|---|---|---|---|
| `[hotkey]` | `key` | `"scroll_lock"` | Session toggle. Any `pynput.keyboard.Key` name. |
| `[hotkey]` | `mute_key` | `"ctrl_r"` | Mute-within-session toggle. Set `""` to disable. |
| `[audio]` | `device` | `-1` | PortAudio device index. `-1` = system default. |
| `[transcription]` | `model_size` | `"small.en"` | `tiny.en` / `base.en` / `small.en` / `medium.en`. |
| `[transcription]` | `device` | `"cuda"` | `"cuda"` or `"cpu"`. |
| `[transcription]` | `min_confidence` | `0.30` | Transcripts below this score fire a miss chime. |
| `[llm]` | `endpoint_url` | `"http://localhost:1234/v1"` | LM Studio (or any OpenAI-compatible) endpoint. |
| `[llm]` | `model_id` | `"google/gemma-4-e4b"` | Model served by the endpoint. |
| `[llm]` | `default_browser` | `"chrome"` | Browser name templated into the few-shot system prompt (ADR 0044). |
| `[llm]` | `timeout_ms` | `1200` | Per-request budget in milliseconds. |
| `[llm]` | `warmup_timeout_ms` | `5000` | One-shot startup warmup budget — larger than `timeout_ms` to cover cold prefill. |
| `[llm]` | `max_plan_steps` | `12` | Maximum tool calls in one dispatched plan. |
| `[llm]` | `warmup_on_startup` | `true` | POST a 1-token completion at startup to seed the KV cache. |
| `[llm]` | `focus_fuzzy_threshold` | `70` | Minimum WRatio score for `focus(target)` to pick a window (ADR 0041/0042). |
| `[llm]` | `open_fuzzy_threshold` | `70` | Minimum WRatio score for `open(target)` to pick an app / Start-Menu entry. |
| `[vad]` | `threshold` | `0.4` | Silero speech-probability floor. |
| `[vad.gates]` | `min_word_count` | `1` | Drop transcripts shorter than N words. |
| `[web]` | `enabled` | `true` | Start the management UI on port 8765. |
| `[hud]` | `enabled` | `true` | Master toggle for the chat-log HUD |
| `[hud]` | `max_lines` | `5` | Number of entries visible at once |
| `[hud]` | `hold_ms` / `fade_ms` | `4000` / `3000` | Full-opacity hold then linear fade |
| `[hud]` | `llm_fallback_enabled` | `true` | Use LM Studio to summarize errors |
| `[sprite]` | `follow_cursor` | `true` | Sprite tracks cursor across monitors |
| `[sprite]` | `follow_poll_hz` | `30` | Cursor-poll rate in Hz |

Full schema + rationale: [`docs/superpowers/specs/2026-04-19-voice-commander-design.md`](docs/superpowers/specs/2026-04-19-voice-commander-design.md) §5.

---

## LLM Router

Every transcript goes through a local LLM unconditionally. `LLMRouter` POSTs to an LM Studio OpenAI-compatible endpoint (default model: Gemma 4 E4B) with a few-shot system prompt describing the nine-verb catalog (ADR 0043). The LLM returns a one-shot ordered plan of typed tool calls — including chained commands like *"open a new tab then paste"* — which the `Dispatcher` executes step-by-step with per-tool settle delays. For the `focus` and `open` verbs, the `target` string is grounded onto a concrete `hwnd` / launch token by the pure-function `resolver` module (rapidfuzz scoring over visible windows / Start-Menu + AppsFolder, ADR 0042). If LM Studio is offline, unreachable, or returns an unparseable response, the router degrades silently to a miss chime; the daemon keeps running.

Configure in `config.toml`:

```toml
[llm]
endpoint_url           = "http://localhost:1234/v1"
model_id               = "google/gemma-4-e4b"
default_browser        = "chrome"
timeout_ms             = 1200
warmup_timeout_ms      = 5000
max_plan_steps         = 12
warmup_on_startup      = true
focus_fuzzy_threshold  = 70
open_fuzzy_threshold   = 70
```

> **Default browser.** `default_browser` is substituted into the few-shot system prompt at `LLMRouter.__init__` time. It teaches the model which browser to name when a voice command has web intent ("search how to lose weight" → `focus(target="chrome")` + `press(ctrl+t)` + ...). Change this string, restart the daemon, and every subsequent web-intent utterance routes through your browser of choice. See [ADR 0044](docs/decisions/0044-few-shot-system-prompt.md).

> **Warmup note.** When `warmup_on_startup = true`, the daemon POSTs a real `/v1/chat/completions` request (with `max_tokens = 1`) at startup. This seeds LM Studio's prefix KV cache with the system prompt and tools array so the first real voice command lands on a warm cache. Startup takes ~5 s longer, but the first spoken command completes within the normal `timeout_ms` budget. `GET /v1/models` alone does not seed the cache — see [ADR 0038](docs/decisions/0038-llm-router-warmup-real-chat-completion.md).

> **Fuzzy thresholds.** The resolver's `focus_fuzzy_threshold` / `open_fuzzy_threshold` knobs control how tolerant `focus(target)` and `open(target)` are when mapping the LLM's `target` string onto a visible window / Start-Menu entry. Lower (e.g. 60) = more permissive, accepts looser phonetic matches; higher (e.g. 80) = stricter, fewer false positives but more misses. Defaults of 70 were validated against a 20-utterance live-test script.

Full design: [`docs/superpowers/specs/2026-04-21-llm-default-no-rapidfuzz-design.md`](docs/superpowers/specs/2026-04-21-llm-default-no-rapidfuzz-design.md). Routing architecture decisions: [ADR 0040](docs/decisions/0040-llm-only-routing-replaces-hybrid.md), [ADR 0041](docs/decisions/0041-rapidfuzz-for-parameter-resolution.md), [ADR 0042](docs/decisions/0042-resolver-module-design.md), [ADR 0043](docs/decisions/0043-nine-verb-primitive-catalog.md), [ADR 0044](docs/decisions/0044-few-shot-system-prompt.md).

---

## Architecture at a glance

```
HotkeyCtrl ─toggle─▶ StreamingRecorder ─ndarray─▶ Transcriber ─text─▶ LLMRouter ─plan─▶ Dispatcher ──▶ tool fn
  pynput            sounddevice + soxr +          faster-whisper      httpx→LM Studio  run_plan()     + resolver.*
                    silero-vad (48k→16k)          (CUDA, small.en)    (few-shot prompt) + FeedbackSink    (focus/open)
                                                                          │miss
                                                                          ▼
                                                                     feedback.on_miss()
                                                       ┌──────────────────┘
                                                       ▼
                                                  EventBus ──SSE /events──▶ voice_sprite (separate process)
                                                  (pub/sub)                  pyglet + StateMachine + ChatLogRenderer
                                                                             + CursorDock (30 Hz) + Summarizer
```

Five long-lived threads (PortAudio callback → VAD worker → pipeline worker, hotkey listener, plus 1 Hz heartbeat) connected by thread-safe queues. Every subsystem is independently unit-testable with no hardware.

Read [`docs/architecture.md`](docs/architecture.md) for the full component contracts. Every big decision has an ADR in [`docs/decisions/`](docs/decisions/).

---

## Adding a new command

The fastest path is the `commander` Claude skill shipped in this repo — it walks you through a seven-question interview and writes the Python, TOML, and tests for you. To do it by hand:

**1. Write the tool function** — `src/voice_commander/tools/<group>.py`

```python
from ..registry import tool

@tool
def my_command() -> None:
    """One-line description shown in the web UI."""
    # do the thing
    ...
```

**2. Register phrases** — `src/voice_commander/tools/<group>.toml`

```toml
[tools.my_command]
phrases = ["my command", "do the thing", "go"]
description = "One-line description shown in the web UI."
enabled = true
```

**3. Write a unit test** — `tests/unit/test_tools_<group>.py`

```python
def test_my_command_fires_expected_side_effect(monkeypatch):
    called = []
    monkeypatch.setattr("voice_commander.tools.<group>.the_lib", lambda: called.append(1))
    from voice_commander.tools.<group> import my_command
    my_command()
    assert called == [1]
```

**4. Verify**

```powershell
uv run pytest tests/unit/test_tools_<group>.py -q
```

That is it. The registry auto-discovers every module under `voice_commander.tools`, so there is nothing else to wire up.

---

## Contributing

Pull requests welcome. This is a small, opinionated codebase — but the surface for useful contributions is huge:

### Easy first PRs
- Add a new tool (see above). Every new tool expands what you can say.
- Improve a tool's description or sidecar TOML so the LLM can select it more reliably.
- Fix a miss that you actually hit. Reproduce with the fixture, inspect the LLM plan, tighten the tool description.

### Meatier contributions
- **Cross-browser support.** Replace the Comet-only `focus_browser` with a config-driven lookup (ProgID → EXE, or just a user-supplied path).
- **Per-app command sets.** Activate different tools when Chrome vs VS Code is focused (Phase 6 roadmap).
- **Wake-word mode.** Drop the push-to-talk hotkey for an always-on wake phrase. Porcupine or OpenWakeWord are the obvious choices.
- **Argument-bearing commands.** "Open readme in the projects folder" — the LLM router already handles chained tool calls; argument extraction (path, app name, etc.) is the next step. Design is sketched in [`docs/superpowers/specs/`](docs/superpowers/specs/).
- **Tray icon + systray controls.** Stub exists in `pyproject.toml` (optional `tray` extra); nobody has wired it up yet.

### House rules
1. **Docs before code.** Architectural decisions land in `docs/decisions/` as an ADR **at the time of the decision**, not retroactively. Look at any existing ADR for the template.
2. **Tests before claims.** No PR is merged until `uv run pytest` is green. Every new tool ships a unit test; every new subsystem ships its own test file.
3. **Narrow interfaces.** Each module has one job. If your PR crosses more than three files outside `tools/`, split it.
4. **Audio-only feedback.** No toast notifications, no popups, no focus stealing. See ADR 0013 for the reasoning.
5. **No AI-generated commit messages that fib.** If Claude wrote the code, keep the `Co-Authored-By` trailer; if you wrote it, drop the trailer. Honesty over optics.
6. **Conventional commits.** `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`. Scope the feature where useful: `feat(tools): add volume up/down`.

### Dev setup

```powershell
# Install dev deps
uv sync

# Full suite
uv run pytest

# Lint + type-check
uv run ruff check .
uv run mypy src

# Fast iteration (skip integration tests)
uv run pytest -m "not integration"

# Run with live debug logs
$env:VC_LOG_LEVEL="DEBUG"; uv run voice-commander
```

Coverage floor is 80% (enforced in `pyproject.toml`). Hardware-dependent modules (`_cuda_setup.py`, `hotkey.py`, `transcriber.py`, `tools/_win32.py`) are excluded from the unit coverage target and validated via `@pytest.mark.hardware` tests + manual phase gates documented in [`docs/testing-strategy.md`](docs/testing-strategy.md).

### Filing an issue

Bugs: include `voice-commander.log`, your `config.toml`, output of `uv run python scripts/list-input-devices.py`, and `nvidia-smi`.

Feature requests: describe the utterance you want to say and what should happen when you say it. "Voice Commander should…" is a better opener than "I want a button that…".

---

## Documentation map

| File | What it is |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Canonical project rules for humans and AI agents |
| [`docs/architecture.md`](docs/architecture.md) | Subsystem diagram + component contracts |
| [`docs/libraries.md`](docs/libraries.md) | Every dependency and why it is here |
| [`docs/gotchas.md`](docs/gotchas.md) | Windows traps, CUDA DLL quirks, threading pitfalls |
| [`docs/testing-strategy.md`](docs/testing-strategy.md) | Four-layer test pyramid + per-phase validation |
| [`docs/decisions/`](docs/decisions/) | One ADR per locked decision |
| [`docs/references/`](docs/references/) | Vendored framework docs (faster-whisper, silero-vad, etc.) |
| [`docs/superpowers/specs/`](docs/superpowers/specs/) | Design docs from brainstorming |
| [`docs/superpowers/plans/`](docs/superpowers/plans/) | Implementation plans |

Start at [`docs/index.md`](docs/index.md) for a guided reading order.

---

## Roadmap

- ✅ Phases 0–5 complete (scaffolding → MVP toolset → hardening)
- ✅ Web UI for command management
- ✅ VAD streaming (session-based, auto-segmented)
- ✅ Mute hotkey for dictation coexistence
- 🔜 Per-app command sets
- 🔜 Wake-word mode (opt-in)
- ✅ LLM intent router (unconditional dispatch via LM Studio)
- 🔜 Argument-bearing commands (path, app name extraction via tool-calling)

---

## Acknowledgements

Built on the shoulders of:
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2-accelerated Whisper
- [silero-vad](https://github.com/snakers4/silero-vad) — ONNX voice activity detection
- [sounddevice](https://python-sounddevice.readthedocs.io/) / [soxr](https://pypi.org/project/soxr/) — audio I/O and resampling
- [pynput](https://pynput.readthedocs.io/) / [pyautogui](https://pyautogui.readthedocs.io/) — hotkey and keystroke emulation
- [FastAPI](https://fastapi.tiangolo.com/) / [HTMX](https://htmx.org/) — the web UI

Inspired in equal parts by [Talon Voice](https://talonvoice.com/) and the desire to never learn Talon Voice.

---

## License

MIT. See [LICENSE](LICENSE) (or the inline notice below).

> Copyright (c) 2026 Sakib
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
