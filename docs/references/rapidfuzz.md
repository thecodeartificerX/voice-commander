# rapidfuzz Reference

> Cited from: https://rapidfuzz.github.io/RapidFuzz/Usage/process.html (2026-04-19)
> Cited from: https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html (2026-04-19)

---

## Overview

RapidFuzz is a fast string matching library for Python, implemented in C++ with
Python bindings. It provides fuzzy string matching algorithms compatible with
(and significantly faster than) the popular `fuzzywuzzy` library. Voice Commander
uses it to match transcribed speech against the registered command list.

## Installation

```bash
pip install rapidfuzz
```

No optional dependencies required. Pre-built wheels for Windows/Linux/macOS.

## Minimal Working Example (Voice Commander)

```python
from rapidfuzz import process, fuzz

# Registered command list
commands = [
    "open browser",
    "close window",
    "scroll down",
    "take screenshot",
    "volume up",
    "volume down",
]

# Transcribed text from faster-whisper
transcript = "open the browser"

# Find single best match
match = process.extractOne(
    transcript,
    commands,
    scorer=fuzz.WRatio,
    score_cutoff=85.0,  # discard if confidence < 85
)

if match:
    command, score, idx = match
    print(f"Matched: '{command}' (score={score:.1f}, idx={idx})")
else:
    print("No command matched above threshold")

# Find top-N matches
results = process.extract(
    transcript,
    commands,
    scorer=fuzz.WRatio,
    limit=3,
    score_cutoff=60.0,
)
for cmd, score, idx in results:
    print(f"  {cmd!r}: {score:.1f}")
```

---

## API Reference

### `process.extract`

```python
rapidfuzz.process.extract(
    query,
    choices,
    *,
    scorer=fuzz.WRatio,
    processor=None,
    limit=5,
    score_cutoff=None,
    score_hint=None,
    scorer_kwargs=None,
)
```

**Purpose:** Find the best matches in a list of choices, sorted descending by similarity.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | Sequence[Hashable] | — | String to search for |
| `choices` | Collection or Mapping | — | Strings to compare against. If dict/Mapping, keys are returned as the index in results |
| `scorer` | Callable | `fuzz.WRatio` | Scoring function to use. See scorers below |
| `processor` | Callable | None | Preprocessing function applied to query and choices before comparison. E.g. `str.lower` |
| `limit` | int | 5 | Max number of results. Pass `None` to return all matches |
| `score_cutoff` | float | None | Minimum score threshold. Results below this are discarded |
| `score_hint` | float | None | Expected score hint for optimization |
| `scorer_kwargs` | dict | None | Additional keyword arguments forwarded to scorer |

**Returns:** `list[tuple[str, float, int|key]]`

Each tuple: `(matched_choice, score, index_or_key)`
- `matched_choice`: The matched string from choices
- `score`: Similarity score 0–100 (for normalized scorers)
- `index_or_key`: Integer index (for list choices) or dict key (for mapping choices)

Results are sorted by score descending.

---

### `process.extractOne`

```python
rapidfuzz.process.extractOne(
    query,
    choices,
    *,
    scorer=fuzz.WRatio,
    processor=None,
    score_cutoff=None,
    score_hint=None,
    scorer_kwargs=None,
)
```

**Purpose:** Find the single best match. When multiple choices have equal scores, the first is returned.

Parameters are identical to `extract()` minus `limit`.

**Returns:** `tuple[str, float, int|key] | None`

Returns `None` if no match meets `score_cutoff`. Otherwise returns same tuple structure as one element of `extract()`.

**Voice Commander usage:**
```python
result = process.extractOne(
    transcript,
    command_registry,     # list[str] of registered command names
    scorer=fuzz.WRatio,
    score_cutoff=config.matching.threshold,   # default 85.0
)
if result is None:
    # No match — play miss sound
    ...
```

---

### `process.cdist`

```python
rapidfuzz.process.cdist(
    queries,
    choices,
    *,
    scorer=fuzz.ratio,
    processor=None,
    score_cutoff=None,
    score_hint=None,
    score_multiplier=1,
    dtype=None,
    workers=1,
)
```

**Purpose:** Compute a matrix of pairwise similarity scores between two collections.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `queries` | Collection | — | First collection |
| `choices` | Collection | — | Second collection |
| `scorer` | Callable | `fuzz.ratio` | Scoring function |
| `dtype` | numpy dtype | None | Result array type. `np.float32/64/uint8` for similarities; `int8/16/32/64` for distances |
| `workers` | int | 1 | Parallel cores; -1 = use all available |

**Returns:** `numpy.ndarray` of shape `(len(queries), len(choices))`

---

## Scorer Reference

### `fuzz.ratio`

```python
fuzz.ratio(s1, s2, *, processor=None, score_cutoff=0) -> float
```

Normalized Indel (insertion-deletion) distance. Computes the edit distance between
two strings and normalises it to 0–100. Purely sequence-based, no token splitting.

