# ADR 0028: LM Studio OpenAI-Compatible Endpoint as LLM Backend

**Status:** Accepted
**Date:** 2026-04-21

## Context

The LLM router (ADR 0026) needs a local inference backend that can receive a tools array, enforce `tool_choice="required"`, and return a structured `tool_calls` response. The backend must be reachable over HTTP from the daemon process without any cloud dependency.

Several options exist: Ollama (local runner), a bare vLLM/llama.cpp server, or LM Studio. The project's target user is a Windows developer who already manages local models via a GUI. LM Studio is the most widely adopted local LLM desktop app on Windows (849 K+ Gemma 4 downloads alone — R2 §1), ships with an OpenAI-compatible server, and supports the exact tool-calling interface required.

LM Studio's `/v1/chat/completions` endpoint accepts the OpenAI tool-calling request shape: `tools` array, `tool_choice`, `temperature`, `stream`, and standard `messages`. The response structure is identical to OpenAI's: `choices[0].message.tool_calls` is an array of `{id, type, function: {name, arguments}}` objects (R1 §1–§3). For llama.cpp (GGUF) backends, LM Studio enforces tool JSON schema via grammar-based constraint sampling, giving syntactically valid output even when the model would otherwise emit prose (R1 §2).

R1 §5 notes that prefix KV cache reuse is unreliable in LM Studio as of April 2026 for hybrid-attention models (Qwen3.5, sliding-window). For Gemma 4 E4B (dense, full-attention) the situation is better, but reuse is not guaranteed. The spec accounts for this by keeping the warmup ping and accepting 500 ms–2 s latency on the LLM path.

## Decision

Use LM Studio's OpenAI-compatible endpoint (`http://localhost:1234/v1/chat/completions` by default) as the sole LLM backend. The endpoint URL is configurable via `[llm_router].endpoint_url` in `config.toml`.

`LLMRouter` communicates via a synchronous `httpx.Client` (R4 — ADR for `httpx` is covered in the implementation plan rather than a separate ADR because it is an implementation detail, not an architectural decision). The client is a singleton held for the daemon's lifetime, reusing the TCP keepalive connection across calls (R4 §Connection Pooling).

A warmup ping via `GET {endpoint_url}/models` is issued at daemon startup when `[llm_router].warmup_on_startup = true`. A non-empty `data` array confirms LM Studio is running and a model is loaded (R1 §6). A missing or empty response logs a warning but does not abort the daemon — the LLM path will simply fail to miss on first call.

## Consequences

### Positive

- Zero custom inference setup for the target user. LM Studio is already installed and used for model management.
- The OpenAI wire format is well-documented and tested against multiple models. The same `LLMRouter` code works with any future model loaded in LM Studio.
- Grammar-based constraint sampling (llama.cpp backend) reduces malformed JSON responses.
- The `/v1/models` health endpoint gives a clean startup probe with no side effects.

### Negative

- LM Studio must be running and a model must be loaded for the LLM path to work. The daemon does not start LM Studio automatically.
- Prefix KV cache reuse is unreliable for hybrid-attention models (R1 §5). Steady-state latency for Gemma 4 E4B is 500 ms–2 s per call, not the lower bound that cache reuse would give. This is accepted as a design constraint and documented in the latency budget.
- The warmup ping may return a false positive: LM Studio returns HTTP 200 for invalid paths (R1 §6 caveat). The implementation must parse the response body, not just the status code.

### Neutral

- Changing backends (e.g., switching to a bare Ollama server) requires only updating `endpoint_url` in config, provided the replacement speaks the same OpenAI wire format.

## Alternatives considered

### Ollama HTTP server
Rejected for primary use. Ollama's OpenAI-compatible endpoint is less mature for multi-tool-call responses and does not have grammar-based constraint sampling on Windows. LM Studio has a larger Windows user base and better-documented tool-calling support (R2).

### Direct llama.cpp server (`./server`)
Rejected. Requires the user to manage a CLI server process, quantisation selection, and GPU offload flags manually. Too much operational overhead for the target user. LM Studio wraps all of this.

### vLLM
Rejected for local Windows deployment. vLLM targets Linux GPU servers. No native Windows support; setup complexity is high; contradicts the local-first principle.

## References

- R1 — LM Studio tool-calling endpoint research (full reference doc)
- R4 — httpx sync client patterns
- ADR 0026 — hybrid routing design
