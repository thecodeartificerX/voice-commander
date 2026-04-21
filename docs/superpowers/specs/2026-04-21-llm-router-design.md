# LLM Router Design — Hybrid Routing via Local MoE Tool-Calling

**Date:** 2026-04-21
**Status:** Draft (feature branch `feature/llm-router`, default off)
**Supersedes:** none (parallels existing rapidfuzz-only routing on main)

## Summary

Introduce a hybrid command router on a feature branch. The existing rapidfuzz matcher handles high-confidence utterances on the hot path. Utterances that fall below a tightened confidence threshold escalate to an LLM router that calls a local Mixture-of-Experts model (first target: Gemma 4 E4B) via LM Studio's OpenAI-compatible endpoint. The LLM returns a one-shot plan — an ordered list of tool calls with typed arguments — that the dispatcher executes with per-tool settle delays.

This unlocks natural-language composition ("focus the browser, open a new tab, then type hello") while preserving sub-millisecond latency for common commands. Stability is guaranteed by keeping rapidfuzz as the default and the LLM path strictly opt-in via config.

## Goals

1. Natural chained utterances are routed to ordered tool executions without retraining any model.
2. Common single-word commands keep their current ~1 ms routing latency.
3. Tools remain defined in exactly one place per tool — Python signature plus sidecar TOML. Schema, web UI, rapidfuzz phrases, and LLM tool definitions all derive from that single source.
4. Adding, editing, renaming, or deleting a tool is a single operation driven by the `commander` skill. No drift across components.
5. LM Studio being offline, slow, or returning garbage does not crash the daemon; it degrades gracefully to a miss chime.
6. The design is reversible: if the LLM path proves unreliable, the feature branch is discarded and main is untouched.

## Non-goals

- Multi-turn conversational context across utterances. Every router call is stateless.
- Agentic loops (model observes tool results and re-plans). Plans are one-shot.
- Cloud inference. LM Studio is local-only.
- Replacing rapidfuzz. The hybrid design depends on rapidfuzz owning the hot path.
- Wake-word, per-app command sets, or TTS replies. Out of scope.

## Architecture

```
Transcriber ──text──▶ Matcher (rapidfuzz, threshold=95) ──high-conf──▶ Dispatcher.run_tool
                            │
                            └──low-conf──▶ LLMRouter ──Plan──▶ Dispatcher.run_plan
                                             │                     │
                                             └─timeout/err─▶ miss  └──per-step: fn(**args) + sleep(settle_ms)
```

### Changed and new components

| Component | Change |
|---|---|
| `Matcher` | Reuses existing `[matching].threshold` config knob. Recommended value when LLM router is enabled: 95 (up from 85). Below this score, the pipeline escalates to the LLM router. No second threshold is introduced. |
| `LLMRouter` *(new, `src/voice_commander/llm_router.py`)* | Stateless HTTP client for LM Studio `/v1/chat/completions`. Returns `Plan` or `None`. |
| `Plan`, `ToolCall` *(new, dataclasses)* | Ordered list of typed tool invocations with resolved settle times. |
| `Dispatcher.run_plan` *(new method)* | Execute a `Plan` step by step, sleeping `settle_ms` between steps, aborting on error. Existing `dispatch(transcript, match)` stays for the hot path. |
| `ToolEntry` *(extended)* | New fields: `params_schema: dict`, `settle_ms: int = 0`, `llm_only: bool = False`. |
| `tool_schema.py` *(new)* | Reflects a tool function signature into OpenAI tool-calling JSON schema, merging arg descriptions from TOML. |
| `tools/primitives.py` *(new)* | LLM-callable primitives: `wait`, `press_keys`, `type_text`, `focus_window`, `launch`, `no_match`. |
| Startup validator *(new)* | Refuses to start the daemon if tool signatures and TOML drift. Also exposed as `python -m voice_commander --validate` for CI. |
| `commander` skill | Interview and code-gen extended to collect args, settle_ms, and llm_only flag; writes TOML `[tool.args.<name>]` sub-tables. |

### Unchanged

HotkeyController, StreamingRecorder, VADGate, Resampler, Transcriber, FeedbackSink, hot-reload watcher, web UI reflection layer. The web UI surfaces the new fields because it already reads from the registry.

## Routing behavior

### Hot path (rapidfuzz ≥ threshold)

1. Transcriber emits text.
2. `Matcher.match` returns a `MatchResult` with score ≥ 95.
3. Dispatcher invokes the bound zero-arg function directly.
4. Feedback fires `on_match`.

Latency: ~1 ms router plus tool execution. No change from main.

