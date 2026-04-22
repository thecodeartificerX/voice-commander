# ADR 0048: EventBus + SSE as Daemon's Outbound Telemetry Channel

**Date:** 2026-04-21
**Status:** Accepted
**References:** ADR 0020 (embedded FastAPI)

## Context

The daemon needs to broadcast state changes to external consumers (sprite, future dashboards). Previous approach was ad-hoc log scraping.

## Decision

In-daemon `EventBus` with sync `publish()` (callable from any thread) and per-subscriber `asyncio.Queue` for SSE consumers. FastAPI `/events` endpoint streams events as `text/event-stream` with `Last-Event-ID` ring-buffer replay (100 events).

## Consequences

- Any future consumer (web dashboard, analytics) uses the same SSE feed.
- `publish()` is non-blocking — zero impact on the audio pipeline hot path.
- Bounded per-subscriber queues (1024 events) prevent memory leaks from slow consumers.
- `events_dropped` counter is log-only in MVP; future ADR for `/metrics` endpoint.
