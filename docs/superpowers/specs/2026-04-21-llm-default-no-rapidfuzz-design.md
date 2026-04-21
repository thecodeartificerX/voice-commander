# LLM-Only Default — Drop rapidfuzz Command Routing, Adopt Verb Primitives + Few-Shot Prompt

**Date:** 2026-04-21
**Status:** Draft
**Supersedes:** `docs/decisions/0026-hybrid-router.md`, `docs/decisions/0027-fuzzy-threshold.md`
**Parent spec:** `docs/superpowers/specs/2026-04-21-llm-router-design.md` (hybrid design, now retired)

## Summary

Retire the hybrid router. LLM routing becomes the only path. rapidfuzz is kept as a dependency but repurposed: it no longer matches utterances to tools — it fuzzy-matches **parameter values** inside two verbs (`focus` and `open`). The phrase-shortcut tool catalog (window, browser, clipboard, system sidecars) is deleted and replaced by a 9-verb primitive catalog that the LLM chains via OpenAI-style `tool_calls`. The system prompt carries three few-shot examples, templated from `[llm].default_browser` at assembly time, so the model learns chaining discipline without inflating prefill on every call. Config section renames `[llm_router]` → `[llm]` and every field's resolution source (env / `config.local.toml` / `config.toml`) is logged at startup. `--router-mode` CLI flag, `.last-router-mode` persistence file, and `start.ps1` router prompt are removed.

Ship as one pull request. The repo is pre-1.0, single-user, and the hybrid was opt-in (`enabled = false`) on main, so there is no migration burden.

## Goals

1. One routing path. No modes. No thresholds that silently reject or accept utterances.
2. LLM sees a small verb catalog it can chain proactively for multi-step commands (e.g. "search how to lose weight" = 5 tool calls).
3. Natural-language parameter values (`focus("notpad")`, `open("spoti")`) resolve to real processes / apps via rapidfuzz, without the LLM having to know process names.
4. Every config field's source is visible at daemon start — silent fallback never happens.
5. Net codebase shrinks. Fewer moving parts than hybrid.
6. Live behavior validated against LM Studio (Gemma 4 E4B) at ≥18/20 utterances correct on a documented 20-utterance regression set.

## Non-goals

- Agentic ReAct-style loops (LLM observes tool results and re-plans). Plans remain one-shot. Door kept open for a future spec.
- Multi-turn conversational context. Every router call is stateless (ADR 0030 unchanged).
- Cloud inference.
- Wake-word, per-app command sets, TTS replies.
- Expanded mouse support (`click(x, y)`, `scroll`, drag). Out of v1 foundation.
- Named close of an arbitrary window (LLM chains `focus(target) → close_window()`).

## Architecture

Pipeline collapses from hybrid to single path.

```
HotkeyCtrl ─▶ StreamingRecorder ─▶ Transcriber ─▶ LLMRouter ─▶ Dispatcher.run_plan() ─▶ verb tools
   pynput    sounddevice+VAD       faster-whisper   httpx → LM Studio   linear exec w/ settle_ms
```

Threads unchanged (PortAudio callback / VAD worker / pipeline worker / hotkey listener). Queues unchanged. Feedback sink unchanged except `on_match` is no longer emitted (no phrase-match path exists).

**Deleted modules.** `matcher.py`, `tools/window.py`+`.toml`, `tools/browser.py`+`.toml`, `tools/clipboard.py`+`.toml`, `tools/system.py`+`.toml`, `tools/mouse.py`+`.toml` (click merged into primitives).

**Added module.** `resolver.py` — two pure functions (`resolve_window`, `resolve_app`) with rapidfuzz-based candidate selection.

**Rewritten modules.** `tools/primitives.py`+`.toml` (new verb catalog), `llm_router.py` (system-prompt assembly + templating), `config.py` (new `LLMConfig`, source-logged resolution), `dispatcher.py` (drop `dispatch()`, keep `run_plan()`), `daemon.py` (unconditional router wiring), `__main__.py` (drop `--router-mode`).

## Verb catalog (v1)

Single sidecar `src/voice_commander/tools/primitives.toml`. All 9 tools are `llm_only = true` and have `phrases = []`.

