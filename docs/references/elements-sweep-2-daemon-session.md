# Elements-mode adversarial sweep — daemon + session state machine

**Scope:** `src/voice_commander/daemon.py` (elements block in `_process_utterance`,
`_do_element_scan`, `_do_element_click`, `_elements_executor`, `on_scroll_lock` cancel,
constructor wiring) + `src/voice_commander/elements/session.py` (full).

**Symptom targeted:** works correctly the first invocation after a fresh start, then
stops working on the second invocation.

---

## Issue ranking (most-likely root cause first)

---

### ISSUE 1 — State stuck in SCANNING after a successful first cycle (CRITICAL — primary suspect)

**File:** `src/voice_commander/elements/session.py:89-105`  
**Also:** `src/voice_commander/daemon.py:602-618`

**The bug:**

After a full successful cycle (scan → show → number → click → hide) the flow is:

1. `begin_scan()` → IDLE → SCANNING
2. `_do_element_scan` (worker thread) calls `show()` → SCANNING → HINTS_SHOWN + timer
3. User says number → `handle_utterance()` → HINTS_SHOWN → **IDLE**, timer cancelled,
   `elements.hide` published, returns `element`
4. Daemon submits `_do_element_click(element)` to `_elements_executor`

This looks clean — the session is back to IDLE after step 3.  
**BUT**: step 4 submits `_do_element_click` to `_elements_executor` after the lock in
`_process_utterance` at line 610 is already released and the state is already IDLE. That
is correct.

However the **actual stuck-in-SCANNING scenario** arises on the *second invocation* under
a specific but common timing:

1. Second "elements" utterance arrives at the pipeline thread.
2. Pipeline reads `state == IDLE`, calls `begin_scan()` → IDLE → SCANNING (line 614).
3. Submits `_do_element_scan` to `_elements_executor` (line 616).
4. **If the `_elements_executor` queue already has a stale first-cycle task still running
   or queued** (see Issue 2), the new `_do_element_scan` sits behind it.
5. Meanwhile, the pipeline thread's *next* utterance arrives and reads
   `state == SCANNING` (line 604-605) → silently dropped.
6. The user can keep saying numbers while in SCANNING state — all are swallowed silently
   (no click, no hide).

More importantly: there is no guard in `begin_scan()` that prevents a fresh scan from
being started when a stale `_do_element_scan` task is still occupying the single-worker
executor queue. Two `_do_element_scan` calls can therefore queue up.

**Why this causes works-once-then-fails:** on the first cycle the executor is empty and
the scan runs immediately. On the second cycle, if any residual task is queued (a slow
scan, a slow click), the new scan waits behind it — during which time the pipeline drops
all utterances as SCANNING noise.

**Fix direction:** Before submitting `_do_element_scan`, check whether the executor queue
is already occupied (a simple `_elements_scan_pending: bool` flag set before submit,
cleared at the top of `_do_element_scan`). Alternatively, cancel the executor's queued
tasks via a future-tracking mechanism before submitting a fresh scan.

---

### ISSUE 2 — Timer from invocation #1 can fire during invocation #2 (HIGH)

**File:** `src/voice_commander/elements/session.py:120-128` (`_on_timeout`)  
**Also:** `session.py:130-135` (`_start_timer_locked`)

**The bug:**

`_cancel_timer_locked()` calls `self._timer.cancel()`. From Python docs, `Timer.cancel()`
sets a stop event but **does not guarantee the timer function is not already running**. If
the timeout fires *concurrently* with `handle_utterance()` or `cancel()`:

- `handle_utterance` acquires `_lock`, transitions HINTS_SHOWN → IDLE, clears
  `_elements`, sets `_timer = None`, releases lock, publishes `elements.hide`.
- `_on_timeout` then acquires `_lock`: it checks `if self._state is not
  ElementsState.HINTS_SHOWN` → the state is now IDLE → returns early. ✓

So for the `_on_timeout` vs `handle_utterance` race, the lock + state check makes it
safe: the stale timer will be a no-op.

