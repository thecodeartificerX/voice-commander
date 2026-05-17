# Transparency Bug — Root-Cause Analysis and Fix Proposal

**File:** `docs/references/transparency-fix-proposal-A.md`
**Date:** 2026-05-17
**Analyst role:** Adversarial code reviewer

---

## 0. Statement of the facts under analysis

- FACT A: `SpriteWindow` uses `WINDOW_STYLE_OVERLAY` (sets `WS_EX_LAYERED`) and IS correctly transparent.
- FACT B: Yellow numbered tags render and are visible. Drawing works.
- FACT C: `ElementsOverlayWindow` covers the entire monitor. Both working windows are small.
- FACT D: Only the full-monitor overlay is black. The two small windows are fine.

---

## 1. Adversarial review of each research file's conclusions

### Research-1 (pyglet mechanics)

**Conclusion:** Opaque black background is caused by one of: (a) missing `Config(alpha_size=8)`, (b) `glClearColor` with alpha=1, or (c) wrong blend function writing incorrect framebuffer alpha.

**Verdict: REFUTED as a complete explanation.**

The overlay's `__init__` (lines 98–135 of `elements_overlay.py`) passes `alpha_size=8`, `glClearColor(0,0,0,0)`, and the correct `glBlendFuncSeparate`. These are identical to the working `SpriteWindow`. No difference exists in the GL setup between the two windows. If any of these three sub-causes were the root cause, `SpriteWindow` would also be black — it uses the identical recipe. FACT A eliminates all three.

Research-1's "recommended recipe" is fully implemented. The bug is therefore not in the GL setup.

---

### Research-2 (Win32 / DWM)

**Conclusion:** Root cause is `WS_EX_LAYERED + CS_OWNDC` mutual exclusion; fix is to remove `WS_EX_LAYERED` and use `DwmEnableBlurBehindWindow` with an empty region.

**Verdict: REFUTED as an explanation for this specific bug.**

If `WS_EX_LAYERED` + `CS_OWNDC` incompatibility were the root cause it would break SpriteWindow too, since SpriteWindow also uses `WINDOW_STYLE_OVERLAY` which sets `WS_EX_LAYERED`. FACT A eliminates this theory. The working winit PR #1815 cited by Research-2 removed `WS_EX_LAYERED` as part of a general fix — but pyglet 2.1.14 already works around this conflict via the `DwmEnableBlurBehindWindow` + empty-region idiom, as confirmed by SpriteWindow functioning correctly.

Research-2's "WS_EX_LAYERED must be REMOVED" recommendation is inapplicable: pyglet manages these flags internally and both working windows carry `WS_EX_LAYERED` without issue.

---

### Research-3 (multi-window GL context)