| Verb | Params | Behavior | Self-verify | settle_ms |
|---|---|---|---|---|
| `focus` | `target: str` | Resolve → win32 attach-thread focus → `_verify_foreground` | yes (today) | 200 |
| `type` | `text: str` | pyautogui.write with 0.02s interval. Truncate >500 chars, log warning | no | 50 |
| `open` | `target: str` | Resolve (URI / path / Start Menu / AppsFolder) → `os.startfile` or `shell:AppsFolder\...` | yes (post-launch window poll, best effort) | 500 |
| `close` | — | pyautogui.hotkey('ctrl', 'w') | yes (fg hwnd changed) | 100 |
| `close_window` | — | pyautogui.hotkey('alt', 'f4') | yes (fg hwnd changed) | 100 |
| `press` | `combo: str` | Split on `+`, pyautogui.hotkey(*keys) | no | 50 |
| `wait` | `ms: int` | time.sleep | — | 0 |
| `click` | `button: str = "left"` | pyautogui.click(button=...). Accepts `left`/`right`/`middle`; reject others | no | 50 |
| `no_match` | `reason: str` | No-op (router intercepts) | — | 0 |

### Python symbol vs LLM-visible name

`type` shadows the Python builtin. Implementation option chosen: register the Python function `type_text` under the LLM-visible name `type` via the registry. Shorter name for the LLM (smaller prefill, fewer tokens). The registry already supports `name=` override on `@tool`.

### Self-verify details

- `focus`: existing `_verify_foreground` poll (60 ms). Raises `FocusWindowError` on mismatch.
- `open`: after `os.startfile`, poll `EnumWindows` for ≤500 ms looking for a new hwnd whose title/process fuzzy-matches `target`. Best-effort — does not raise on timeout (some apps take seconds to appear); logs INFO on success, WARNING on timeout.
- `close` / `close_window`: snapshot foreground hwnd before, poll for ≤100 ms expecting foreground hwnd to change (or the previous hwnd to become invalid). On timeout, log WARNING, do not raise.

`press` / `type` / `wait` / `click` are not self-verifying in v1.

## Parameter resolver (`resolver.py`)

Two pure functions. rapidfuzz is the sole fuzzy engine. Scorer: `WRatio`. Threshold default 70, configurable.

### `resolve_window(target: str) -> int`

```
1. EnumWindows. For each visible hwnd:
     - title = GetWindowText(hwnd)
     - tid, pid = GetWindowThreadProcessId(hwnd)
     - proc_name = GetModuleBaseName(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, pid))
       (falls back to empty string if OpenProcess denies)
2. Build candidates = [(hwnd, proc_name, title), ...].
3. For each candidate, score = max(WRatio(target, proc_name), WRatio(target, title)).
4. Pick argmax. If score < focus_fuzzy_threshold → raise FocusWindowError with top-3 candidates+scores.
5. Return hwnd.
```

No caching (windows change constantly).

### `resolve_app(target: str) -> str`

```
1. If target has URI scheme (re.match(r'^[a-z][a-z0-9+\-.]*://')) → return target as-is.
2. If Path(target).exists() → return target as-is.
3. Enumerate .lnk files under %ProgramData% + %APPDATA% Start Menu\Programs (recursive glob). Cache at first call; reuse for daemon lifetime.
4. Enumerate shell:AppsFolder entries (COM: Shell.Application.NameSpace("shell:AppsFolder").Items()). Cache too.
5. Union candidates = [(display_name, launch_token), ...] where launch_token is the .lnk path or shell:AppsFolder\<AUMID>.
6. score = WRatio(target, display_name) — highest wins above threshold.
7. Raise OpenResolveError(top-3) if no candidate clears open_fuzzy_threshold.
```

Cache invalidation: daemon restart. Reload on SIGHUP is a v2 feature.

### Logging

- DEBUG per call: `resolve_window target={!r} top3=[(hwnd=..., proc=..., title=..., score=87), ...] picked=hwnd=...`.
- INFO on failure only (not on every success — too noisy).

### Gotcha reserve

