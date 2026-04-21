# ADR 0038: LLM Router Warmup via Real Chat-Completion POST

**Status:** Accepted
**Date:** 2026-04-21

## Context

ADR 0028 introduced a startup warmup ping for the LLM router: a `GET /v1/models` request that confirms LM Studio is running and a model is loaded. The intent was to ensure the first real user utterance would find a warm, responsive endpoint.

In production, the warmup ping reported success but the first real voice command still timed out:

```
2026-04-21 14:54:29 INFO  llm_router: warmup ping — GET /v1/models → 200, model loaded: google/gemma-4-e4b
2026-04-21 14:54:29 INFO  llm_router: warmup complete
...
2026-04-21 14:54:50 WARNING llm_router: route() timed out after 600 ms (utterance: "focus browser")
2026-04-21 14:54:50 INFO  dispatcher: miss — no plan returned by LLM router
```

The gap between warmup success (14:54:29) and the first real call timeout (14:54:50) is 21 seconds — the daemon was idle, the user spoke their first command, and the router timed out despite having "warmed up" moments before.

The root cause is that `GET /v1/models` is a pure metadata query. It does not touch the model's inference engine, does not allocate KV cache buffers, and does not perform any computation that would prime the GPU or populate LM Studio's prefix KV cache with the system prompt and tools array. The model is loaded in VRAM, but the first inference call still triggers all first-call overhead: CUDA kernel compilation (on some drivers), KV cache allocation, and the full prompt prefill for the system message + tools array (the stable prefix that ADR 0030 relies on for cache reuse).

R1 §5 explains the mechanics: prefix KV cache reuse in LM Studio applies to the unchanging prefix sent on every call — the system prompt and tools array. For the cache to be populated, an actual `/v1/chat/completions` request carrying those exact tokens must have been processed. A `GET /v1/models` request contributes nothing to the KV cache. The first real call pays the full prefill cost regardless of the GET warmup.

R1 §6 also notes a structural limitation: LM Studio returns HTTP 200 for many paths (including invalid ones), so a 200 response from `GET /v1/models` does not even confirm the inference path is functional — only that the HTTP server is up.

The timeout at 14:54:50 is therefore expected: the per-call `timeout_ms = 600` budget was set assuming a warm KV cache (ADR 0028 §Negative: "500 ms–2 s latency per call"). A cold prefill of the full system prompt + tools array for Gemma 4 E4B takes 4–8 s on the target hardware (RTX 3060). The per-call timeout is correctly tuned for warm calls and correctly too tight for a cold first call.

The fix is to make warmup perform the same computation that the real routing calls will perform: POST to `/v1/chat/completions` with the real system prompt, the real tools array, and `tool_choice = "none"` (or `"auto"` as fallback if the model does not support `"none"`), with `max_tokens = 1` to terminate generation immediately. This forces LM Studio to prefill the system prompt + tools prefix, populate the KV cache, and compile any CUDA kernels needed for inference — so the first real call benefits from a warm cache and completes within the per-call timeout.

## Decision

`LLMRouter.warmup()` is replaced with an implementation that:

1. Builds the same `messages` array that `route()` would send — the static system message with the routing instructions, and a dummy user message (`"warmup"` or equivalent short string).
2. Builds the same `tools` array from the registry that `route()` would send.
3. POSTs to `/v1/chat/completions` with `tool_choice = "none"` and `max_tokens = 1`. `tool_choice = "none"` instructs the model not to emit a tool call, preventing any downstream tool dispatch logic from being triggered. If LM Studio returns a 4xx error for `"none"` (some backends do not support it), the implementation falls back to `tool_choice = "auto"` and discards the response regardless.
4. Uses a dedicated `warmup_timeout_ms` field on `LLMRouterConfig` (default 5000 ms) rather than the per-call `timeout_ms` (default 600 ms). The warmup budget must accommodate the cold-prefill cost; the per-call budget need not.
5. Logs the outcome at INFO level: `warmup complete — /v1/chat/completions responded in {elapsed_ms} ms` or `warmup failed — first real call may be slow`.
6. On timeout or HTTP error, logs a warning and continues. Warmup failure does not abort the daemon.

`LLMRouterConfig` gains:

```toml
[llm_router]
warmup_timeout_ms = 5000  # budget for the startup POST; separate from per-call timeout_ms
```

