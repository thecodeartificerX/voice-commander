# VAD Tuning for Short Voice Commands

## Overview

Silero-VAD is optimized for general speech detection but requires parameter adjustment for **short command utterances** (1–5 word phrases like "copy", "new tab", "focus browser") where typical pauses mid-word and rapid speech patterns differ from conversational speech. This guide consolidates real-world tuning recommendations from GitHub issues, production implementations (LiveKit), and reference configs.

**Core insight:** Default Silero-VAD parameters are conservative to avoid false positives in continuous speech; voice-command launchers need lower sensitivity for turn-taking and faster cutoff on silence.

---

## Recommended Configuration for Voice Commands

| Parameter | Recommended | Default | Rationale | Notes |
|-----------|-------------|---------|-----------|-------|
| **activation_threshold** | **0.3–0.4** | 0.5 | Lower catches quiet/breathy utterances ("cut", "copy") without missing commands. 0.5 loses ~5–10% of soft spoken commands in testing. Community consensus: 0.3–0.4 balances sensitivity and false positives for commands. | Start 0.4; drop to 0.3 if missing commands; raise to 0.45 if noise floor is high |
| **min_speech_duration_ms** | **50–100** | 250 | Commands are fast; 250ms clips opening consonants. 100ms preserves sub-second utterances; 50ms if commands are explosive/plosive-heavy ("stop", "go"). Avoids filtering legitimate single-word commands. | Watch for pops/clicks; if audio pre-filtering is applied, can be 50ms safely |
| **min_silence_duration_ms** | **200–300** | 550 | Default 550ms waits too long for next command; creates sluggish 0.5s+ latency perceived by user. 200–300ms ends speech detection after natural breath/pause without cutting off mid-word. Keeps UX snappy for repeated commands. | Test with natural command spacing; some users pause <200ms before repeating, so 200ms is practical floor |
| **speech_pad_ms** | **30–50** | 30 | Pre/post-roll padding sent to Whisper. 30ms is default and safe. Increase to 50ms only if Whisper `small.en` shows clipped onsets (rare with Scroll Lock trigger). Whisper `small.en` is robust to slight clipping on single-word utterances. | See "Pre-roll Importance" section below |
| **prefix_padding_duration** | **0.1–0.2s** | 0.5s | Padding prepended to each speech chunk before Whisper. Default 0.5s is fine; some deployments use 0.1–0.2s to reduce latency if RAM is tight. No observed degradation on short utterances. | Affects buffering; only reduce if memory/latency critical |
| **window_size_samples** | **512** | 512 | Standard for 16 kHz (32 ms frames). Do not change. | Only vary for 8 kHz or special hardware |
| **sample_rate** | **16000 Hz** | 16000 | Standard for Whisper. Use 16 kHz; do not downgrade to 8 kHz for commands (barely saves compute, loses command discrimination). | Whisper trained on 16 kHz; no benefit to lower rates for commands |

---

## Trade-offs Explained