`OpenProcess` with `PROCESS_QUERY_LIMITED_INFORMATION` (0x1000) is sufficient for `GetModuleBaseName` on Win10+ and works without admin for most processes. Some system processes (csrss, smss) still deny — treat as empty proc_name and continue, do not raise. If unexpected permission issues appear during testing, document in `docs/gotchas.md` §21.

## LLM router changes

### System prompt

Fixed prose + three few-shot examples. `{default_browser}` placeholder substituted at `LLMRouter.__init__` time from `config.default_browser`. Prompt text lives as a module-level constant in `llm_router.py` (not inline in `__init__`) and is covered by a golden-file test so changes force review.

```
You are a Windows voice command executor. Spoken commands reach
you as transcripts; pick the tools that carry out the intent.

Chain multiple tool calls proactively when the command is multi-step
(browser search, copy across apps, open-then-type). Single-step
commands use one tool — don't pad.

Rules
- Prefer the user's default browser ('{default_browser}') for web
  or search intents.
- Emit tool calls in strict execution order. The dispatcher runs
  them linearly and cannot replan.
- Call `no_match(reason)` only when the utterance is not an
  executable command (casual speech, nonsense).

Examples

User: "search how to lose weight"
Tools: focus(target="{default_browser}"),
       press(combo="ctrl+t"),
       press(combo="ctrl+l"),
       type(text="how to lose weight"),
       press(combo="enter")

User: "copy that and paste it in notepad"
Tools: press(combo="ctrl+c"),
       focus(target="notepad"),
       press(combo="ctrl+v")

User: "open spotify"
Tools: open(target="spotify")
```

Approximate token budget: ~180 prose + ~220 examples + ~280 tools array = ~680 prefill tokens. Lower than the hybrid build (≈957) because the verb catalog is smaller.

### Warmup

Unchanged mechanism (POST `/v1/chat/completions` with `tool_choice="none"`, `max_tokens=1`). Seeds the new prompt + tools array into LM Studio's prefix KV cache. Runs once on startup iff `[llm].warmup_on_startup = true` (default).

### Timeout

`[llm].timeout_ms = 1200` (raised from 600 in hybrid to absorb the larger warm-call budget and leave room for cold-cache recovery). `[llm].warmup_timeout_ms = 5000` unchanged.

### Plan cap

`[llm].max_plan_steps = 12`. Silently truncates the response if the model emits more (defensive — current prompt and examples never require more than 5).

## Config surface

Rename section `[llm_router]` → `[llm]`. Drop `enabled`. Final committed `config.toml`:

```toml
[llm]
endpoint_url = "http://localhost:1234/v1"
model_id = "google/gemma-4-e4b"
default_browser = "chrome"
timeout_ms = 1200
warmup_timeout_ms = 5000
max_plan_steps = 12
warmup_on_startup = true
focus_fuzzy_threshold = 70
open_fuzzy_threshold = 70
```

Delete `[matching]` section entirely. Remove `enabled` from all docs.

### Resolution order (per field)

1. Environment variable (`VC_LLM_ENDPOINT_URL`, `VC_LLM_MODEL_ID`, `VC_LLM_DEFAULT_BROWSER`, etc. — prefix `VC_LLM_` + upper-case field name).
2. `config.local.toml` (gitignored, per-machine).
3. `config.toml` (committed).

### Startup log format

Every field logged at INFO during `daemon` boot:

```
config: llm.endpoint_url      = http://localhost:1234/v1       (source: config.toml)
config: llm.model_id          = google/gemma-4-e4b             (source: config.local.toml)
config: llm.default_browser   = comet                          (source: VC_LLM_DEFAULT_BROWSER env)
config: llm.timeout_ms        = 1200                           (source: config.toml)
config: llm.warmup_timeout_ms = 5000                           (source: config.toml)
config: llm.max_plan_steps    = 12                             (source: config.toml)
config: llm.warmup_on_startup = True                           (source: config.toml)
config: llm.focus_fuzzy_threshold = 70                         (source: config.toml)
config: llm.open_fuzzy_threshold  = 70                         (source: config.toml)
```

Implementation: `config.py` loads each field via a helper that records its source, returns `(value, source)` tuples, and the daemon prints them at startup. Test: `test_config_resolution_logging.py` mocks env + tmp `config.local.toml` and asserts the log line contents.

