# ADR 0009: Phased Delivery with Human-in-the-Loop Validation Gates

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander integrates several independently risky subsystems: a global keyboard hook (pynput), a live audio capture stream (sounddevice), a CUDA-accelerated speech recogniser (faster-whisper), fuzzy command matching (rapidfuzz), OS automation dispatch (pyautogui), and a system-tray UI (pystray). Each subsystem depends on hardware (GPU, microphone, display), platform-specific behaviour (Windows hotkey capture, DirectX audio device enumeration), or external services that cannot be fully exercised in a unit-test environment.

A big-bang delivery approach — building everything before any human verification — would bury integration failures inside a fully assembled system, making diagnosis expensive and rework scope unpredictable. Automated tests can verify logic, but they cannot substitute for a human confirming that recording starts and stops on real hardware, that the Whisper model actually loads on the real CUDA device, or that toasts appear on the real display.

At the same time, pure waterfall (build one layer at a time, no code until the design is frozen) discards the feedback that only a running system can provide. The project needs incremental, verifiable forward motion.

## Decision

Deliver the project in **six phases (Phase 0–5)**, each ending in a documented human-validation gate before the next phase begins. Each gate is a named milestone (e.g. `VC-P0-GATE`) that the maintainer owns. No phase is considered complete until the maintainer personally checks off every item on that phase's checklist in `docs/testing-strategy.md` and signs off the gate.

The six phases and their scope are:

| Phase | Deliverables | Gate task |
|-------|-------------|-----------|
| 0 — Scaffolding & Docs | `pyproject.toml`, directory skeleton, all documentation, ADRs, `config.toml`, `Config` loader | `VC-P0-GATE` |
| 1 — Hotkey & Audio Capture | pynput listener, sounddevice capture, WAV file writer, Phase1Daemon | `VC-P1-GATE` |
| 2 — Transcription | faster-whisper integration, worker thread, transcript log | `VC-P2-GATE` |
| 3 — Router, Registry & First Tool | `@tool` decorator, FuzzyRouter, one working tool, Phase3Daemon | `VC-P3-GATE` |
| 4 — Full MVP Toolset | All planned tools, toast feedback, miss sound, full daemon | `VC-P4-GATE` |
| 5 — Hardening | ≥ 80 % test coverage, 24 h soak test, tray icon, packaging | `VC-P5-GATE` |

Gate tasks hold the state that makes phased delivery auditable: each gate's completion status is the single source of truth for whether a phase has been signed off. AI agents that build within a phase do the implementation work; gate tasks must be signed off by the human maintainer and cannot be auto-completed by an agent.

## Consequences

### Positive
- Integration failures surface at the earliest possible phase, when the codebase is still small and the blast radius is limited.
- Each completed gate gives a known-good checkpoint; regressions are immediately visible in the next phase.
- Human sanity-checks on real hardware catch environment issues (missing CUDA driver, wrong audio device, UAC prompts) that unit tests cannot detect.
- Gate task states provide a persistent audit trail of project progress that survives context resets.
- Smaller per-phase scope lowers the cognitive load of each AI-agent work session and reduces the chance of diverging from the spec.

### Negative
- Phases cannot be parallelised; a blocker in one phase stalls all subsequent work.
- Human gate reviews add calendar friction: if the maintainer is unavailable, the project sits idle even if all automated checks pass.
- Six distinct integration milestones require maintaining six incrementally functional daemon entry points, which adds some scaffolding code that is later superseded.

### Neutral
- The phase structure mirrors the dependency graph of the subsystems (you cannot test transcription without working audio capture), so the sequence is not an arbitrary constraint — it is the natural build order.
- The gate checklist in `docs/testing-strategy.md` is the authoritative source; this ADR records the decision rationale, not the checklist items themselves.

## Alternatives considered

### Continuous delivery (no gates)
Ship features as they are implemented and rely solely on automated tests and code review for quality assurance. Rejected because automated tests cannot exercise the real hardware integration points (CUDA, microphone, global hotkey capture on Windows). A defect introduced in Phase 1 could remain latent until Phase 4 discovery, at which point the cause would be difficult to isolate.

### Waterfall (complete design freeze before any code)
Defer all implementation until the design is fully specified and signed off in one batch. Rejected because the design cannot anticipate all hardware and library behaviour; iterative feedback from a running Phase 1 daemon has already informed the audio buffer sizing and threading decisions. Waterfall removes this feedback loop.

### Automated integration gates (no human review)
Replace the human gate with a fully automated integration test suite that runs on real hardware in CI. Rejected as impractical for this project's scale: setting up a GPU-equipped CI runner, attaching a physical microphone, and scripting toast acceptance is a larger infrastructure investment than the project itself. Human gate review is the right tool at this scale.

## References

- Phase checklists: [../testing-strategy.md](../testing-strategy.md) §3
