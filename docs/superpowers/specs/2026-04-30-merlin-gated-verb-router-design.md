# Merlin-Gated Verb Router Design

**Date:** 2026-04-30
**Status:** Proposed
**Source request:** Replace unconditional LLM routing with a deterministic normal-mode router. Use the LLM only while a session-level "Merlin" mode is active.

## 1. Summary

Voice Commander will stop sending every transcript to `LLMRouter.route()` by default. Instead, the daemon will route utterances through a new deterministic **normal-mode verb router** unless the user has toggled **Merlin mode** on for the current session.

Normal mode is driven by a small explicit verb table:

- fuzzy-match **only the first word** of the transcript to a known verb
- treat the remaining transcript as the **tail**
- let the matched verb decide whether the tail means:
  - use a default action
  - fuzzy-match a subcommand
  - pass raw text through as a primitive argument

Merlin mode is toggled by speaking exactly `Merlin` during an active voice session. While Merlin mode is on, all non-toggle utterances continue to use `LLMRouter.route()` unchanged. Saying `Merlin` again exits Merlin mode.

This design intentionally removes the current command bloat made of one-shortcut aliases (`new_tab`, `copy`, `refresh`, etc.). Those become verb-router behavior instead of top-level user commands.

## 2. Goals

- Remove the default LLM dependency for common commands.
- Preserve a local-LM escape hatch for complex or open-ended requests.
- Make routing deterministic and low-latency for common command shapes.
- Reduce command catalog bloat by collapsing many single-shortcut commands into verb rules.
- Reuse existing execution primitives (`press`, `open`, `focus`, `type`, etc.) instead of creating a new executor.

## 3. Non-goals

- No free-form natural-language understanding in normal mode.
- No whole-phrase fuzzy routing in normal mode.
- No gradual compatibility shim for redundant old commands; they will be removed as part of this change.
- No change to the dispatcher contract or `Plan` execution semantics.

## 4. User-visible behavior

### 4.1 Session-level Merlin mode

- During an active session, speaking exactly `Merlin` toggles Merlin mode on.
- Speaking exactly `Merlin` again toggles Merlin mode off.
- While Merlin mode is off, utterances use the deterministic normal-mode router.
- While Merlin mode is on, utterances use `LLMRouter.route()` exactly as they do today.

The Merlin toggle is session-scoped. Opening a new voice session starts with Merlin mode off.

### 4.2 Normal-mode routing

Given a transcript:

1. Split on the first space.
2. `head` = first word.
3. `tail` = everything after the first word, trimmed.
4. Fuzzy-match `head` against configured verb aliases.
5. Resolve the matched verb using its own rules.

Examples:

- `copy` → standalone default action
- `close` → default close action
- `close window` → `close` subcommand `window`
- `new tab` → `new` subcommand `tab`
- `type hello world` → raw tail `hello world`
- `open spotify` → first try subcommand/alias match inside `open`; otherwise pass raw tail to `open(target=...)`

## 5. Verb model

Each verb entry has:

- `name`
- `aliases`: phrases eligible for first-word fuzzy matching
- `default_action`: optional action when `tail == ""`
- `subcommands`: optional map of subcommand aliases → action
- `raw_tail_fallback`: optional behavior that passes the raw tail through to a primitive/command argument
- `thresholds`: per-verb override hooks if needed later

### 5.1 Verb categories

#### A. Standalone-default verbs
Examples:

- `copy`
- `cut`
- `paste`
- `undo`
- `redo`
- `save`
- `refresh`
- `minimize`
- `maximize`

These usually ignore any tail. If spoken with no tail, they dispatch a fixed action.

#### B. Default + subcommand verbs
Examples:

- `close`
  - default → close tab/document (`ctrl+w`)
  - `window` → close focused window (`alt+f4`)
- `new`
  - `tab` → `ctrl+t`
  - `window` → `ctrl+n`
- optionally `next`, `previous`, `last` if retained in the spoken surface

#### C. Subcommand + raw-tail fallback verbs
Examples:

- `open`
- `focus`
- `type`

These first try subcommand/alias resolution within the verb. If that does not clear the subcommand threshold, they pass the raw tail through.

Examples:

- `open gmail` → explicit alias target if defined
- `open spotfy` → raw tail `spotfy` to existing `open(target)` resolution
- `type hello there` → raw tail to `type(text)`

## 6. Execution model

The verb router does not execute tools directly. It returns a normal `Plan`, preserving the daemon → dispatcher architecture.

The router may target:

- low-level primitives (`press`, `open`, `focus`, `type`, etc.)
- curated commands/workflows that still exist after pruning

This keeps `Dispatcher.run_plan()` unchanged.

## 7. Source of truth

The new routing system uses a hybrid source of truth:

