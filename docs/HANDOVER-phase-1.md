# HANDOVER — Phase 1 Pickup

**Read this file completely, internalize it, then delete it (`rm docs/HANDOVER-phase-1.md`) before you do any other work.**

You are picking up the Voice Commander project mid-flight. Phase 0 (Scaffolding & Docs) is complete and validated by the user. Your job is **Phase 1 — Hotkey + Audio Capture**.

---

## 1. What this project is (in 3 sentences)

A Windows-native voice command launcher. User presses Scroll Lock → speaks → presses again → `faster-whisper` transcribes on CUDA → `rapidfuzz` matches the transcript against a registry of `@tool`-decorated Python functions → the matched tool fires (e.g. clipboard copy, focus browser). The MVP ships 14 tools across clipboard, browser, window, and system commands; a local-LLM intent router is a far-future phase.

For the full vision and principles, read `/CLAUDE.md` first. It is durable project context and overrides anything you think you remember.

---

## 2. Where the authoritative docs live

Read these in order if you need context:

| File | Purpose |
|---|---|
| `CLAUDE.md` | Durable project context — read first |
| `docs/superpowers/specs/2026-04-19-voice-commander-design.md` | Full design spec (13 sections) |
| `docs/superpowers/plans/2026-04-19-voice-commander-plan.md` | The implementation plan. **Your Phase 1 tasks live here.** |
| `docs/architecture.md` | Subsystem diagram + the 8 component contracts with type signatures |
| `docs/gotchas.md` | Windows/CUDA/threading traps. Read before Phase 1 T01 and T02. |
| `docs/testing-strategy.md` | Test pyramid + per-phase human validation checklists |
| `docs/references/` | 9 vendored library docs (faster-whisper, rapidfuzz, sounddevice, pynput, etc.). Consult before using an API. |
| `docs/decisions/` | 11 ADRs explaining why every library/approach was chosen |

When you implement a task, the plan file has the exact code. Don't improvise — copy the code verbatim unless there's a real reason to deviate, and if you deviate, report the deviation in your final summary.

---

## 3. What Phase 0 produced (what's already in the repo)

- `pyproject.toml` with all deps pinned, `uv.lock` committed
- `.gitignore`, `.python-version`, directory skeleton
- `config.toml` at project root (spec §5 defaults)
- `src/voice_commander/config.py` — frozen Config dataclass tree with TOML loader + 5 passing tests
- Full `docs/` tree: index, architecture, gotchas, libraries, testing-strategy, 11 ADRs, 9 reference docs
- `CLAUDE.md`, `README.md`

Verify on arrival:

```bash
cd F:/Tools/Projects/voice-commander
git log --oneline | head -20          # should show 15+ Phase-0 commits ending at efb65c7
git status                             # should be clean
uv sync --all-groups                   # should exit 0
uv run python -c "import faster_whisper, rapidfuzz, pynput, sounddevice, windows_toasts"  # should exit 0
uv run pytest -v                       # should report 5 passed
```

If any of those fail, **stop and report** — do not try to "fix" Phase 0 work. Ping the user.

---

## 4. Known Phase 0 deviations (context for your work)

1. **Python version:** the plan uses PEP 695 generic syntax (`def foo[T](...)`) in a few places. This requires Python 3.12+, but the project pins **Python 3.11**. The Config loader (T14) was adapted to use `TypeVar("T") + get_type_hints()` instead. **Watch for similar PEP 695 usage in Phase 1 code samples and adapt the same way.**
2. **Reference docs — `⚠️ verify` flags:** 17 flags raised across reference docs during T12. Most are in `windows-toasts` (Phase 3 concern) and `pyautogui`/`winsound` edge cases. None affect Phase 1. Skim the flags if you're curious, but don't block on them.

---

## 5. Phase 1 scope

**Epic goal from the plan:** Press Scroll Lock → start chime → record mic → press Scroll Lock → stop chime → WAV in `outputs/`. Playback verifies. No transcription, no matching, no tools yet — those are Phase 2 and 3.

