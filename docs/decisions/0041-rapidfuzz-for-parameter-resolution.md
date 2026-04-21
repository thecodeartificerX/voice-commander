# ADR 0041: Keep rapidfuzz — Scoped to Parameter Resolution

**Status:** Accepted
**Date:** 2026-04-21
**Supersedes (scope only):** ADR 0005 — the *command-matching* use of `rapidfuzz` is retired; the *dependency itself is kept* for a narrower purpose.

## Context

ADR 0040 replaced the hybrid router with LLM-only routing. `matcher.py` was deleted and `rapidfuzz` is no longer called from any routing code path.

An earlier revision of this ADR proposed dropping `rapidfuzz` from `pyproject.toml` entirely. Implementation revealed that the dependency is still needed — not for routing utterances to tools, but for resolving tool **arguments**. The `focus(target)` and `open(target)` verbs receive a free-form string from the LLM (process name, window title fragment, app display name, or Start-Menu entry) and must map it onto a concrete `hwnd` / launch token. A substring check is not good enough: "spotify" must match "Spotify Premium" in the AppsFolder catalog; "chrome" must match a Chrome window whose title is "Inbox — Gmail — Google Chrome".

## Decision

Keep `rapidfuzz` in `pyproject.toml`, but scope its use to parameter resolution only:

1. **`resolver.resolve_window(target)`** — enumerates visible, titled windows and scores each candidate as `max(WRatio(target, proc_name), WRatio(target, title))`. The top-scoring hwnd above the configured `focus_fuzzy_threshold` wins. Below threshold → `FocusWindowError` carrying the top-3 `(proc_name, title, score)` tuples.
2. **`resolver.resolve_app(target)`** — after URI / file-path branches bail out, scores `target` against the cached `(display_name, launch_token)` list (Start Menu `.lnk` stems plus `shell:AppsFolder` display names) with `WRatio`. Picks argmax above `open_fuzzy_threshold`. Below threshold → `OpenResolveError` with top-3 candidates.
3. **`primitives._verify_open(target)`** — optional post-launch verification poll. Uses `WRatio(target, window_title)` ≥ 60 to confirm that the app actually opened a window. Best-effort: timeout logs a warning, never raises.

Thresholds default to **70** for both resolvers. They are configurable via `[llm].focus_fuzzy_threshold` and `[llm].open_fuzzy_threshold` in `config.toml`. Configuration is injected once at daemon startup via `resolver._set_config(cfg.llm)` — see ADR 0042.

No rapidfuzz call remains on the routing hot path. Every call is a one-shot scoring pass over a small list (visible windows: typically <50; AppsFolder: ~200 entries), well under 1 ms on commodity hardware.

## Consequences

### Positive

- Tool arguments are robust to fuzzy user speech and transcription artefacts ("fire fox" → Firefox; "vs code" → Visual Studio Code).
- The LLM does not need to know exact window titles or AppsFolder IDs — it emits a human-style `target` and the resolver grounds it.
- Thresholds are a single knob per verb; default 70 was validated against a 20-utterance live-test script.
- `rapidfuzz` is still a native-wheel dependency, but it was already vendored for the old matcher; keeping it is zero new install pain.

### Negative

- Two C++ dependencies (`rapidfuzz` + its transitive `Levenshtein`) remain in the install footprint. Install-time cost: ~6 MB.
- The resolver fails closed: an unfamiliar app name below threshold raises `OpenResolveError` rather than guessing. Users occasionally see a miss chime for apps whose display name differs from the spoken form; the fix is to lower the per-user threshold or add a new `.lnk` to the Start Menu.

### Neutral

- The `@tool` decorator no longer accepts `phrases=`. Phrase lists in sidecar TOMLs are `phrases = []` by convention; the field exists only for backward-compatible TOML round-tripping (ADR 0040).

## Alternatives considered

### Drop rapidfuzz, use a local Levenshtein implementation
Rejected. `rapidfuzz`'s `WRatio` combines several algorithms (`partial_ratio`, `token_sort_ratio`, `ratio`) in a way that handles acronyms, word-order reshuffles, and partial overlaps much better than plain Levenshtein. Reimplementing `WRatio` correctly is more code than the dependency costs.

### Delegate resolution to the LLM
Rejected. The LLM does not know which windows are open on the user's desktop or which `.lnk` files live in the Start Menu. Even if it did, a round-trip to LM Studio per argument would inflate the per-utterance latency budget past the 1.5 s hard ceiling.

### Expose thresholds per-tool rather than per-verb-class
Deferred. The current `[llm].focus_fuzzy_threshold` / `open_fuzzy_threshold` pair covers the only two verbs with fuzzy parameters. If a future verb needs its own threshold, add a new `[llm].<verb>_fuzzy_threshold` field at that time.

## Related

- ADR 0040 — LLM-only routing (removed the former *routing* use of rapidfuzz).
- ADR 0042 — Resolver module design (the consumer of rapidfuzz inside the daemon).
- ADR 0043 — Nine-verb primitive catalog (the verbs whose parameters are fuzzy-resolved).
- ADR 0005 — original rapidfuzz matching decision; the *matching* use is superseded by ADR 0040 but the dependency is preserved by this ADR.