**But the stacking timer scenario is still possible:**

`_start_timer_locked` is called from `show()`, which holds `_lock`. It creates a brand-
new `threading.Timer` and assigns it to `self._timer`. It does **not** check whether
`self._timer` is already non-None before creating a new one. The only place a stale timer
could remain non-None at the point `show()` runs is if the session is in SCANNING state
at that moment — and it is, since `show()` guards on `self._state is
ElementsState.SCANNING`. A SCANNING state means no timer should be running (timer is only
started in `show()` after SCANNING → HINTS_SHOWN). So this particular double-creation
path appears safe in isolation.

**The real residual risk:** `Timer.cancel()` does not join the timer thread. After
`cancel()` + `self._timer = None`, the timer's internal `threading.Thread` may still be
alive for a small window. If `_on_timeout` has already started executing *before*
`_cancel_timer_locked` ran, and the state-machine guard in `_on_timeout` (line 123) fires
after `handle_utterance` already reset the state to IDLE, the extra `bus.publish(
"elements.hide", {})` at line 128 **is executed** — a spurious second `elements.hide`
event is published.

**Consequence:** on the second invocation, if the first cycle's overlay was dismissed by
timer rather than by number-pick, *and* the overlay process received a late duplicate
`elements.hide`, the overlay process may be in a confused state when it receives the
second invocation's `elements.show`.

**Severity of this path:** only reachable when `_on_timeout` wins the race over
`_cancel_timer_locked` — i.e. the timer fires in the instant between `cancel()` being
called and the lock being released. Low probability per individual cycle, but cumulative
across invocations.

**Fix direction:** Assign `self._timer = None` *before* calling `timer.cancel()` inside
`_cancel_timer_locked`, then in `_on_timeout` test `self._timer is not None` (or use a
generation counter) to distinguish a cancelled-but-racing fire from a live one.

---

### ISSUE 3 — `_do_element_scan` not guarded against being submitted twice (HIGH)

**File:** `src/voice_commander/daemon.py:613-617`

```python
if _normalize_spoken(result.text) in ENTRY_WORDS:
    self._elements_session.begin_scan()
    self._feedback.on_recording_start()
    self._elements_executor.submit(self._do_element_scan)
    run.set_status("ok")
    return
```

`begin_scan()` is properly guarded (no-op if not IDLE). So a second "elements" utterance
arriving while `_state == SCANNING` will **not** call `begin_scan()` a second time — that
is correct.

**However**, the path falls through to the SCANNING-state check at line 604 which returns
early (`run.set_status("ok"); return`). This means the "element"/"elements" utterance
itself is silently consumed without telling the user anything is wrong — no chime, no
re-queuing of the scan.

More critically: if the user says "elements" twice in quick succession while the first
`begin_scan()` has run but `_do_element_scan` hasn't started yet (still queued), the
second utterance is consumed by the `state == SCANNING` guard and swallowed. This is
*by design*, but if `_do_element_scan` then fails (no window, nothing found), the session
returns to IDLE — but the user got no feedback that the second invocation was ignored.

**No actual double-scan is possible** via this path alone. The issue is that SCANNING is
a tarpit: nothing the user says can escape it until the worker thread finishes, and on
failure there is no retry mechanism.

---

### ISSUE 4 — `handle_utterance` called outside HINTS_SHOWN returns None silently (MEDIUM)

**File:** `src/voice_commander/elements/session.py:89-105`  
**Also:** `src/voice_commander/daemon.py:607-612`

```python
# daemon.py
if state is ElementsState.HINTS_SHOWN:
    element = self._elements_session.handle_utterance(result.text)
    if element is not None:
        self._elements_executor.submit(self._do_element_click, element)
    run.set_status("ok")
    return
```

The daemon reads `state` (line 603) *outside the lock*, then calls `handle_utterance`
which acquires the lock internally. There is a TOCTOU window:

1. Pipeline thread reads `state == HINTS_SHOWN`.
2. Timer fires, `_on_timeout` acquires lock, transitions HINTS_SHOWN → IDLE, publishes
   `elements.hide`, releases lock.
3. Pipeline thread calls `handle_utterance` — it acquires lock, sees
   `state != HINTS_SHOWN`, returns `None` without publishing a second `elements.hide`. ✓

This race is actually safe because `handle_utterance` re-checks state under the lock.
But the **converse race** is the problem:

1. Pipeline thread reads `state == HINTS_SHOWN`, enters the if-branch.
2. Meanwhile `_on_timeout` fires, acquires lock: HINTS_SHOWN → IDLE, publishes
   `elements.hide`, sets `_timer = None`.
3. Pipeline thread calls `handle_utterance`: acquires lock, state is now IDLE, returns
   `None` (no click, no second hide). ✓

This is safe. The only symptom is that the user's number utterance is silently swallowed
(no click performed, no miss chime). Not a crash, but a confusing UX.

**The double-hide via the timer race** (described in Issue 2) remains the only path where
a stray event is actually emitted.

---

### ISSUE 5 — Utterance falls through to command routing when HINTS_SHOWN race loses (LOW-MEDIUM)

**File:** `src/voice_commander/daemon.py:602-618`

The state-machine check block is:

```python
if self._elements_session is not None:
    state = self._elements_session.state          # snapshot outside lock
    if state is ElementsState.SCANNING:
        run.set_status("ok")
        return
    if state is ElementsState.HINTS_SHOWN:
        element = self._elements_session.handle_utterance(result.text)
        ...
        run.set_status("ok")
        return
    if _normalize_spoken(result.text) in ENTRY_WORDS:
        ...
```

If the timer fires between the `state` snapshot and the HINTS_SHOWN if-branch, and
`handle_utterance` returns `None` (state was already reset to IDLE by timeout), execution
falls through to `run.set_status("ok"); return` — it does **not** fall through to command
routing, because of the unconditional `return` at line 612. ✓

However if the state snapshot sees `IDLE` (neither SCANNING nor HINTS_SHOWN), and the
utterance is *not* an entry word, the code falls through to the normal command-routing
pipeline — **even if elements mode was conceptually active**. This can only happen if the
timer fired between the time the user said the number and the pipeline thread processed
it, which is unlikely but possible on a heavily loaded system.

**Impact:** a number like "three" gets routed to command matching. Most likely a miss
chime. Not a crash, but confusing.

---

### ISSUE 6 — `_do_element_click` runs after session returns to IDLE — no guard (LOW)

**File:** `src/voice_commander/daemon.py:610-611`  
**Also:** `daemon.py:816-822`

```python
element = self._elements_session.handle_utterance(result.text)
if element is not None:
    self._elements_executor.submit(self._do_element_click, element)
```

`_do_element_click` receives the `element` by value at the time of submit. At the time it
actually executes, the session is in IDLE and a new scan may already be in progress or
even completed. `_do_element_click` does not check the session state before clicking —
it just calls `clicker.click_point`. This is intentional (the element was resolved
correctly), but if the click task is slow (settle_ms=50 + pyautogui overhead) and the
user has already started a second "elements" scan, the stale click fires mid-scan.

This is a **late-click contamination**: the user starts a new scan, sees the overlay, says
a number, and a stale click from the previous cycle also fires, resulting in two clicks.

**Precondition:** the first click was submitted, the user *immediately* says "elements"
again, the second scan completes and shows hints before the first click executes. Possible
on a fast machine where the click's settle delay puts it behind the new utterance
processing.

**Fix direction:** Assign a `_click_generation` counter, increment it on `begin_scan`,
capture the current value in the closure, and bail in `_do_element_click` if the counter
has advanced.

---

### ISSUE 7 — `on_scroll_lock` cancel path does not drain `_elements_executor` (LOW)

**File:** `src/voice_commander/daemon.py:374-375`

```python
if self._elements_session is not None and self._elements_session.active:
    self._elements_session.cancel()
```