Four implementation tasks + one human validation gate. Run them **sequentially** (each depends on the previous one per Kaizen dependency edges):

| Kaizen UUID | Task | One-liner |
|---|---|---|
| `1ff35f4d-7fca-408e-93aa-23f5315396d0` | **VC-P1-T01** | Implement `HotkeyController` (pynput Scroll Lock listener) |
| `e6cb2c60-aa85-4ab9-8a6b-d5ec0bcf3fe0` | **VC-P1-T02** | Implement `Recorder` (sounddevice → WAV + retention policy) |
| `b38fb7e3-52aa-4898-b5e5-f2c857910197` | **VC-P1-T03** | Implement `FeedbackSink` — chimes only (no toasts yet; toasts are Phase 3) |
| `55b980b0-31ba-4b87-a9b8-53094742d51d` | **VC-P1-T04** | Wire minimal `Phase1Daemon` (hotkey → recorder → chimes) + `__main__.py` |
| `d9403561-f682-43ac-946c-20534176a41f` | **VC-P1-GATE** | **HUMAN VALIDATION — do not execute.** Belongs to `sakib`. Stop and hand back after T04. |

**Phase 1 Quest UUID** (use for listing tasks): `438c05fc-0e08-4a85-9898-2728137d6587`

The full task content — files to create, exact test code, exact implementation code, commit messages, acceptance criteria — is in **`docs/superpowers/plans/2026-04-19-voice-commander-plan.md`** under the sections `### VC-P1-T01:` through `### VC-P1-T04:` and `### VC-P1-GATE:`.

---

## 6. How to execute a single task (the pattern used successfully in Phase 0)

For each task in order, dispatch a **fresh Sonnet sub-agent** via the `Agent` tool (`subagent_type: general-purpose`, `model: sonnet`). Don't do the coding yourself in the main context — your context is precious; use it for orchestration only.

### Sub-agent prompt template

Fill in `<TASK_ID>` and `<TASK_TITLE>`:

> You are implementing a single Kaizen subquest end-to-end: **VC-P1-`<TASK_ID>` — `<TASK_TITLE>`**.
>
> **Plan:** `F:\Tools\Projects\voice-commander\docs\superpowers\plans\2026-04-19-voice-commander-plan.md` — search `### VC-P1-<TASK_ID>:` and execute the steps verbatim (TDD order: failing test → verify fail → implement → verify pass → commit).
>
> **Project root:** `F:\Tools\Projects\voice-commander\` — forward slashes in bash; shell is bash-on-Windows.
>
> **Python:** 3.11. If the plan's code uses PEP 695 generic syntax (`def foo[T](...)` or `class Foo[T]`), adapt to `TypeVar("T")` + `get_type_hints()` instead.
>
> **Kaizen flow:**
> 1. Find the subquest UUID: the Phase 1 quest is `438c05fc-0e08-4a85-9898-2728137d6587`. Run `kaizenos tasks list --quest 438c05fc-0e08-4a85-9898-2728137d6587 --json`, match `VC-P1-<TASK_ID>` prefix, capture `id`. (Or use the UUID directly: `<paste UUID from table above>`.)
> 2. Mark in_progress: `kaizenos tasks update <id> --status in_progress --json`.
> 3. Do the work from the plan.
> 4. Verify acceptance criteria — run the exact pytest commands from the plan's "Acceptance" section.
> 5. Only if tests pass: `kaizenos tasks done <id> --json`. If tests fail, leave as `in_progress` and report the failure.
>
> **Git commit contention** (for tasks running concurrently — not an issue if you're the only active agent):
> ```bash
> for i in 1 2 3 4 5 6 7 8 9 10; do
>   git add <files> && git commit -m "<msg>" && break
>   sleep $((RANDOM % 3 + 1))
> done
> ```
>
> **Report back (≤120 words):** Kaizen UUID, status transitions, commit SHA, pytest summary (e.g. "N passed in 0.Ns"), any deviations and why.

### Phase 1 serialization

Unlike Phase 0, Phase 1 tasks build on each other — **do not parallelize them**. Run T01, then T02, then T03, then T04. Each subsequent task may import or depend on the previous one.

After T04 is done: **STOP**. Do not touch VC-P1-GATE. Present the human validation checklist from the plan's `### VC-P1-GATE:` section to the user and wait for their sign-off.