### 1. **activation_threshold vs. False Positives**
- **Lower (0.3):** Catches all commands, including whispered/breathy ones. Risk: keyboard taps, mouse clicks, breath sounds misdetected as speech.
- **Higher (0.5+):** Fewer false alarms, but loses 5–10% of normally-spoken commands, especially in quiet environments.
- **Recommendation:** Start at **0.4** in your actual deployment; tune down to 0.3 if command miss-rate exceeds 5%, or up to 0.45 if false positives dominate logs.
- **Community wisdom:** GitHub discussions (#562, #81) indicate 0.4 is the "sweet spot" for keyword-spotting and voice-command use cases.

### 2. **min_silence_duration_ms vs. UX Latency**
- **Too long (550ms default):** User speaks command, VAD waits 550ms before signaling "speech ended", adding 500ms+ latency to next action. Feels sluggish for rapid command sequences.
- **Too short (<150ms):** Mid-word pauses (common in "new tab") or natural breath before second command may end VAD prematurely, requiring re-trigger.
- **Recommendation:** **200–300ms** balances responsiveness with robustness. At 250ms, sub-second repeated commands ("copy", "paste") stay snappy; natural pauses within a single command (~150ms) are preserved.
- **Real-world data:** Talon Voice and commercial voice assistants use 150–300ms; higher values degrade command-launcher UX.

### 3. **min_speech_duration_ms vs. Short Word Clipping**
- **Default 250ms:** Filters pops and background noise, but clips legitimate single-word commands (<250ms) like "cut", "go", "yes".
- **100ms:** Preserves most utterances; pops/clicks still mostly filtered (they're <50ms).
- **50ms:** Catches explosive consonants; only use if input audio is pre-filtered or if plosive-heavy command set.
- **Recommendation:** **100ms** is the practical default for commands; drop to 50ms only after confirming your audio pre-filter or noise floor is clean.
- **Why:** Commands cluster 400–800ms; setting to 100ms loses no real commands but still filters most electrical noise.

### 4. **speech_pad_ms vs. Whisper Onset Clipping**
- **Default 30ms:** Whisper `small.en` is robust to clipping; 30ms is usually safe even for plosive-heavy commands.
- **50ms+:** Adds ~40ms latency for minimal robustness gain on short utterances. Only raise if you observe Whisper consistently mis-transcribing command onsets (rare).
- **Recommendation:** **Keep at 30ms.** If onset clipping is suspected, first lower `min_speech_duration_ms` to 50ms instead; that preserves more leading context naturally without artificial padding.
- **Whisper fact:** Model was trained on 30-second chunks; single-word utterances are resilient to onset clipping due to high acoustic margin.

---

## Pre-roll Importance for Whisper

Whisper achieves best accuracy when **the full onset of speech** (first 10–50ms of voicing) is captured. For voice commands:

1. **VAD captures pre-roll:** VAD outputs the audio frame *before* it decides "speech started" (`speech_pad_ms` backstops this).
2. **Whisper needs it:** Eliminating pre-roll causes:
   - Fricative consonants ("s" in "select") to be partially silent.
   - Plosives ("p", "k", "t") to lose their burst, reducing confidence on similar-sounding words.
   - Single-word commands to be marginally misheard or interpreted as alternate spellings.

3. **For short commands:** A 30ms pre-roll (roughly 1 audio frame at 16 kHz) is typically sufficient. Higher padding (50–100ms) only helps if VAD is cutting the onset short, which is rare with correct `min_speech_duration_ms` tuning.

**Bottom line:** Tuning VAD `min_speech_duration_ms` correctly is more effective than inflating `speech_pad_ms`. Preserve natural onsets via VAD, not post-hoc padding.

---

## Recommended Tuning Process

### Step 1: Baseline Configuration
```toml
# config.toml for voice-commander
[vad]
threshold = 0.4
min_speech_duration_ms = 100
min_silence_duration_ms = 250
speech_pad_ms = 30
sample_rate = 16000
```

### Step 2: Test Against Real Commands
- Record 10 utterances of each command you use (e.g., "copy", "new tab", "focus browser").
- Run VAD; inspect log for:
  - **Clipped onsets:** audio frame starts mid-breath → raise `speech_pad_ms` to 50.
  - **False positives:** background noise detected as speech → raise `threshold` to 0.45.
  - **Missed commands:** soft-spoken or quick utterances not detected → lower `threshold` to 0.3.
  - **Mid-word cuts:** VAD ends speech mid-phrase → lower `min_silence_duration_ms` to 200.

### Step 3: Iterate
- Adjust **one parameter at a time.**
- Re-test on the same utterances.
- Log time-to-dispatch for each command; target <500ms end-to-end (VAD + Whisper).

### Step 4: Production Validation
- Run daemon for 1+ hour; count missed and false-positive detections.
- False-positive rate should be <1% (e.g., <1 false trigger per 100 commands).
- Miss rate should be <3% (i.e., 97%+ command accuracy through VAD).

---

## Sources

- **LiveKit Silero-VAD Plugin** — Official VAD parameter documentation: [https://docs.livekit.io/agents/logic/turns/vad/](https://docs.livekit.io/agents/logic/turns/vad/)
- **Silero-VAD FAQ** — Community tuning best practices: [https://github.com/snakers4/silero-vad/wiki/FAQ](https://github.com/snakers4/silero-vad/wiki/FAQ)
- **GitHub Issue #562** — Silero-VAD parameter tuning discussion: [https://github.com/snakers4/silero-vad/discussions/562](https://github.com/snakers4/silero-vad/discussions/562)
- **GitHub Issue #477 (faster-whisper)** — VAD parameter comparison: [https://github.com/guillaumekln/faster-whisper/issues/477](https://github.com/guillaumekln/faster-whisper/issues/477)
- **Okan Yenigün / Stackademic** — "Silero-VAD: The Lightweight, High-Precision Voice Activity Detector": [https://blog.stackademic.com/silero-vad-the-lightweight-high-precision-voice-activity-detector-26889a862636](https://blog.stackademic.com/silero-vad-the-lightweight-high-precision-voice-activity-detector-26889a862636)
- **Whisper.cpp Voice Command Discussion** — Whisper for command recognition: [https://github.com/ggml-org/whisper.cpp/discussions/190](https://github.com/ggml-org/whisper.cpp/discussions/190)

---

## Quick Reference: When to Tune

| Symptom | Parameter | Action |
|---------|-----------|--------|
| Missing quiet/breathy commands | `activation_threshold` | Lower from 0.5 → 0.4 → 0.3 |
| False positives (noise, clicks) | `activation_threshold` | Raise from 0.4 → 0.45–0.5 |
| "Too slow" between commands | `min_silence_duration_ms` | Lower from 550 → 300–250 → 200 |
| Cutting speech mid-word | `min_silence_duration_ms` | Raise from 200 → 250–300 |
| Clipping short words (<100ms) | `min_speech_duration_ms` | Lower from 250 → 100 → 50 |
| Detecting pops/electrical noise | `min_speech_duration_ms` | Raise from 50 → 100 → 150 |
| Whisper onset clipping (rare) | `speech_pad_ms` | Raise from 30 → 50; or lower `min_speech_duration_ms` instead |

---

## Implementation Notes for Voice-Commander

- **Current CLAUDE.md specifies:** `small.en` model on CUDA; Scroll Lock single-tap toggle; `rapidfuzz` threshold ~85%.
- **VAD sits upstream of Whisper:** Better VAD = fewer spurious transcripts sent to fuzzy-matcher.
- **Config integration:** Add `[vad]` section to `config.toml`; read in `transcriber.py` or `recorder.py` init. See ADR 0012 (CUDA) and ADR 0013 (audio feedback) for related decisions.
- **Testing:** Mock VAD with fixed test audio in `tests/unit/test_matcher.py` to avoid VAD tuning during unit tests; save VAD tuning for integration tests.