`cancel()` correctly resets session state and publishes `elements.hide` if an overlay was
showing. But the `_elements_executor` may still have a queued or running `_do_element_scan`
or `_do_element_click` task. These tasks run to completion regardless, because the
executor is never shut down or drained during a session close.

After scroll-lock close + immediate scroll-lock re-open:

1. Session is now IDLE (cancel() ran).
2. User says "elements" → `begin_scan()` → SCANNING, new scan submitted.
3. Stale `_do_element_scan` from before the close fires first (if it was queued, not
   running): calls `self._elements_session.show()` — but state is now SCANNING (correct
   state for show), so **it succeeds** and shows an overlay for the OLD window.
4. New scan then fires, calls `show()` — state is now HINTS_SHOWN (not SCANNING) → **no-
   op**. The new scan's results are silently discarded.

**This is a direct cause of the described symptom**: old scan results appear on the second
invocation, new scan results are dropped.

**Precondition:** scroll-lock close+reopen while a scan was queued but not yet running.
More likely: two rapid "elements" invocations (no scroll-lock between them), combined with
a slow scan (timeout near 3 s): the first scan was still running when the second
`begin_scan()` was called; the second scan was submitted but sits behind the first;
the first scan's `show()` succeeds; the second scan's `show()` is a no-op.

**Fix direction:** Track the executor task via `Future.cancel()` at the start of a new
scan, or use a scan-generation counter checked at the top of `_do_element_scan` and
`_do_element_click`.

---

### ISSUE 8 — `_do_element_scan` calls `fail()` for error paths, but `fail()` clears state unconditionally (LOW-MEDIUM)

**File:** `src/voice_commander/daemon.py:797-814`  
**Also:** `session.py:81-87`

```python
def fail(self) -> None:
    with self._lock:
        self._cancel_timer_locked()
        self._state = ElementsState.IDLE
        self._elements = []
```

`fail()` transitions *any* state → IDLE. If `_do_element_scan` from invocation #1 finishes
with an error *after* invocation #2 has already successfully called `begin_scan()` and
moved the session to SCANNING, the stale `fail()` call will slam the session back to
IDLE — killing invocation #2's scan before it can show results.

This is the **cross-invocation state clobber** path and is a direct "works once then
stops" failure mode.

**Precondition:** first scan fails slowly (e.g. UIA timeout at 3 s), user says "elements"
again within those 3 seconds → second `begin_scan()` succeeds (first scan was submitted,
state was SCANNING, then transitioned to IDLE on first scan's fail(); `begin_scan()` from
the second utterance runs, state goes SCANNING again) — actually this is safe because
`begin_scan()` guards on `state is not IDLE`. But:

1. First scan running (state = SCANNING, thanks to first `begin_scan()`).
2. First scan finishes with error; calls `fail()` → state = IDLE.
3. Second "elements" utterance: reads `state == IDLE`, calls `begin_scan()` → SCANNING,
   submits second `_do_element_scan`.
4. Second scan runs fine, calls `show()` → HINTS_SHOWN, overlay appears.
5. User says number, click is performed.

This actually works. The issue is when step 2 and step 3 are swapped (i.e. the fail comes
*after* the second begin_scan). That requires the first scan task to be *queued* behind
a second task — impossible with a single-worker executor unless two tasks are queued.

Two tasks can be queued if `_do_element_scan` is submitted twice. But `begin_scan()` is
no-op when not IDLE, so the pipeline cannot submit twice unless state resets to IDLE
between submissions. With a single worker the execution order is FIFO, so if task1 and
task2 are both `_do_element_scan`, task1 always runs first — and task1 calling `fail()`
from SCANNING may clobber a state that task2's `begin_scan()` had just set, if there was
a state reset in between.

The most dangerous race is specifically: task1 finishes with fail() → IDLE, then on the
pipeline thread a new begin_scan() fires → SCANNING, submitting task2 — but task1 hasn't
returned yet so the executor immediately picks up task2. Now task2 calls `show()` and sets
HINTS_SHOWN. This is normal. **No clobber here.**