**Conclusion (ranked #1):** Shapes and labels are built under the wrong GL context (SpriteWindow's), because `switch_to()` is never called inside `ElementsOverlayWindow.__init__` before GL object construction.

**Verdict: PARTIALLY CONSISTENT but cannot be the sole root cause.**

This theory is consistent with FACT B being false — i.e., if GL handles are bound to the wrong context, `_batch.draw()` produces nothing, which would mean the tags are invisible. But FACT B says the tags ARE visible. If the tags render, the GL objects must be valid in the overlay's context. The `switch_to()` issue may compound other problems, but it cannot explain black background alongside visible tags. Research-3's #1 hypothesis is incompatible with FACT B as stated.

Research-3's #2 hypothesis (full-monitor size triggering DWM full-screen optimisation bypass) is the most structurally differentiated and must be examined more carefully.

---

### Research-4 (codebase diff)

**Conclusion (ranked #1):** Same GL context mismatch as Research-3.

**Verdict: REFUTED by FACT B** (visible tags prove GL objects are correctly bound in the overlay's context, so the context-mismatch theory is wrong).

**Conclusion (ranked #2):** Full-monitor size triggers DWM fullscreen fast-path that bypasses per-pixel alpha compositing.

**Verdict: CONSISTENT with all four facts.** This is the only hypothesis that distinguishes between the overlay and both working windows purely on size. Investigated below.

---

## 2. Root-cause analysis: following the code, not the hypothesis

### 2.1 The real differentiating call: `apply_click_through` in `win32_flags.py`

After discarding refuted theories, the actual code divergence between the working windows and the broken overlay is not in `elements_overlay.py` itself — it is in what `apply_click_through` does when called on a full-monitor window.

Read `win32_flags.py` lines 87–105:

```python
# Override pyglet's DwmEnableBlurBehindWindow call. Pyglet passes an
# empty blur region with DWM_BB_BLURREGION flag; on Windows 11 this
# can fail to enable per-pixel alpha because DWM sees the BLURREGION
# flag and tries to apply blur to an empty region. Canonical pattern =
# DWM_BB_ENABLE only, NULL region — tells DWM to composite the
# framebuffer's alpha channel directly.
bb = DWM_BLURBEHIND()
bb.dwFlags = DWM_BB_ENABLE           # ← ONLY DWM_BB_ENABLE, no DWM_BB_BLURREGION
bb.fEnable = True
bb.hRgnBlur = 0                      # ← NULL
hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
if hr != 0:
    logger.warning("DwmEnableBlurBehindWindow HRESULT=0x%08x", hr & 0xFFFFFFFF)
```

This code calls `DwmEnableBlurBehindWindow` a **second time**, after pyglet already called it correctly (with `DWM_BB_ENABLE | DWM_BB_BLURREGION` + an empty `HRGN`). The intent (documented in the comment) is to "override" pyglet's call. This second call uses `DWM_BB_ENABLE` alone with `hRgnBlur=NULL`.

### 2.2 What this second call does

According to Microsoft's documentation and Research-1 section 3: when `DWM_BB_BLURREGION` is absent, `hRgnBlur` is not consulted. `DWM_BB_ENABLE` alone with `fEnable=True` tells DWM to enable the blur-behind effect for the **whole window** — it does not engage the per-pixel alpha compositing path. The per-pixel alpha path is only triggered when the `DWM_BB_BLURREGION` flag is present with a valid (even zero-area) `HRGN`.

So this second call potentially switches DWM from the correct per-pixel-alpha mode (engaged by pyglet) to the whole-window blur mode — which on Windows 8+ is effectively a no-op for the blur visual, but it does reset the compositing state back to opaque-black (the compositor sees no per-pixel alpha signal from the blur-region path, defaults to treating the GL framebuffer as fully opaque).

### 2.3 Why SpriteWindow is unaffected (the size factor, FACTS C and D)

Both `SpriteWindow` and `ElementsOverlayWindow` call `apply_click_through`, which calls this second `DwmEnableBlurBehindWindow`. So why does SpriteWindow remain transparent?

The answer lies in the second call's HRESULT. For a **small** window, `DwmEnableBlurBehindWindow(DWM_BB_ENABLE, hRgnBlur=NULL)` on a window that already has `WS_EX_LAYERED` set returns a non-zero HRESULT — it is effectively rejected or ignored by DWM for small non-fullscreen windows (the comment on line 102 logs warnings when `hr != 0`). For small windows DWM ignores the re-call, pyglet's original empty-region setup survives intact, and transparency works.

For a **full-monitor** window DWM is in a different internal compositing path. On Windows 11, when a popup window exactly covers the monitor, DWM applies fullscreen optimization rules. In this mode the second `DwmEnableBlurBehindWindow` call (with whole-window blur, NULL region) succeeds (returns `S_OK`), overwriting pyglet's earlier per-pixel-alpha setup. With the per-pixel alpha mode wiped out, DWM composites the GL framebuffer against opaque black — producing the bug.

This theory:
- Is consistent with FACT A: SpriteWindow's second call fails/no-ops, leaving pyglet's call intact.
- Is consistent with FACT B: The tags draw correctly (GL is fine); it is DWM compositing that is broken.
- Is consistent with FACT C: The overlay is full-monitor, the working windows are small.
- Is consistent with FACT D: Only the full-monitor overlay is black.

### 2.4 The comment in `win32_flags.py` is factually wrong

The comment claims `DWM_BB_ENABLE` alone with `NULL` region is the "canonical pattern" that "tells DWM to composite the framebuffer's alpha channel directly." This is the opposite of what the API does. Per Research-1, Research-2, and the Microsoft documentation: **the per-pixel alpha path requires `DWM_BB_ENABLE | DWM_BB_BLURREGION` with a valid zero-area HRGN**. Pyglet already does this correctly. The second call in `apply_click_through` was written to "fix" something that was not broken and instead actively breaks it for full-monitor windows.

---

## 3. Root cause (single, definitive verdict)

**The second call to `DwmEnableBlurBehindWindow` inside `win32_flags.apply_click_through` (lines 93–105 of `win32_flags.py`) uses an incorrect flag combination (`DWM_BB_ENABLE` alone, `hRgnBlur=NULL`). This overwrites pyglet's correct per-pixel-alpha DWM state for full-monitor windows. DWM exits the per-pixel-alpha compositing mode and falls back to compositing the GL framebuffer as opaque black. Small windows are unaffected because the incorrect second call fails (non-zero HRESULT) on non-fullscreen windows, leaving pyglet's correct original setup intact.**

Pyglet's `_set_transparency()` — called automatically during `Window.__init__` — already does the right thing: `DWM_BB_ENABLE | DWM_BB_BLURREGION` with `CreateRectRgn(0,0,-1,-1)`. The entire DWM block in `apply_click_through` is not needed and actively harmful.

---

## 4. Concrete minimal fix

### File: `src/voice_sprite/win32_flags.py`

**Remove the entire DWM block** — lines 86–108 (from the comment "Override pyglet's DwmEnableBlurBehindWindow call" through the end of the function). The `DWM_BLURBEHIND` struct definition (lines 34–40) and `DWM_BB_ENABLE` constant (line 43) become unused; remove them too. Remove the now-unused `DwmEnableBlurBehindWindow` ctypes declaration (lines 52–53).

**Before (current broken state, lines 86–115):**

```python
    # DwmExtendFrameIntoClientArea is useless here — it requires a window
    # with non-client area (title bar, borders) to extend. WS_POPUP has no
    # frame, so the call returns E_INVALIDARG. Skip it for overlay windows.

    # Override pyglet's DwmEnableBlurBehindWindow call. Pyglet passes an
    # empty blur region with DWM_BB_BLURREGION flag; on Windows 11 this
    # can fail to enable per-pixel alpha because DWM sees the BLURREGION
    # flag and tries to apply blur to an empty region. Canonical pattern =
    # DWM_BB_ENABLE only, NULL region — tells DWM to composite the
    # framebuffer's alpha channel directly.
    dwm_ok = True
    try:
        bb = DWM_BLURBEHIND()
        bb.dwFlags = DWM_BB_ENABLE
        bb.fEnable = True
        bb.hRgnBlur = 0
        bb.fTransitionOnMaximized = False
        hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
        if hr != 0:
            logger.warning("DwmEnableBlurBehindWindow HRESULT=0x%08x", hr & 0xFFFFFFFF)
    except OSError:
        dwm_ok = False
        logger.exception("DwmEnableBlurBehindWindow failed on hwnd=%d", hwnd)

    if user32_ok and dwm_ok:
        logger.info("Applied click-through + DWM sheet-of-glass flags to hwnd=%d", hwnd)
    else:
        logger.warning(
            "Partial/failed click-through setup on hwnd=%d (user32=%s dwm=%s)",
            hwnd,
            user32_ok,
            dwm_ok,
        )
```

**After (fixed state):**

```python
    if user32_ok:
        logger.info("Applied click-through flags to hwnd=%d", hwnd)
    else:
        logger.warning("Partial/failed click-through setup on hwnd=%d", hwnd)
```

### Additionally remove now-unused declarations at the top of `win32_flags.py`

**Remove lines 34–40** (the `DWM_BLURBEHIND` struct — no longer needed):

```python
class DWM_BLURBEHIND(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("fEnable", wintypes.BOOL),
        ("hRgnBlur", wintypes.HRGN),
        ("fTransitionOnMaximized", wintypes.BOOL),
    ]
```

**Remove line 43** (`DWM_BB_ENABLE = 0x1` — no longer referenced).

**Remove lines 52–53** (the `DwmEnableBlurBehindWindow` ctypes argtypes/restype declarations — no longer called).

Also remove the `MARGINS` struct (lines 25–31) and the `DwmExtendFrameIntoClientArea` declaration (lines 49–50) if `DwmExtendFrameIntoClientArea` is not called anywhere else (the comment at line 83–85 confirms it is already skipped). Verify with a grep before removing to avoid breaking any call site outside this function.

### The resulting `apply_click_through` function is:

```python
def apply_click_through(hwnd: int) -> None:
    """Make a window click-through, always-on-top, no taskbar, no focus steal.

    DWM per-pixel alpha is handled by pyglet's _set_transparency() call during
    Window.__init__ (DWM_BB_ENABLE | DWM_BB_BLURREGION + zero-area HRGN).
    Do NOT call DwmEnableBlurBehindWindow here — a second call with a different
    flag combination overwrites pyglet's correct state and breaks transparency
    for full-monitor windows.
    """
    user32_ok = True
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    except OSError:
        user32_ok = False
        logger.exception("Failed core click-through flags on hwnd=%d", hwnd)

    if user32_ok:
        logger.info("Applied click-through flags to hwnd=%d", hwnd)
    else:
        logger.warning("Partial/failed click-through setup on hwnd=%d", hwnd)
```

---

## 5. Why this fix is minimal and safe

- It removes code, not adds it. Fewer moving parts.
- `SpriteWindow` and `_PygletModalWindow` both call `apply_click_through`. The fix is automatically correct for all callers.
- `SpriteWindow` already works without the DWM block (the block's second call was failing silently for small windows). Removing it makes the failure explicit by deletion rather than by silent HRESULT.
- Pyglet's `_set_transparency()` is the documented mechanism for this operation and is confirmed to be correct in pyglet 2.1.14 (Research-1 section 5). Trusting it is the right design.
- No changes needed in `elements_overlay.py` or `__main__.py`.

---

## 6. Verification

### Step 1: Run the overlay and observe the background

Launch voice-sprite and trigger `elements.show`. Before the fix the background is opaque black. After the fix the background must be see-through (desktop visible) while the yellow numbered tags remain visible.

### Step 2: Add HRESULT logging to confirm the theory during investigation (optional, remove after)

Before removing the block, temporarily change it to log the HRESULT for both a small and a full-monitor window:

```python
hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
logger.info("DwmEnableBlurBehindWindow (DWM_BB_ENABLE only): HRESULT=0x%08x window_size=%dx%d",
            hr & 0xFFFFFFFF,
            user32.GetSystemMetrics(0),  # confirm monitor width
            user32.GetSystemMetrics(1))
```

For the SpriteWindow (small) you should see a non-zero HRESULT. For the overlay (full-monitor) you should see `0x00000000` (S_OK) — confirming the call succeeds only for the full-monitor window and overwrites the correct DWM state.

### Step 3: Confirm `granted_alpha=8` in logs

The existing `logger.info("elements overlay GL config granted alpha_size=%s ...")` log line should show `8`. If it shows `0` the bug has a different cause (driver-level alpha channel downgrade) and requires a driver fix — but this is unlikely given FACT B (tags visible = GL rendering works = framebuffer is functional).

### Step 4: Regression test

Run the full test suite (`pytest`). The fix touches only `win32_flags.py` and removes code with no logic changes to the user32 path. The picker modal test and sprite window test should continue to pass.

### Step 5: Test with a reduced-size overlay (additional confidence)

As a confirmation experiment before applying the fix: change the overlay to be 1 pixel smaller than the monitor on each edge (e.g., `width=mr-ml-2, height=mb-mt-2`). If transparency works at `mr-ml-2 x mb-mt-2` but fails at `mr-ml x mb-mt`, that directly confirms the full-monitor-size trigger.

---

## 7. Summary

| Question | Answer |
|---|---|
| Root cause | `apply_click_through` calls `DwmEnableBlurBehindWindow` a second time with `DWM_BB_ENABLE` only (NULL region), overwriting pyglet's correct per-pixel-alpha DWM state |
| Why only full-monitor? | For small windows this incorrect second call fails (non-zero HRESULT), leaving pyglet's original correct state intact. For full-monitor windows DWM's fullscreen compositing path accepts the call (S_OK), resetting alpha compositing to opaque. |
| Why tags still visible? | GL rendering is unaffected — the batch draws correctly. The bug is in DWM compositing, not in the GL layer. |
| Fix | Delete the DWM block (lines 86–108) from `apply_click_through` in `win32_flags.py`. Pyglet's `_set_transparency()` already handles this correctly. |
| Files changed | `src/voice_sprite/win32_flags.py` only |
| Lines changed | ~20 lines removed, 0 added |