The `warmup_on_startup = true` flag is unchanged. Setting it to `false` skips the new POST warmup entirely, reverting to no warmup (appropriate for environments where startup latency matters more than first-call latency, e.g. integration test harnesses).

## Consequences

### Positive

- The first real user utterance after daemon startup now lands on a warm KV cache. The 4–8 s first-call cold prefill is moved to startup (once, amortised over the session) rather than occurring on the first voice command.
- The dedicated `warmup_timeout_ms = 5000` budget is calibrated for cold-prefill cost without inflating `timeout_ms`, which must remain tight (600 ms) for real interactive calls.
- Warmup genuinely confirms that the full inference path is functional (CUDA kernels, KV cache allocation, model forward pass), not just that the HTTP server is reachable.
- `tool_choice = "none"` with `max_tokens = 1` minimises the token output during warmup. The cost is purely the prompt prefill; no generation tokens are produced.

### Negative

- Daemon startup now takes approximately 5 s longer (the warmup POST) when `warmup_on_startup = true` and `llm_router.enabled = true`. This is a one-time cost per daemon run. For users who start the daemon at login and leave it running, the cost is invisible. For users who start/stop the daemon frequently, it is noticeable.
- The warmup POST sends the system prompt and tools array to LM Studio. This is the same data sent on every real call; there is no additional information disclosed. However, it does mean that `tools` must be fully built at startup before `warmup()` is called. This is already required by ADR 0036 (the startup validator runs before the daemon arms the hotkey).
- If LM Studio unloads or swaps the model after warmup (e.g., the user changes models in the LM Studio UI), the KV cache is invalidated and the first subsequent call will be cold again. There is no re-warmup mechanism in this ADR. Periodic re-warmup is a future option.

### Neutral

- The `warmup_timeout_ms` / `timeout_ms` split makes it explicit that warmup and runtime have different latency contracts. Previous callers that read `timeout_ms` for warmup purposes will need updating, but there were none — the old GET warmup did not use `timeout_ms` at all.
- Tests that mock `LLMRouter.warmup()` are unaffected by the implementation change; the mock still returns immediately. Tests that test warmup behaviour directly must be updated to expect a POST to `/v1/chat/completions` rather than a GET to `/v1/models`.

## Alternatives considered

### Longer per-call `timeout_ms` (e.g., 8000 ms)
Raising the per-call timeout papers over the cold-first-call symptom but does not fix it. A user speaking a voice command would wait 4–8 s for the first response. Voice UX requires sub-second feedback or a clear indication that something is processing. Silent 8 s wait is worse than a miss chime and retry. Rejected.

### Periodic re-warmup on a background timer
Useful if LM Studio is frequently swapping models. Not needed for the current use case (single model loaded for the session) and adds concurrency complexity (background timer making HTTP calls while the pipeline worker thread also makes calls). Deferred to Phase 6+ if field metrics show re-warmup is needed.

### Fire-and-forget warmup (do not wait for completion)
Would not move the cold-prefill cost to startup. The first real call would still pay the prefill cost because the warmup POST might still be in-flight or might complete just before the first real call. The benefit of deterministic warmup (first call is guaranteed warm) disappears. Rejected.

### `GET /v1/models` + sleep (current approach, with a delay)
Adding an arbitrary sleep after the GET warmup does not cause KV cache prefill. It just delays the daemon's readiness. Rejected.

## Also see

- ADR 0028 — original LM Studio endpoint decision; superseded warmup design (`GET /v1/models`).
- ADR 0030 — stateless per-call requests; the system prompt + tools prefix that warmup now pre-populates.
- ADR 0026 — hybrid routing; the LLM path whose first-call latency this ADR improves.
- ADR 0033 — `settle_ms`; the timing mechanism for post-tool delays (separate concern).

## References

- R1 §5 — LM Studio prefix KV cache mechanics; why GET warmup does not seed the cache
- R1 §6 — `/v1/models` endpoint; caveat that 200 does not confirm inference path health
- R1 §1 — `/v1/chat/completions` request shape; `tool_choice`, `max_tokens` parameters
- ADR 0028 — LM Studio endpoint; original warmup rationale (superseded by this ADR)
- ADR 0030 — stateless requests; stable prefix structure that warmup pre-populates
- ADR 0026 — hybrid routing; motivates the latency requirement warmup must meet