The actual clobber requires the stale `fail()` to run *after* a subsequent `begin_scan()`
has moved the state forward, which is impossible with a FIFO single-worker executor when
the pipeline thread only submits after `begin_scan()` succeeds. Issue 8 is lower risk
than initially assessed; it is superseded by Issues 1, 3, and 7.

---

## Summary table

| # | Location | Severity | "Works once then fails" vector |
|---|----------|----------|-------------------------------|
| 1 | `daemon.py:614-617` + `session.py:54-59` | **CRITICAL** | Stale task in executor queue keeps new scan stuck in SCANNING tarpit |
| 7 | `daemon.py:374-375` | **HIGH** | Stale queued scan fires after scroll-lock close, occupies HINTS_SHOWN, real scan's `show()` is no-op |
| 2 | `session.py:120-128` | **HIGH** | Timer fires while cancel() executes → duplicate `elements.hide` confuses overlay on next invocation |
| 6 | `daemon.py:610-611` | **LOW-MEDIUM** | Stale click fires mid-second-scan; two clicks on second invocation |
| 3 | `daemon.py:602-617` | **MEDIUM** | SCANNING is a silent tarpit — no retry, no re-entry until worker finishes |
| 4 | `session.py:89-105` | **MEDIUM** | Timer-wins TOCTOU: user's number swallowed silently, no click, no chime |
| 5 | `daemon.py:602-618` | **LOW-MEDIUM** | Number utterance falls to command router if state snapshot race loses and is not an entry word |
| 8 | `daemon.py:797-814` | **LOW** | `fail()` unconditionally clears state — could clobber second begin_scan() in edge race |

---

## Top suspect (root cause of works-once-then-fails)

**Issue 7 combined with Issue 1.**

The single-worker `_elements_executor` is never drained or cancelled between invocations.
On the first cycle, the scan task runs immediately (empty queue). If the first scan is
slow (Chromium UIA tree near the 3 s timeout) and the user says "elements" again before
it finishes:

1. First scan is still running on the executor. State = SCANNING.
2. Second "elements" utterance: reads `state == SCANNING` → silently swallowed (Issue 3,
   SCANNING tarpit). User gets no feedback.
3. First scan completes, calls `show()` → HINTS_SHOWN. Overlay appears — for the old
   window from invocation #1. User says a number, click fires on the old window's
   element. Looks broken.

Or, if the user waited long enough (first scan finished, session returned to IDLE):

1. First cycle complete, all clean. `_elements_executor` has one completed task.
2. User says "elements" again → `begin_scan()` → SCANNING, second scan submitted.
3. Executor picks it up immediately (first task long-since finished). Scan runs. `show()`
   called → HINTS_SHOWN. Overlay appears. User says number. `handle_utterance()` returns
   element. `_do_element_click` submitted.
4. This should work — but the stray `elements.hide` from a racing timer (Issue 2) or
   a duplicate event reaching the overlay process at the wrong time can leave the overlay
   in a state where it ignores the second `elements.show`.

The most direct "works once then fails on the very next try" cause is **Issue 2**: if the
overlay process receives a spurious `elements.hide` from a racing first-cycle timer, it
hides itself. When the second `elements.show` arrives, if the overlay's own state machine
doesn't handle `show` while already visible, it may ignore the second show entirely.
This is a consumer-side effect of the stale timer event, but the root cause is in
`_on_timeout` / `_cancel_timer_locked` not being race-proof.

**The single highest-leverage fix:** add a scan-generation counter (`_scan_gen: int`)
to `ElementsSession`, incremented on every `begin_scan()`. Pass the current generation
to `_start_timer_locked` and capture it in the timer closure. In `_on_timeout`, bail if
the captured generation does not match `self._scan_gen`. This eliminates stale timer
fires across invocations. Combine with a parallel fix in `_do_element_scan`/
`_do_element_click` checking the generation before acting.
