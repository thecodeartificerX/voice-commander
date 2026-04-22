# ADR 0045: Sprite as a Separate Process via SSE

**Date:** 2026-04-21
**Status:** Accepted
**Supersedes:** —
**References:** ADR 0013 (dropped WinRT toasts), ADR 0020 (embedded FastAPI)

## Context

We need visual feedback for daemon state (listening, thinking, success, miss, crashed). The sprite must never steal focus, never block the audio pipeline, and must be crash-isolated from the daemon.

## Decision

The sprite runs as a separate OS process (`voice_sprite`) consuming events from the daemon's FastAPI `/events` SSE endpoint. No direct IPC, shared memory, or in-process rendering.

## Consequences

- A sprite crash cannot take down voice recognition.
- Daemon has zero knowledge of sprite health — fire-and-forget events.
- SSE reconnect with `Last-Event-ID` provides automatic gap recovery.
- Adds ~20 MB memory for the pyglet process.
- Requires the FastAPI web server to be running (it already is by default).