### User's machine (reference)

`config.local.toml` currently sets device index, mute_key. Append:

```toml
[llm]
default_browser = "comet"
```

Not committed — machine-local.

## Dispatcher + plan execution

`Dispatcher.dispatch()` deleted. `Dispatcher.run_plan()` is the sole entry point.

```python
class Dispatcher:
    def __init__(self, feedback: FeedbackSink) -> None:
        self._feedback = feedback

    def run_plan(
        self, transcript: str, plan: Plan, registry: ToolRegistry
    ) -> None:
        self._feedback.on_plan_start(transcript, len(plan.steps))
        executed = 0
        for i, step in enumerate(plan.steps):
            tool = registry.by_name(step.name)
            if tool is None:
                self._feedback.on_error(
                    f"plan:step{i}:unknown_tool:{step.name}",
                    ValueError(f"Tool {step.name!r} not registered"),
                )
                break
            logger.info(
                "plan step %d/%d: %s(%s)",
                i + 1, len(plan.steps), step.name,
                ", ".join(f"{k}={v!r}" for k, v in step.kwargs.items()),
            )
            try:
                tool.func(**step.kwargs)
            except Exception as e:
                self._feedback.on_error(f"plan:step{i}:{step.name}", e)
                break
            executed += 1
            if tool.settle_ms > 0:
                time.sleep(tool.settle_ms / 1000.0)
        self._feedback.on_plan_complete(transcript, executed)
```

Changes vs current: per-step INFO log with args. Halt-on-exception preserved. `settle_ms` sleep preserved.

### Daemon pipeline worker

```
transcript = transcriber.transcribe(audio)
plan = router.route(transcript)
if plan is None:
    feedback.on_miss(transcript)  # LLM said no_match, timed out, or errored
else:
    dispatcher.run_plan(transcript, plan, registry)
```

No mode branching. No matcher instance.

### Feedback

- `on_match` dead. Delete from `FeedbackSink` protocol + `winsound` sink implementation.
- `on_miss` fires on `plan is None` (LLM no_match / timeout / error / connect fail).
- `on_plan_start` / `on_plan_complete` / `on_error` preserved.
- Per-step chime or plan-abort chime redesign: **deferred**. v1 relies on logs.

## CLI + startup

### Removed

- `--router-mode` flag in `src/voice_commander/__main__.py`.
- `apply_router_mode()` function.
- `.last-router-mode` persistence file (and the `.gitignore` entry for it).
- `start.ps1` router-mode interactive prompt + persistence block.

### Retained

- `--validate` flag.
- Single-instance lock (ADR 0039).
- Tool-catalog validation at boot (zero tools = hard fail, same as today).

## Testing strategy

Four-layer pyramid. Coverage target: ≥90% on new + rewritten modules.

### L1 — unit (no I/O, pytest)

- `test_resolver.py` — 20 tests. Mock `EnumWindows`, `GetWindowThreadProcessId`, `OpenProcess`, `GetModuleBaseName`, `GetWindowText`. Assert top-pick per scorer, threshold miss raises, tie-break deterministic, empty proc_name tolerated. Start Menu glob mocked to a fixture dir, AppsFolder enum mocked, URI / path / app branches distinguished.
- `test_tools_primitives.py` — 25 tests. Each verb. `focus` calls resolver and routes hwnd. `press`/`type`/`click`/`wait` call pyautogui (mocked). `open` dispatches URI → path → app. `close`/`close_window` hotkey and fg-poll (mocked).
- `test_llm_router_prompt.py` — 5 tests. `_build_system_prompt(default_browser="comet")` == golden string. Token-count assertion (±20). Missing config raises. Placeholder not left unresolved.
- `test_llm_router.py` — 15 tests (extend existing). Response parsing: OpenAI tool_calls, malformed JSON, missing tool_calls, no_match-first → None, timeout → None, HTTP 500 → None, max_plan_steps truncation.
- `test_dispatcher_plan.py` — 12 tests. Happy path, halt-on-exception, unknown tool, settle sleep, per-step INFO log assertion, feedback events fired.
- `test_config.py` — ~15 tests (extend existing; replace the `[matching]` and `[llm_router]` cases). `[llm]` parse, missing-field defaults, env-var override per field, `config.local.toml` layering, unknown-field warning preserved.
- `test_config_resolution_logging.py` — 5 tests. Mock env + tmp local.toml, capture caplog, assert every field's source line.

