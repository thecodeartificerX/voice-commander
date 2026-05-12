# ADR 0081 — WASAPI-Only Audio Device Resolution

**Status:** Accepted
**Date:** 2026-05-12
**Supersedes parts of:** initial name-based resolver introduced alongside `[audio].device_name`

## Context

Voice Commander captures microphone audio through `sd.InputStream` (PortAudio). The configured device is stored in `[audio]` as two fields:

- `device` — integer PortAudio index.
- `device_name` — human-readable device name, intended as a stable identity that survives index drift when Windows reshuffles its device list (USB device added/removed, default device changed, driver restart).

`StreamingRecorder._resolve_device_by_name()` re-resolves `device_name` → current index on every `open_session()` and on stream-recovery, with `device` acting as a fast-path hint.

On Windows, PortAudio enumerates **each physical input device once per host API**. For a typical USB microphone the same name appears under:

1. **MME** — legacy, blocking, high-latency.
2. **Windows DirectSound** — legacy DirectX path. Prone to `PaErrorCode -9999` ("Unanticipated host error") and DirectSound-internal errors such as `-2005401480` when the device topology shifts (USB hot-plug, sample-rate change, exclusive-mode acquisition by another process).
3. **Windows WASAPI** — modern native Windows audio path. Far more stable across device-list changes; lower latency; the path Microsoft recommends for capture in 2020+.
4. **Windows WDM-KS** — kernel-streaming. Exclusive-mode only; brittle for shared-use voice capture.

The original resolver matched the first device whose name equalled `device_name`. With a typical Windows device list, the **DirectSound copy enumerates before WASAPI**, so the resolver kept landing on the DirectSound entry. Once Windows changed the device topology, that DirectSound handle started raising:

```
sounddevice.PortAudioError: Error opening InputStream: Unanticipated host error
[PaErrorCode -9999]: 'DirectSound error' [Windows DirectSound error -2005401480]
```

— even though a perfectly healthy WASAPI copy of the same microphone existed at a different index.

## Decision

1. **WASAPI is the only host API the resolver considers.** Filtering: `dev.get("hostapi") == _find_wasapi_hostapi_index() and dev.get("max_input_channels", 0) > 0 and name matches`. Same-named entries under MME, DirectSound, and WDM-KS are skipped, both on the fast path and on the slow scan.

2. **`device_name` is the authoritative identity.** When `device_name` is set in `config.toml`, the resolver is invoked on every `open_session()` and stream-recovery. The saved integer `device` is treated as a non-authoritative cache — used only for fast-path validation, never accepted blindly. If `device` points at a non-WASAPI host API (e.g. the user's previously-saved DirectSound index), the fast path rejects it and the slow scan re-resolves to the WASAPI copy.

3. **`[audio].device` int stays in the schema as a cache.** Backwards compatible: existing configs that only set `device` continue to work (resolver returns it untouched when `device_name` is empty). The web admin form is unchanged for this revision — backend-only fix.

4. **WASAPI absence ⇒ system default.** If `sd.query_hostapis()` doesn't surface a "Windows WASAPI" entry (degenerate Windows install, or a non-Windows test host), the resolver logs a warning and returns `None`, letting PortAudio pick the system default rather than silently picking a less-reliable DirectSound copy.

5. **Resolver exceptions never wipe configuration.** Any unexpected error inside `_resolve_device_by_name` (PortAudio glitch, transient enumeration failure) returns `saved_index` unchanged so a working configuration survives a transient hiccup.

## Consequences

- **`PaErrorCode -9999` from DirectSound disappears for name-resolved devices**, because DirectSound entries are never selected.
- **The on-disk `[audio].device` int can drift freely** without breaking capture — the resolver corrects it on the next session-open.
- **Pure non-Windows test hosts** (CI hypothetically, dev machines without WASAPI) silently fall back to system default, which is the same behaviour as `device_name = ""`.
- **A device that genuinely exists only under MME/DirectSound** (rare — exotic legacy hardware) becomes unreachable via `device_name`. The user can still pin it via the raw `device` int with `device_name = ""`.
- **Web admin UI** still shows a numeric `device` field; replacing it with a dropdown sourced from `/audio/devices` (name + host API) is deferred. Users who want name-based config edit `config.toml` directly until then.

## Implementation pointers

- `src/voice_commander/streaming_recorder.py`
  - `_find_wasapi_hostapi_index()` — caches nothing; `sd.query_hostapis()` is microsecond-cheap.
  - `_resolve_device_by_name()` — fast path checks `hostapi == wasapi_idx` before accepting saved index; slow path scans WASAPI-only.
- `tests/unit/test_streaming_recorder.py` — `test_resolve_by_name_skips_directsound_duplicate`, `test_resolve_fast_path_rejects_directsound_match`, `test_resolve_when_wasapi_missing_returns_none` cover the new semantics.