### LLM path (rapidfuzz below threshold)

1. Pipeline calls `LLMRouter.route(transcript)`.
2. Router builds a fresh request body — no history, no prior messages, no session state:
   ```json
   {
     "model": "google/gemma-4-e4b",
     "messages": [
       {"role": "system", "content": "<short static prompt>"},
       {"role": "user",   "content": "<transcript>"}
     ],
     "tools":        [ /* derived from registry */ ],
     "tool_choice":  "required",
     "temperature":  0,
     "stream":       false
   }
   ```
3. POST to `{endpoint_url}/chat/completions` with a 600 ms total timeout (config knob).
4. Parse `choices[0].message.tool_calls` into a `Plan`.
5. If the first step is `no_match(reason)`, short-circuit to miss feedback.
6. Otherwise, hand `Plan` to `Dispatcher.run_plan`.
7. Dispatcher iterates steps: look up tool, call `fn(**args)`, sleep `settle_ms`, advance. Mid-chain exception stops the chain and fires `on_error`.

LM Studio prefix KV cache reuses the unchanging `system + tools` prefix across calls, so steady-state first-token latency stays low. A warmup ping during daemon start ensures the very first user-issued call also hits a warm cache.

### Miss path

- Rapidfuzz below threshold AND LLM returns `None` (timeout, connect error, malformed response) → `feedback.on_miss(transcript, candidates=())`.
- LLM returns a plan whose first step is `no_match(reason)` → `feedback.on_miss(transcript, reason=reason)`.

## Tool contract — single source of truth

**Rule:** one tool change = one place.

Canonical source = Python function with type hints plus sidecar TOML. Everything else is derived.

| Artifact | Source |
|---|---|
| Registry entry | `@tool` decorator (auto-discovery) |
| Rapidfuzz phrases | TOML `phrases` list |
| LLM JSON schema | Python signature type hints, reflected |
| Arg descriptions (LLM prompt) | TOML `[tool.args.<name>].description` |
| `settle_ms` | TOML `[tool].settle_ms` |
| `llm_only` flag | TOML `[tool].llm_only` |
| Web UI display | Registry reflection |
| Tests | One `test_<module>.py` per tools file |

### Canonical example

```python
# src/voice_commander/tools/filesystem.py
@tool
def open_file(name: str, folder: str | None = None) -> None:
    ...
```

```toml
# src/voice_commander/tools/filesystem.toml
[open_file]
phrases = ["open file", "open"]
description = "Open a file by name, optionally from a specific folder."
category = "filesystem"
enabled = true
settle_ms = 200
llm_only = false

[open_file.args.name]
type = "str"
description = "File name to open (without extension)."
required = true

[open_file.args.folder]
type = "str"
description = "Optional subfolder under the user's projects directory."
required = false
default = ""
```

### Supported arg types

`str`, `int`, `float`, `bool`, `typing.Literal[...]`, and `X | None`. Anything else fails startup validation loudly with tool name and param name in the error.

### Startup validation rules

The daemon refuses to start — and `--validate` exits non-zero — if any of the following are true:

1. A `@tool`-decorated function lacks a TOML entry (existing rule).
2. A function signature parameter lacks `[tool.args.<name>].description` in TOML.
3. A TOML `[tool.args.<name>]` sub-table names a parameter absent from the signature.
4. A parameter uses an unsupported type.
5. `settle_ms` is negative or greater than 5000.
6. A tool marked `llm_only = true` also declares `phrases`.
7. Required primitives (`no_match`, `wait`) are missing or not flagged `llm_only`.

This is the QA/Ops lift the user asked for: drift is impossible to ship.

## Primitive toolset

LLM-only tools in `tools/primitives.py`, invisible to rapidfuzz (flagged `llm_only = true`):

| Primitive | Signature | Purpose |
|---|---|---|
| `wait` | `(ms: int)` | LLM-controlled extra pause beyond per-tool settle. |
| `press_keys` | `(combo: str)` | `"ctrl+l"`, `"alt+tab"`, etc. |
| `type_text` | `(text: str)` | Synthesize keystrokes for arbitrary text. |
| `focus_window` | `(title_substring: str)` | Generic window focus by title match. |
| `launch` | `(app: str)` | Shell-launch an executable or URI. |
| `no_match` | `(reason: str)` | Escape hatch under `tool_choice="required"`. |

Existing macros (`copy`, `paste`, `new_tab`, `focus_browser`, etc.) remain, gain `settle_ms` where appropriate, and stay visible to both routers.

Total tool count stays under ~25 to preserve small-MoE accuracy.

## Latency model