### L2 — integration (real modules, fake LM Studio)

- `test_llm_only_pipeline.py` — 8 tests. `httpx.MockTransport` returns canned `tool_calls`; full transcript → plan → dispatcher.run_plan → pyautogui mocks. Assert call order, args, settle gaps between steps.
- `test_daemon_factory.py` — updated for LLM-only construction. No mode param, matcher not constructed, router always wired.
- `test_resolver_integration.py` — 3 tests, Windows-only. Spawn real Notepad, `resolve_window("notepad")` returns its hwnd. Teardown closes Notepad. Skip on non-Windows CI.

### L3 — live (skipped by default, run with `pytest --live`)

- `test_llm_only_live.py` — 1 marker, 20 sub-cases. Hits real LM Studio at `[llm].endpoint_url`. Utterances cover: single-step (open, press, type-in-current), 2-step (copy+paste), 3-step (focus+open+type), 5-step (search), edge cases (empty → no_match, gibberish → no_match, mixed noise). Pass bar: ≥18/20 produce the expected plan shape (tool sequence + arg keys; exact arg values fuzzy-compared).
- Warmup live test — 1 test. Asserts warmup shaves ≥200 ms off first real call vs no-warmup baseline.

### L4 — manual (human validation, pre-merge gate)

20-utterance live script documented in the PR description. User speaks, reports pass/fail per utterance. Flagship case: "search how to lose weight" producing the full 5-step chain into Comet.

### Test deltas vs current 367-test suite

- Delete: `test_matcher.py`, `test_router_mode.py`, `test_hybrid_pipeline.py`, any matcher-threshold-specific tests (~30 tests).
- Update: `test_config.py`, `test_cli_validate.py`, `test_dispatcher_plan.py`, `test_llm_router.py`, `test_daemon_factory.py`, `test_tools_primitives.py` (~40 tests rewritten).
- Add: `test_resolver.py`, `test_llm_router_prompt.py`, `test_config_resolution_logging.py`, `test_resolver_integration.py`, `test_llm_only_live.py` (~60 tests).

Final suite target: ≈395 passing, 0 failing.

## Documentation plan

### New ADRs

- `docs/decisions/0040-llm-only-default.md` — Accepted. Supersedes 0026, 0027. Full context on why hybrid is retired and LLM-only replaces it.
- `docs/decisions/0041-parameter-fuzzy-matching.md` — Accepted. rapidfuzz repurposed for `resolve_window` + `resolve_app`. `WRatio`, threshold 70, configurable.
- `docs/decisions/0042-verb-catalog-primitives-only.md` — Accepted. 9-verb catalog. Rationale: smallest prefill. Adding a verb requires a new ADR + prefill impact statement.
- `docs/decisions/0043-few-shot-system-prompt.md` — Accepted. 3 few-shot examples, templated `default_browser`, assembly centralized in `LLMRouter._build_system_prompt()`, golden-file tested.

### Superseded ADRs

- `docs/decisions/0026-hybrid-router.md` — header updated: `Status: Superseded by 0040`. Body untouched (historical record).
- `docs/decisions/0027-fuzzy-threshold.md` — header updated: `Status: Superseded by 0040`.

### Updated docs

- `docs/agents/technical-decisions.md` — new "LLM-Only Pipeline" section with 4 rows (one per new ADR). Old "Hybrid Router" / "Fuzzy Threshold" rows retained with `→ superseded by 0040`.
- `docs/architecture.md` — pipeline diagram collapsed. New "Parameter Resolver" subsection. "Dispatcher" rewritten for `run_plan()`-only. "LLM Router" subsection rewritten: system-prompt assembly, default_browser templating, config resolution log.
- `docs/libraries.md` — `rapidfuzz` row rationale rewritten from command matching to parameter resolution.
- `docs/gotchas.md` — reserve §21 for `OpenProcess` permission edge cases. Fill in if hit during implementation.
- `README.md` — replace `[llm_router]` block with `[llm]`; delete "Matching" subsection; delete `--router-mode` subsection; add "Default browser" example with `comet` / `chrome` / `firefox`; add "How it works" one-liner: "Speak → VAD cuts → Whisper transcribes → LLM plans tool chain → Dispatcher executes."
- `CLAUDE.md` — rewrite the "End-state vision" paragraph: LLM-only with arguments is now the shipped default, not post-MVP. Note hybrid retired in favour of primitives + few-shot prompt.

