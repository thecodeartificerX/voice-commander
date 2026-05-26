# Voice Commander — Documentation Index

Read in this order when joining the project:

1. [`/CLAUDE.md`](../CLAUDE.md) — canonical entry point; lazy-references everything below
2. [`agents/`](agents/) — agent-oriented lazy references (repo layout, technical-decisions summary)
3. [`architecture.md`](architecture.md) — subsystem diagram and contracts
4. [`libraries.md`](libraries.md) — every dependency and why
5. [`gotchas.md`](gotchas.md) — Windows traps, threading pitfalls, CUDA DLLs
6. [`testing-strategy.md`](testing-strategy.md) — unit / integration / human-validation
7. [`superpowers/specs/`](superpowers/specs/) — design docs from brainstorming
8. [`superpowers/plans/`](superpowers/plans/) — implementation plans
9. [`decisions/`](decisions/) — ADRs for every locked decision
10. [`references/`](references/) — vendored framework documentation

## Quick links

- LLM router ADRs: 0026–0038 in [`decisions/`](decisions/)
- Node-graph ADRs: 0062–0068 in [`decisions/`](decisions/)
- **Transcription pipeline (end-to-end):** [`transcription-pipeline.md`](transcription-pipeline.md) — both paths (command + dictation) with ASCII flow diagrams, module references, and ADR cross-links
- **Dictation custom vocabulary:** ADR [`decisions/0088-dictation-custom-vocabulary.md`](decisions/0088-dictation-custom-vocabulary.md) + streaming transcription server reference [`references/ws-transcribe-server.md`](references/ws-transcribe-server.md)
- **Streaming dictation (server-side decode):** [`dictation-streaming.md`](dictation-streaming.md) — raw PCM transport, server accumulates and decodes once, ADR [`decisions/0096-server-side-dictation.md`](decisions/0096-server-side-dictation.md) + backend selector (ADR 0102)
- **Named command modes:** [`modes.md`](modes.md) — scoped, voice-switchable command catalogs; `modes/<name>.toml` format, enter/exit lifecycle, action grammar, hot-reload; ADR [`decisions/0100-named-command-modes.md`](decisions/0100-named-command-modes.md)