| Path | Budget |
|---|---|
| Rapidfuzz hot path | ~1 ms router, dominated by tool execution. |
| LLM path, steady state | 600 ms total timeout, typically much less on a warm 4B MoE. |
| LLM path, cold start | Warmed by daemon startup ping — user never pays cold-start cost. |
| Per-chain step | `settle_ms` per tool (authored empirically) plus any `wait(ms)` the LLM inserts. |

No new threads. Synchronous HTTP on the existing pipeline worker thread is fine because it already runs serialized per utterance.

## Failure modes

| Failure | Behavior |
|---|---|
| LM Studio offline at daemon start | Health ping logs warning; daemon still runs; LLM path timeouts fail to miss. |
| LM Studio timeout mid-session | `LLMRouter.route` returns `None`; miss chime; daemon continues. |
| Malformed JSON in response | Parse error logged; treated as `None`; miss. |
| Model emits plain text despite `tool_choice="required"` | Treated as malformed; miss. Belt-and-suspenders JSON grammar (where backend supports it) reduces occurrence. |
| Plan step references unknown tool | Chain stops; error feedback; logged with tool name. |
| Plan step throws | Chain stops; error feedback; subsequent steps skipped. |
| Unsupported type in a tool sig | Caught by startup validator before daemon runs. |
| Commander skill writes drifted TOML | Validator fails; skill's post-write `--validate` step surfaces it immediately. |

## Config

New section in `config.toml`:

```toml
[llm_router]
enabled           = false
endpoint_url      = "http://localhost:1234/v1"
model_id          = "google/gemma-4-e4b"
timeout_ms        = 600
max_plan_steps    = 8
warmup_on_startup = true
```

The escalation point is the existing `[matching].threshold`. When `llm_router.enabled = true`, users are expected to raise that value (recommended 95) so that only high-confidence utterances take the hot path and the rest escalate.

Defaults on feature branch: `enabled = false`, so cloning the branch does not change behavior until the user opts in.

## Commander skill contract extension

The existing `.claude/skills/commander/` skill is the single authorized author for tool changes. Its interview and code-gen are extended so that every create/edit/rename/delete operation updates the Python sig and the TOML in lockstep.

New interview questions:

1. *"Does this tool take arguments? (e.g., file path, window title)"*
   For each: name, type, description, required?, default.
2. *"Does this tool change window focus, open an app, or paste keystrokes? If yes, how long to wait after? (default 0 ms)"* → maps to `settle_ms`.
3. *"LLM-only primitive, rapidfuzz macro, or both? (default: both)"* → `llm_only` flag.

Code-gen produces:
- Python with full type hints on the signature.
- TOML with `[tool.args.<name>]` sub-tables per param.
- A test asserting (a) import succeeds, (b) sig matches TOML args, (c) one happy-path call with mocked side-effects.

Post-write steps run in order: `pytest tests/unit/test_tools_<module>.py`, then `uv run python -m voice_commander --validate`. Either failing aborts the change and rolls back the files.

## Phased delivery

Each phase ends with a validation gate. No skipping. Detailed breakdown belongs to the implementation plan; this spec fixes phase boundaries.

| Phase | Scope | Gate |
|---|---|---|
| L0 | Research (R1–R8), ADRs 0026–0036 drafted. No code. | Human reads notes + ADRs; greenlight. |
| L1 | Tool schema + validator + `--validate` CLI flag. `ToolEntry` extended. Existing zero-arg tools unaffected. | `--validate` passes on unchanged repo; rapidfuzz still works. |
| L2 | `LLMRouter` + `Dispatcher.run_plan` + `Plan`/`ToolCall`. Mocked unit tests. | Live integration test against real LM Studio with Gemma 4 E4B — one chain plan end-to-end. |
| L3 | Primitives module. Threshold tightened. Hybrid pipeline wiring. Config opt-in. | Voice test: `"copy"` hits hot path; `"open new browser tab then focus search bar then type hello"` hits LLM chain. |
| L4 | Commander skill interview + code-gen + atomic writes + post-write validation. | Dry-run commander produces a valid new tool in one invocation. |
| L5 | Structured logs, `outputs/last_plan.json` artifact, latency alarms, web UI exposure of new fields. | Soak test: 50 varied utterances; hot/LLM split, miss rate, chain correctness measured. |
| L6 | Merge decision (opt-in `enabled=true` only; default remains false on main). | Human verdict. |

## Research subjects (Phase L0)

Gathered by Haiku sub-agents in parallel, vendored under `docs/references/llm-router/`:

