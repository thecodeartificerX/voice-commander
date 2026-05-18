# Whisper.cpp Server `/inference` — Parameter Reference

**Vendored:** 2026-05-17
**Source:** `examples/server/server.cpp` in the whisper.cpp repository
**Cited by:** `src/voice_commander/dictation/remote.py`

## Endpoint

```
POST /inference
Content-Type: multipart/form-data
```

## Form fields

| Field | Type | Default | Description |
|---|---|---|---|
| `file` | binary | required | Audio file (WAV, MP3, OGG, …). Voice Commander sends 16 kHz mono 16-bit PCM WAV. |
| `response_format` | string | `"verbose_json"` | Response format. Voice Commander uses `"json"` to skip per-segment timestamps (saves ~1.2 s on long clips). |
| `temperature` | string | `"0.0"` | Decoder temperature. `"0.0"` = greedy decoding. |
| `prompt` | string | `""` | Initial prompt injected into the decoder context. Biases the decoder toward correct spellings of rare words. Capped at **224 tokens** by whisper.cpp; see truncation note below. |
| `carry_initial_prompt` | string | `"false"` | When `"true"`, the `prompt` is re-applied to every 30-second decode window, not only the first. Required for long dictations spanning multiple windows. |

## Token limit for `prompt`

Whisper's tokenizer uses a vocabulary where the average token is ~4 characters.
The hard server-side cap is **224 tokens** (~896 characters). If the string exceeds
this limit whisper.cpp silently truncates it at a token boundary.

Voice Commander's `postprocess.build_prompt` enforces `_PROMPT_CHAR_CAP = 800`
characters by dropping whole trailing words, keeping the prompt safely under the
server cap regardless of the user's vocabulary size.

## Response (response_format=json)

```json
{"text": " transcribed text here"}
```

The `text` field may contain leading/trailing whitespace and segment-boundary
newlines. The dictation path handles this via `LocalAgreement`, which accumulates
confirmed words joined by single spaces — no embedded newlines in the stabilised transcript.

## Response (response_format=verbose_json)

Returns additional `segments` array with per-segment `start`, `end`, `text`, and
token-level data. Voice Commander deliberately avoids this format because the extra
computation adds ~1.2 s on the reference hardware for data the pipeline discards.

## carry_initial_prompt behaviour

Without `carry_initial_prompt=true`, the `prompt` is injected only into the first
30-second decode window. For dictations longer than 30 seconds, subsequent windows
start cold and may revert to incorrect spellings. Setting
`carry_initial_prompt=true` re-injects the prompt at every window boundary.