1. **Explicit verb router table** for normal-mode verbs, defaults, subcommands, and tail behavior.
2. **Existing primitives / curated commands / workflows** as execution targets.

This is intentional. Routing knowledge becomes explicit and compact, while execution remains in the existing registry/plan infrastructure.

## 8. Catalog pruning

### 8.1 Keep as execution primitives

Keep these active low-level building blocks:

- `press`
- `open`
- `focus`
- `type`
- `wait`
- `click`
- `scroll`
- `no_match`

Keep `speak` as a system tool. Keep perception tools only if still needed by graphs or internal surfaces.

### 8.2 Delete redundant commands immediately

Delete current command entries whose only value is a single shortcut alias now represented by verb routing, including at least:

- `new_tab`
- `new_window`
- `close_tab`
- `close_window`
- `next_tab`
- `previous_tab`
- `copy`
- `cut`
- `paste`
- `undo`
- `redo`
- `save`
- `refresh`
- `minimize`
- `maximize`
- other equivalent one-shortcut aliases discovered during implementation

### 8.3 Keep only semantic commands/workflows

Retain commands/workflows that still provide distinct value, such as:

- app/site shortcuts like `gmail`, `facebook`
- bespoke named actions that do not fit the verb grammar
- real multi-step workflows

## 9. Matching rules

### 9.1 First-word matching

- Only the first token participates in top-level fuzzy routing.
- This threshold should be high because it selects the routing branch.
- If no verb clears threshold, route to the existing miss path (`on_miss` / `plan_outcome(status="miss")`).

### 9.2 Tail matching

For verbs with subcommands:

- Fuzzy-match the tail within that verb's allowed subcommand aliases.
- This threshold may be slightly lower than the first-word threshold because the candidate set is small and scoped.

### 9.3 Raw-tail fallback

For verbs that allow it:

- if subcommand matching succeeds, use the subcommand action
- otherwise pass the raw tail through unchanged

### 9.4 Missing-tail behavior

- `copy`, `paste`, etc. work with no tail.
- `close` supports a default when no tail is present.
- `open`, `focus`, `type` require a meaningful tail unless a verb-specific explicit subcommand handles the empty case.
- Empty-tail verbs without defaults miss.

## 10. Error handling

Normal-mode failures reuse the current miss path. No new user-facing error mode is introduced.

Miss cases include:

- unknown first word
- known verb but missing required tail
- subcommand tail below threshold and no raw-tail fallback
- verb-specific validation failure

The daemon continues to publish the existing miss outcome and play existing miss feedback.

## 11. Daemon integration

`StreamingDaemon._process_utterance()` keeps the current transcription and confidence gates. The routing section changes from:

- unconditional `LLMRouter.route(result.text)`

to:

- Merlin-toggle check
- if Merlin mode on → `LLMRouter.route(result.text)`
- else → normal-mode verb router

This preserves the existing transcribe → route → dispatch shape while changing only the routing branch.

## 12. Data model and module boundary

Add a dedicated normal-mode router module with a narrow contract, e.g.:

```python
class VerbRouter:
    def route(self, transcript: str) -> Plan | None: ...
```

Supporting data structures should be explicit and testable:

- `VerbRule`
- `SubcommandRule`
- `RouteTarget`

The daemon should not embed the rule table inline. Routing policy belongs in its own module.

## 13. Testing strategy

### 13.1 Unit tests

Test:

- head/tail tokenization
- first-word fuzzy matching
- subcommand fuzzy matching
- default action selection
- raw-tail fallback
- missing-tail miss cases
- Merlin toggle detection
- Merlin-mode on/off branch selection

### 13.2 Integration tests

Test end-to-end `_process_utterance()` behavior with mocked collaborators:

- `copy`
- `close`
- `close window`
- `new tab`
- `type hello world`
- `open spotify`
- `Merlin` toggle on
- Merlin-mode LLM route
- `Merlin` toggle off

Also verify the command catalog no longer contains the removed redundant commands.

## 14. Documentation and decision updates

This work changes a locked architectural decision. It will require:

- a new ADR superseding the current LLM-only routing decision
- updates to `docs/agents/technical-decisions.md`
- architecture docs updated to describe dual routing modes
- user docs updated to explain Merlin mode and the deterministic normal router

## 15. Open implementation notes resolved by this spec

The following design choices are locked by this spec and should not be reopened during implementation:

- Normal mode does **not** whole-phrase fuzzy match.
- Only the **first word** is fuzzy-matched at the top level.
- The **tail** is verb-owned.
- Tail matching inside a verb is fuzzy.
- Verbs may define defaults.
- Verbs may define raw-tail fallback.
- Merlin is a **session-level toggle**, not a transcript prefix.
- Redundant old commands are deleted as part of this change, not migrated gradually.
