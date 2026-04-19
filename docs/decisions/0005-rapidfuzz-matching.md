# ADR 0005: rapidfuzz for fuzzy phrase matching (WRatio, threshold 85)

**Status:** Accepted
**Date:** 2026-04-19

## Context

After transcription, Voice Commander must map a free-form text string (e.g. `"open the browser please"`) to the closest registered phrase (e.g. `"open browser"`). ASR output is imperfect: it may insert filler words, drop articles, or slightly mis-transcribe consonants. A strict string-equality check would miss too many valid utterances, while a full embedding-based semantic search would be overly complex for a vocabulary of ~50 short command phrases. The matching step runs synchronously on every voice command, so it must complete in under 5 ms to avoid perceptible delay.

## Decision

We use `rapidfuzz` with the `WRatio` scorer and a match-acceptance threshold of 85. `WRatio` (Weighted Ratio) is a composite scorer that internally tries multiple comparison strategies — simple ratio, partial ratio, token sort ratio, token set ratio — and returns the best result. This handles word-order variation and partial transcript overlap gracefully. A threshold of 85 was empirically validated against a sample of ASR outputs to yield low false-positive rates while still accepting natural phrasing variations. `rapidfuzz` implements all scorers in C extensions, making it 10–100x faster than pure-Python alternatives.

## Consequences

### Positive
- Matching across 50 phrases completes in under 1 ms (single-threaded), adding negligible latency to the command pipeline.
- `WRatio` naturally handles transcript variations like dropped articles, inserted filler words, and mild word reordering.
- `rapidfuzz` is actively maintained, has a permissive MIT licence, and installs as a pre-built wheel on Windows with no C toolchain.
- The threshold is a single integer constant, easy to tune without code changes.

### Negative
- Fuzzy matching is inherently heuristic; a threshold of 85 may accept false positives for very short phrases with many common characters (e.g. `"type"` vs `"wipe"`). This must be mitigated by choosing sufficiently distinct registered phrases.
- `WRatio` does not understand semantic intent — it is purely character/token-level similarity. Two phrases with very different wording but identical meaning will not match (acceptable given the controlled vocabulary).
- The threshold was calibrated on one voice and microphone setup; different accents or noisy environments may require adjustment.

### Neutral
- The scorer and threshold are both runtime-configurable via `MATCH_SCORER` and `MATCH_THRESHOLD` environment variables, so production tuning does not require code changes.
- `rapidfuzz` is a drop-in replacement for `thefuzz`/`fuzzywuzzy` with an identical API, making a future migration trivial if needed.

## Alternatives considered

### thefuzz / fuzzywuzzy (pure Python)
The original Python fuzzy-string library that `rapidfuzz` was designed to replace. It offers the same `WRatio` algorithm but implemented in pure Python (or optionally with `python-Levenshtein`). Benchmarks show it is 10–100x slower than `rapidfuzz` for the same operations. At ~50 phrases this is still sub-millisecond, but the performance gap widens when scanning larger phrase lists and makes `thefuzz` a poor long-term choice.

### Regex-based templates
Each tool phrase could be expressed as a regular expression (e.g. `r"open\s+(the\s+)?browser"`). This gives precise control and zero false positives, but it requires manually authoring a regex for every phrase and is brittle: a single unexpected word insertion by the ASR engine causes a miss. Maintenance burden grows linearly with phrase count and degrades developer experience significantly.

### Embedding similarity (sentence-transformers / cosine distance)
Semantic embedding models (e.g. `all-MiniLM-L6-v2`) would handle paraphrasing and synonym variation elegantly. For a vocabulary of ~50 phrases, however, this is significant overkill: the model adds hundreds of megabytes to the install, requires a GPU or multi-millisecond CPU inference per command, and introduces a second neural-network dependency with its own CUDA DLL requirements. The benefit — handling true semantic paraphrasing — is not needed for a structured command vocabulary where users speak known phrases.

## References
- Spec: [../superpowers/specs/2026-04-19-voice-commander-design.md](../superpowers/specs/2026-04-19-voice-commander-design.md)
- rapidfuzz documentation: https://rapidfuzz.github.io/RapidFuzz/
- rapidfuzz GitHub: https://github.com/rapidfuzz/RapidFuzz
- WRatio scorer explanation: https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html#rapidfuzz.fuzz.WRatio
