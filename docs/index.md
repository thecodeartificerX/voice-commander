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

- Latest spec: [`superpowers/specs/2026-04-19-voice-commander-design.md`](superpowers/specs/2026-04-19-voice-commander-design.md)
- Latest plan: [`superpowers/plans/2026-04-19-voice-commander-plan.md`](superpowers/plans/2026-04-19-voice-commander-plan.md)
- LLM router spec: [`superpowers/specs/2026-04-21-llm-router-design.md`](superpowers/specs/2026-04-21-llm-router-design.md)
- LLM router ADRs: 0026–0038 in [`decisions/`](decisions/)
- Node-graph ADRs: 0062–0068 in [`decisions/`](decisions/)
- **Transcription pipeline (end-to-end):** [`transcription-pipeline.md`](transcription-pipeline.md) — both paths (command + dictation) with ASCII flow diagrams, module references, and ADR cross-links
- **Dictation custom vocabulary:** ADR [`decisions/0088-dictation-custom-vocabulary.md`](decisions/0088-dictation-custom-vocabulary.md) + whisper.cpp server reference [`references/whisper-cpp-server-inference.md`](references/whisper-cpp-server-inference.md)
- **Streaming dictation (experimental):** [`dictation-streaming.md`](dictation-streaming.md) — WebSocket + LocalAgreement experiment, ADR [`decisions/0091-streaming-dictation-experiment.md`](decisions/0091-streaming-dictation-experiment.md)