**When to use:** Simple character-level similarity. Best for strings of similar length.

```python
fuzz.ratio("voice command", "voice commands")  # ~96.3
fuzz.ratio("open browser", "open the browser") # ~88.9
```

---

### `fuzz.partial_ratio`

```python
fuzz.partial_ratio(s1, s2, *, processor=None, score_cutoff=0) -> float
```

Finds the optimal alignment of the shorter string within the longer string. Returns
the ratio for that best sub-sequence alignment.

- Short strings ≤ 64 chars: O(NM) — guarantees optimal alignment
- Long strings > 64 chars: heuristic using longest common substrings

**When to use:** When the query may be a substring of the choice (or vice versa).

```python
fuzz.partial_ratio("browser", "open the browser now")  # ~100
fuzz.ratio("browser", "open the browser now")          # ~46
```

---

### `fuzz.token_set_ratio`

```python
fuzz.token_set_ratio(s1, s2, *, processor=None, score_cutoff=0) -> float
```

Tokenises both strings, computes common and unique tokens, then compares using
`fuzz.ratio` on various combinations. Returns the maximum ratio.

Key behaviour: Returns 100 when one string's tokens are a complete subset of the other.

**When to use:** Word-order-insensitive matching; handles extra/missing words well.

```python
fuzz.token_set_ratio("open browser", "browser open")    # 100
fuzz.token_set_ratio("scroll down", "please scroll down please")  # 100
```

---

### `fuzz.WRatio` (Weighted Ratio)

```python
fuzz.WRatio(s1, s2, *, processor=None, score_cutoff=0) -> float
```

A meta-scorer that combines multiple algorithms with heuristic weighting:
1. Computes `fuzz.ratio`
2. If length difference is significant, tries `fuzz.partial_ratio`
3. Tries `fuzz.token_sort_ratio` and `fuzz.token_set_ratio`
4. Returns the highest result

This is the **default scorer** for `process.extract`/`process.extractOne`.

**When to use:** General-purpose, works well across diverse query/choice pairs.
Recommended for Voice Commander command matching.

```python
fuzz.WRatio("open the browser", "open browser")   # ~95
fuzz.WRatio("scroll down please", "scroll down")  # ~95
```

---

### `fuzz.token_sort_ratio`

```python
fuzz.token_sort_ratio(s1, s2, *, processor=None, score_cutoff=0) -> float
```

Sorts tokens alphabetically before comparing with `fuzz.ratio`. Makes comparison
order-independent without the subset tolerance of `token_set_ratio`.

---

### `fuzz.partial_token_set_ratio`

Combines `partial_ratio` with token set logic. Useful when substring + word-order
independence is needed simultaneously.

---

## Scorer Comparison Table

| Scorer | Order-sensitive | Substring-aware | Token-aware | Best for |
|--------|-----------------|-----------------|-------------|----------|
| `fuzz.ratio` | Yes | No | No | Simple char comparison |
| `fuzz.partial_ratio` | Yes | Yes | No | Query is substring of choice |
| `fuzz.token_sort_ratio` | No | No | Yes | Word order varies |
| `fuzz.token_set_ratio` | No | No | Yes | Extra/missing words |
| `fuzz.WRatio` | Smart | Smart | Smart | General purpose ✓ |

---

## Processor Functions

The `processor` parameter pre-processes strings before comparison:

```python
from rapidfuzz import utils

# Built-in processors
process.extractOne(query, choices, processor=utils.default_process)
# default_process: lowercases + removes non-alphanumeric chars

# Custom processor
process.extractOne(query, choices, processor=str.lower)
```

`rapidfuzz.utils.default_process` is the most common choice for voice matching as it
strips punctuation and normalises case.

---

## Performance Notes

- RapidFuzz is ~10-100× faster than fuzzywuzzy for large choice lists
- For lists > 1000 items, consider `process.cdist` with `workers=-1` for parallelism
- All scorer functions release the GIL for strings > 64 chars (C++ backend)
- `score_cutoff` provides early-exit optimisation — always set it when possible

---

## Known Gotchas

1. **`score_cutoff` on `extractOne` returns `None`** — not an exception. Always check
   `if result is not None` before unpacking.

2. **Dictionary choices** — if `choices` is a dict, the third element of the tuple is
   the dict key, not an integer index. This is useful for `{command_name: handler}` maps.

3. **`processor=None` by default** — unlike fuzzywuzzy, no preprocessing is applied.
   Add `processor=rapidfuzz.utils.default_process` for case-insensitive matching.

4. **`fuzz.WRatio` can be 0** for empty strings — guard against empty transcripts.

5. **Return order** — `process.extract` returns sorted descending. `limit=1` is NOT
   the same as `extractOne` for empty results (extractOne returns None, extract returns []).

---

## Cited from

- https://rapidfuzz.github.io/RapidFuzz/Usage/process.html — fetched 2026-04-19
- https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html — fetched 2026-04-19