### This spec

Stored at `docs/superpowers/specs/2026-04-21-llm-default-no-rapidfuzz-design.md`. Implementation plan written next by the `superpowers:writing-plans` skill and saved to `docs/superpowers/plans/`.

## Rollout

**Approach 1: Big-bang single PR.** Branch: `feat/llm-default-drop-rapidfuzz`. All deletions + additions in one diff. Supersede ADRs in the same commit range as the code that replaces them. Rebase onto current main before FF merge. The repo has no external users; no deprecation window needed.

Parallelise via Archon sub-agents:

- Agent 1: verb primitives + tests.
- Agent 2: resolver + tests.
- Agent 3: LLM router prompt assembly + tests.
- Agent 4: config resolution + logging + tests.
- Agent 5: dispatcher + daemon wiring + tests (depends on 1+3).
- Agent 6: deletions + docs + ADRs.

Coordinate via shared branch. Final rebase + FF merge mirrors the proven flow from the prior session (5 branches landed clean, same pattern).

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| LM Studio offline at daemon start → no routing possible | Warmup failure already logs WARNING, does not crash. Runtime `route()` returns None on connect error → miss chime. User sees nothing works and starts LM Studio. Behaviour matches hybrid `enabled=true` today. |
| Gemma 4 E4B fails to chain the 5-step search flow reliably | Few-shot prompt includes that exact utterance. L3 live test covers it. If pass rate <90%, swap in stronger model via `[llm].model_id` — config-driven. |
| rapidfuzz resolver picks wrong process when multiple browsers open | Threshold 70 default; tune per L3 feedback. Log top-3 candidates at DEBUG so user can see why a wrong pick happened. |
| Prefill grows and cold-call latency blows timeout_ms=1200 | Timeout already raised from 600. Warmup re-run on a periodic timer is a queued follow-up (handover open item #10). |
| Start Menu / AppsFolder enum is slow on first call, blocks the pipeline | Cache at first call (daemon lifetime). First call runs in warmup, not on hot path. Measure during L2 integration; optimize if >100 ms. |
| Removing `--router-mode` breaks my `start.ps1` workflow | `start.ps1` rewritten in the same PR. |
| `type` shadows Python builtin, confuses readers | Python symbol is `type_text`, registry `name="type"`. Docstring + ADR 0042 spell this out. |

## Success criteria

1. `main` ships with no `matcher.py`, no `[matching]` config, no `--router-mode` flag, no phrase-shortcut sidecars.
2. Full test suite green (≈395 tests).
3. L3 live suite: ≥18/20 utterances produce the expected plan shape against LM Studio + Gemma 4 E4B.
4. L4 human validation (user's 20-utterance script) signed off, including the "search how to lose weight" flagship case working into Comet.
5. Config log at startup shows every `[llm].*` field with a source — no silent defaults.
6. New ADRs 0040–0043 committed. ADRs 0026 + 0027 marked superseded.
7. `README.md`, `docs/architecture.md`, `docs/libraries.md`, `CLAUDE.md`, `docs/agents/technical-decisions.md` updated in the same commit range.

## Open questions (to be resolved during implementation)

- Does `pyautogui.click(button="middle")` work on Windows without extra setup? Verify in L1. Fall back to `pynput` if not.
- `resolve_app` AppsFolder enumeration — confirm the `Shell.Application.NameSpace("shell:AppsFolder")` COM call is safe from the daemon's main thread (vs background thread). Investigate during implementation; if threading-sensitive, enumerate in warmup and cache.
- Golden-file prompt test — commit the golden as a `.txt` fixture or inline as a string constant? Decide during test authoring. Inline is simpler; `.txt` is nicer for diffs.

None of these block the design. All are tactical.