---

## 7. Kaizen CLI refresher

```bash
# Health check
kaizenos doctor --json

# List Phase 1 tasks
kaizenos tasks list --quest 438c05fc-0e08-4a85-9898-2728137d6587 --json

# Start a task
kaizenos tasks update <subquest_id> --status in_progress --json

# Complete a task
kaizenos tasks done <subquest_id> --json

# Inspect a single task
kaizenos tasks get <subquest_id> --json
```

Kaizen UUIDs live in Kaizen OS — they don't match the `VC-P1-TXX` IDs directly. Always look up by title prefix or use the table above.

---

## 8. Test execution

```bash
cd F:/Tools/Projects/voice-commander

# Non-hardware tests (CI-safe)
uv run pytest -v -m "not hardware"

# Hardware tests (needs real keyboard / mic / GPU — for VC-P1-T01's Scroll Lock test and general Phase 2+ work)
uv run pytest -v -m hardware

# Single file
uv run pytest tests/unit/test_hotkey.py -v
```

Markers `hardware` and `integration` are already declared in `pyproject.toml` under `[tool.pytest.ini_options]`.

---

## 9. Principles to carry forward (from CLAUDE.md)

- **Sub-agents do the typing.** Orchestrate from your context; don't write code yourself.
- **Test-first.** Every task in the plan is TDD-shaped: write the failing test, verify it fails for the expected reason, implement, verify it passes, commit.
- **Never skip validation.** After T04 completes, the **human** runs the Phase 1 validation. You do not mark VC-P1-GATE done.
- **Every commit is atomic** (single task, single commit message per the plan).
- **Don't invent library APIs.** Check `docs/references/<library>.md` first. If unsure, use `WebFetch` to verify against the real docs.
- **On failure: stop and report.** Do not bypass safety checks, do not `--no-verify`, do not guess.

---

## 10. What "done" looks like for your session

After you finish, the repo should have:

- 4 new commits (one per VC-P1-T01..T04), each following the plan's commit message
- New files:
  - `src/voice_commander/hotkey.py` + `tests/unit/test_hotkey.py`
  - `src/voice_commander/recorder.py` + `tests/unit/test_recorder.py`
  - `src/voice_commander/feedback.py` + `tests/unit/test_feedback.py`
  - `src/voice_commander/daemon.py` + `src/voice_commander/__main__.py` + `tests/unit/test_daemon_phase1.py`
  - `assets/sounds/start.wav`, `stop.wav`, `miss.wav` (generated by the T03 snippet — sine-wave placeholders until Sakib supplies real ones)
- All unit tests green via `uv run pytest -m "not hardware"`
- 4 Kaizen subquests marked `done` (VC-P1-T01..T04)
- VC-P1-GATE still in `todo` status, waiting for the human

Then hand back to the user with the validation checklist from `### VC-P1-GATE:` in the plan. Example phrasing: "Phase 1 implementation complete — 4/4 tasks shipped, N/N unit tests green, Kaizen updated. Please validate end-to-end with the following checklist, and when satisfied mark VC-P1-GATE done in Kaizen to unlock Phase 2."

---

## 11. First thing to do when you start

1. Confirm environment is clean (the commands in Section 3).
2. Read the plan sections `### VC-P1-T01:` through `### VC-P1-GATE:` once, end to end. It's ~600 lines of plan content.
3. Skim `docs/gotchas.md` sections on: Scroll Lock LED, PortAudio device indices, pynput callback threading.
4. **Delete this handover file**: `rm docs/HANDOVER-phase-1.md` and commit with message `chore: remove phase-1 handover (consumed)`. You don't need it in the repo once you've read it.
5. Dispatch the VC-P1-T01 sub-agent.

Good luck. Ship it clean. 🎙️