- **R1** — LM Studio OpenAI-compatible endpoint: tool-calling request/response shape, `tool_choice="required"` support, multi tool_call emission, `response_format` JSON schema grammar, prefix KV cache, health endpoint.
- **R2** — Gemma 4 E4B: availability, tool-call template, cold-start vs steady-state latency, compliance under `tool_choice="required"`, max tool count before accuracy drops.
- **R3** — Backup MoE candidates (Qwen3-30B-A3B, Qwen3-Coder-30B-A3B, Phi-3.5-MoE, Granite-3.x-MoE). Selection matrix.
- **R4** — `httpx` sync client: timeouts, keepalive, error taxonomy.
- **R5** — Python sig → JSON schema: `pydantic.TypeAdapter` vs hand-rolled `inspect` walker.
- **R6** — Windows keystroke synthesis for `press_keys` and `type_text`.
- **R7** — Generic `focus_window` on Windows.
- **R8** — `launch` via `os.startfile` vs `subprocess`.

Deliverable per research item: one markdown file with vendored snippets and conclusions.

## ADR plan

Written during L0, alongside research:

- ADR-0026 — Hybrid routing: rapidfuzz first, LLM on escalation.
- ADR-0027 — Rapidfuzz threshold 85 → 95 when LLM router enabled.
- ADR-0028 — LM Studio OpenAI-compatible endpoint as LLM backend.
- ADR-0029 — Gemma 4 E4B as first router model; selection criteria for alternates.
- ADR-0030 — Stateless per-call LLM requests; prefix KV cache only.
- ADR-0031 — `tool_choice="required"` + `no_match()` escape tool.
- ADR-0032 — One-shot plan execution (not agentic loop).
- ADR-0033 — Per-tool `settle_ms` + explicit `wait(ms)` primitive.
- ADR-0034 — Python sig + TOML sub-tables as single source of truth for tool args.
- ADR-0035 — Commander skill contract extension.
- ADR-0036 — Startup validator for tool drift detection.

## Testing strategy

**Unit (new):**
- `test_tool_schema.py` — signature reflection across every supported type; drift detection.
- `test_llm_router.py` — mocked LM Studio: happy, timeout, connect error, malformed JSON, empty tool_calls, `no_match`, multi-step chain.
- `test_dispatcher_plan.py` — `run_plan` happy path; `time.sleep` patched for settle; mid-chain error stops chain; `no_match` short-circuits.
- `test_tools_primitives.py` — each primitive with mocked Win32 / subprocess.
- `test_validation.py` — every startup-validator rejection condition.

**Unit (extend):** existing `test_tools_*.py` assert signature matches TOML args sub-table for every tool.

**Integration:**
- `test_llm_router_live.py` — skipped unless `LM_STUDIO_URL` env is set; when set, runs against real LM Studio.
- `test_hybrid_pipeline.py` — canned or injected transcript through full pipeline including live LLM fallback.

**CI additions:** `uv run python -m voice_commander --validate` as a CI step on the feature branch. `ruff` and `mypy` already catch bad type hints in tool signatures.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Gemma 4 E4B fails `tool_choice="required"` reliably | R2 verifies before L2. Backup candidates in R3. Design decouples model choice from router code. |
| Small MoE accuracy drops above ~25 tools | Primitive set kept minimal; LLM-only flag hides low-value primitives from schema if needed. |
| LM Studio prefix cache invalidates on minor prompt edits | System prompt is static and short. Tools array is stable per daemon run. Warmup ping after load. |
| Chain race conditions (window not yet focused when next keystroke fires) | Per-tool `settle_ms` plus explicit `wait(ms)` primitive. Authors tune empirically. |
| Drift between Python sig and TOML args | Startup validator blocks it. Commander skill runs `--validate` post-write. |
| LLM hangs on slow backend | 600 ms timeout; httpx cancels; caller fails to miss chime. |
| Commander skill edits leave orphaned TOML sub-tables on rename | Skill performs atomic write of all three files (py, toml, test) with rollback on any failure. |

## Open questions

None at spec-approval time. Any opens raised during research or implementation land in ADR 0026+ as they are decided.

## Success criteria

Branch is merge-eligible (not merged by default) when all of the following hold:

1. Gate L6 passes: hybrid routing demonstrably better than rapidfuzz-only on a 50-utterance soak covering single commands and chained commands.
2. Hot-path latency unchanged vs main.
3. No drift possible: startup validator rejects every contrived mismatch in tests.
4. Commander skill demo: creating, editing, renaming, and deleting a tool with args each takes one invocation and produces valid artifacts across py + toml + test.
5. LM Studio offline does not crash the daemon; miss chime fires; feature flag lets the user disable LLM routing instantly.
