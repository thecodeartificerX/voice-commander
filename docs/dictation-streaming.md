# Streaming Dictation (Experimental)

An isolated experiment in streaming dictation, separate from the shipped batch
dictation (`docs/transcription-pipeline.md`). Run standalone:

    python -m voice_commander.dictation_stream

Press Right Ctrl to start dictating, press it again to finish. The transcript
is pasted at the cursor.

## Flow

```
mic -> MicCapture -> raw_q -> Chunker (VADGate: silence | 15s cap)
    -> chunk_q -> bridge.pump -> stream_transcribe (WebSocket)
    -> partials -> LocalAgreement.commit -> TextSink.accumulate
    -> stop: LocalAgreement.finalize -> transform() -> clipboard paste
```

## Modules — `src/voice_commander/dictation_stream/`

| Module | Responsibility |
|--------|----------------|
| `config.py` | `StreamDictationConfig` + `[dictation_stream]` loader |
| `capture.py` | `MicCapture` — sounddevice input stream |
| `chunker.py` | `Chunker` — raw audio → WAV speech chunks (reuses `VADGate`) |
| `ws_client.py` | `stream_transcribe` — WebSocket client |
| `bridge.py` | `pump` — sync→async queue bridge |
| `local_agreement.py` | `LocalAgreement` — word stabiliser |
| `sink.py` | `TextSink` + `transform` seam |
| `session.py` | `StreamSession` — pipeline orchestrator |
| `__main__.py` | Right Ctrl entrypoint |

## Configuration

Set `[dictation_stream] input_device` in `config.toml` to select a non-default
microphone by integer device index or name substring (e.g. `input_device = 4`
or `input_device = "AT2020"`). Omit the key to use the Windows default input
device.

## Status

Experimental — not wired into the daemon. See ADR 0091. If it proves out, a
later decision covers replacing the batch transcription path.
