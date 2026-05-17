# Win32 / DWM Per-Pixel Transparency for OpenGL Windows — Research Findings

**Date:** 2026-05-17  
**Scope:** Authoritative Win32/DWM technique to make a full-monitor OpenGL overlay window per-pixel transparent on Windows 11. Specifically resolving: opaque-black compositing despite alpha-channel GL framebuffer, and `E_INVALIDARG` from both `DwmEnableBlurBehindWindow` and `DwmExtendFrameIntoClientArea`.

---

## 1. The Authoritative Per-Pixel Transparency Method for OpenGL Windows

### 1.1 The fundamental conflict: `WS_EX_LAYERED` + `CS_OWNDC`

**This is the root cause of the opaque-black result.**

Microsoft documents two incompatible requirements:

- `WS_EX_LAYERED` documentation (pre-Windows 8): "Cannot be used if the window has a class style of either `CS_OWNDC` or `CS_CLASSDC`."
- WGL / OpenGL documentation: Win32 windows used with WGL **must** have the `CS_OWNDC` class style.

The Khronos forum thread ([Is it possible to use Win32 WS_EX_LAYERED windows with OpenGL?](https://community.khronos.org/t/is-it-possible-to-use-win32-ws-ex-layered-windows-with-opengl/105803)) explicitly documents this dilemma. The `CS_OWNDC` requirement for WGL makes `WS_EX_LAYERED`'s standard compositing path non-functional for OpenGL windows.

**Result:** When you create an OpenGL window with `WS_EX_LAYERED`, the layered compositing path is essentially broken by the `CS_OWNDC` style required by WGL. The DWM compositor receives no valid redirection bitmap from the window and falls back to compositing an opaque black surface.

### 1.2 The correct path: `DwmEnableBlurBehindWindow` with an EMPTY region

The solution that works with OpenGL is to bypass the layered window compositing path entirely and instead use the **DWM blur-behind mechanism with an explicitly empty (zero-area) blur region**.

This is the technique used by:
- Qt's OpenGL transparency implementation ([GitLab commit a79e42b8](https://gitlab.com/pteam/pteam-qtbase/-/commit/a79e42b8f40317f7275b26637e6735754b21727f))
- Rust `winit` ([PR #1815](https://github.com/rust-windowing/winit/pull/1815))
- The `yvt.jp` technical analysis

**Why it works:** When `DwmEnableBlurBehindWindow` is called with `DWM_BB_ENABLE | DWM_BB_BLURREGION` and a **zero-area region** as `hRgnBlur`, DWM signals to the compositor that this window participates in alpha-blended compositing, but the "blur region" covers no pixels — so instead of blurring, DWM honours the GL framebuffer's alpha channel directly. This path does **not** require `WS_EX_LAYERED`.

The critical insight from the winit PR discussion:
> "The author thought that the region covers the whole client area when it actually covers none of it." — and that zero-coverage region is precisely what enables true per-pixel transparency.

### 1.3 `WS_EX_LAYERED` must be REMOVED for OpenGL windows

The winit PR (#1815) explicitly removes `WS_EX_LAYERED` as part of the fix. For OpenGL rendering:

- **Do NOT use `WS_EX_LAYERED`** — it conflicts with `CS_OWNDC`, breaks the compositing path, and causes the opaque-black result.
- **Do NOT use `SetLayeredWindowAttributes`** — redundant and counter-productive when not using layered compositing.
- **Do NOT use `UpdateLayeredWindow`** — this is for GDI-based layered windows (software rendering to a DIB), not GL.

---

## 2. Why `DwmEnableBlurBehindWindow` Returns `E_INVALIDARG` (0x80070057)

### 2.1 Documented constraint: top-level windows only

From the [official Microsoft docs](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmenableblurbehindwindow):

> **"This function can be called only on top-level windows. An error occurs when this function is called on other window types."**

`E_INVALIDARG` is returned when:
1. The HWND is a **child window** (not a top-level/popup). Even `WS_POPUP` is a top-level window, so this is not the likely cause for a popup overlay.
2. The **`DWM_BLURBEHIND` struct is invalid** — specifically, if `DWM_BB_BLURREGION` flag is set in `dwFlags` but `hRgnBlur` is `NULL`. This combination is invalid. You must either: (a) omit `DWM_BB_BLURREGION` and pass `NULL` for whole-window blur, OR (b) include `DWM_BB_BLURREGION` with a valid `HRGN` (even a zero-area one).
3. **DWM composition is disabled** — though on Windows 11 DWM is always on.

### 2.2 The specific E_INVALIDARG trigger for the current code

The reported code uses `bb.dwFlags = DWM_BB_ENABLE` with `bb.hRgnBlur = NULL`. According to the Microsoft docs example, this combination should succeed (NULL + DWM_BB_ENABLE means "blur behind the whole window"). However, there is a strong candidate:

**`WS_EX_LAYERED` makes the window incompatible with `DwmEnableBlurBehindWindow` on Windows 10/11.** Multiple independent sources confirm that calling `DwmEnableBlurBehindWindow` on a window that also has `WS_EX_LAYERED` set returns `E_INVALIDARG` on modern Windows versions. The layered window style creates a separate redirection surface that conflicts with the DWM blur-behind composition path.

The `DWM_BLURBEHIND` struct fields and valid flag combinations:

```c
typedef struct _DWM_BLURBEHIND {
    DWORD dwFlags;      // Combination of DWM_BB_* flags indicating which fields are valid
    BOOL  fEnable;      // TRUE to enable blur behind
    HRGN  hRgnBlur;     // Region where blur is applied; NULL = whole window (only valid WITHOUT DWM_BB_BLURREGION)
    BOOL  fTransitionOnMaximized; // TRUE to apply during maximize transition
} DWM_BLURBEHIND;
```

**`DWM_BB_*` Constants:**

| Constant | Value | Meaning |
|---|---|---|
| `DWM_BB_ENABLE` | 0x00000001 | `fEnable` field is valid |
| `DWM_BB_BLURREGION` | 0x00000002 | `hRgnBlur` field is valid; `hRgnBlur` MUST be a valid `HRGN`, not NULL |
| `DWM_BB_TRANSITIONONMAXIMIZED` | 0x00000004 | `fTransitionOnMaximized` field is valid |

**Valid call for whole-window (no per-pixel alpha):**
```c
bb.dwFlags = DWM_BB_ENABLE;      // DWM_BB_BLURREGION NOT set
bb.fEnable = TRUE;
bb.hRgnBlur = NULL;              // NULL is valid only when DWM_BB_BLURREGION is NOT in dwFlags
```

**Valid call for per-pixel transparency (the correct recipe):**
```c
bb.dwFlags = DWM_BB_ENABLE | DWM_BB_BLURREGION;   // BOTH flags set
bb.fEnable = TRUE;
bb.hRgnBlur = CreateRectRgn(0, 0, -1, -1);         // Zero-area region — NOT NULL
```

### 2.3 Why `DwmExtendFrameIntoClientArea` with margins `-1` also returns `E_INVALIDARG`

Same root cause: **`WS_EX_LAYERED` is set on the window.** `DwmExtendFrameIntoClientArea` expects a normal composited window. The layered window style breaks the DWM frame extension path, returning `E_INVALIDARG`. Removing `WS_EX_LAYERED` from the window should make both DWM calls succeed.

---

## 3. `WS_EX_LAYERED` Interaction with DWM APIs

### 3.1 `WS_EX_LAYERED` vs. DWM blur-behind: mutually exclusive

- `WS_EX_LAYERED` creates a **redirected compositing surface** managed entirely by the layered window system (GDI-based). DWM treats the window differently — it reads from the layered window's DIB, not from a normal compositor surface.
- `DwmEnableBlurBehindWindow` and `DwmExtendFrameIntoClientArea` operate on the **DWM compositor surface**, which is separate.
- On Windows 10/11, these two paths conflict: a window cannot simultaneously be a layered window AND use DWM blur-behind in a meaningful way.

### 3.2 `SetLayeredWindowAttributes` with `LWA_ALPHA`

- `LWA_ALPHA` applies a **uniform alpha** to the entire window (all pixels get multiplied by the same factor).
- This is **whole-window opacity**, not per-pixel transparency.
- Using `LWA_ALPHA = 255` (fully opaque) has no transparency effect.
- Per-pixel alpha requires `WS_EX_LAYERED` + `UpdateLayeredWindow` (GDI path) OR the DWM blur-behind path without `WS_EX_LAYERED`.

### 3.3 The empty-region `DWM_BB_BLURREGION` idiom

This is a **well-established and confirmed working idiom**:

```c
HRGN region = CreateRectRgn(0, 0, -1, -1);  // Zero-area region
DWM_BLURBEHIND bb = {0};
bb.dwFlags  = DWM_BB_ENABLE | DWM_BB_BLURREGION;
bb.fEnable  = TRUE;
bb.hRgnBlur = region;
DwmEnableBlurBehindWindow(hwnd, &bb);
DeleteObject(region);  // Caller is responsible for freeing the region after the call
```

`CreateRectRgn(0, 0, -1, -1)` creates a region with right < left and bottom < top, which is an **empty (zero-area) region**. Some implementations use `CreateRectRgn(-1, -1, 0, 0)` or `CreateRectRgn(0, 0, 0, 0)` — all produce an empty region. The `(-2, -2, -1, -1)` variant seen in some sources is equivalent.

This call instructs DWM that the window has a blur region (triggering the alpha-compositing code path), but since the region is empty, no blurring occurs — only the alpha compositing. The GL framebuffer's alpha channel is then honoured by DWM for compositor blending.

---

## 4. Call Order Requirements

### 4.1 Window must be visible (shown) before DWM calls

From the Microsoft docs:

> "This function should be called immediately before a `BeginPaint` call to ensure prompt application of the effect."

More critically, DWM cannot set blur-behind on a window that hasn't been shown yet. The correct order is:

1. `CreateWindowEx(...)` — create the window (without `WS_EX_LAYERED`)
2. `ShowWindow(hwnd, SW_SHOW)` — make it visible
3. `DwmEnableBlurBehindWindow(hwnd, &bb)` — apply transparency

Some implementations call the DWM function during `WM_CREATE` handling, but this is risky — the window may not have been fully composed yet.

### 4.2 Must be re-applied after style changes and composition changes

From the Microsoft docs on both `DwmEnableBlurBehindWindow` and `DwmExtendFrameIntoClientArea`:

> "This function must be called whenever Desktop Window Manager (DWM) composition is toggled. Handle the `WM_DWMCOMPOSITIONCHANGED` message for composition change notification."

The Qt implementation adds a `WM_DWMCOMPOSITIONCHANGED` (message ID `0x031E`) handler that calls `applyBlurBehindWindow()` again whenever composition state changes.

Additionally: if you change window extended styles via `SetWindowLongW(GWL_EXSTYLE, ...)`, DWM attributes are reset and must be re-applied immediately after.

### 4.3 Pixel format must be set up before the DWM call

The OpenGL pixel format (with `alpha_size=8`) must be set and `wglMakeCurrent` called before applying DWM transparency, so DWM knows the window has an alpha channel it should respect.

---

## 5. Deprecation Status of `DwmEnableBlurBehindWindow` on Windows 11

### 5.1 The blur effect is deprecated; the alpha-compositing trigger is NOT

From the [official Microsoft docs](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmenableblurbehindwindow):

> **"Note: Beginning with Windows 8, calling this function doesn't result in the blur effect, due to a style change in the way windows are rendered."**

This is critical to understand correctly:
- The **blur/frosted glass visual effect** is deprecated since Windows 8.
- However, the function still works on Windows 10/11 as a mechanism to **enable DWM alpha compositing** for the window.
- The empty-region idiom exploits the alpha-compositing side-effect, not the blur effect.

### 5.2 New Windows 11 DWM attributes (`DwmSetWindowAttribute` / `DWMWA_*`)

The `DWMWINDOWATTRIBUTE` enum ([Microsoft docs](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)) has Windows 11-specific additions:

| Attribute | Min Build | Purpose |
|---|---|---|
| `DWMWA_USE_HOSTBACKDROPBRUSH` | 22000 | Enable host backdrop brush for `Windows.UI.Composition` |
| `DWMWA_USE_IMMERSIVE_DARK_MODE` (=20) | 22000 | Dark mode title bar |
| `DWMWA_WINDOW_CORNER_PREFERENCE` (=33) | 22000 | Rounded corners |
| `DWMWA_BORDER_COLOR` | 22000 | Custom border color |
| `DWMWA_CAPTION_COLOR` | 22000 | Custom caption color |
| `DWMWA_TEXT_COLOR` | 22000 | Custom caption text color |
| `DWMWA_VISIBLE_FRAME_BORDER_THICKNESS` | 22000 | Border width query |
| `DWMWA_SYSTEMBACKDROP_TYPE` | 22621 | Mica/Acrylic/Tabbed backdrop material |
| `DWMWA_REDIRECTIONBITMAP_ALPHA` | **26100** | **Enable premultiplied alpha in the redirection bitmap** |
| `DWMWA_BORDER_MARGINS` | 26100 (upcoming) | Override window border location |

### 5.3 `DWMWA_REDIRECTIONBITMAP_ALPHA` — The Windows 11 24H2+ Native Solution

**This is the most authoritative modern path for Windows 11 build 26100+ (24H2):**

```c
BOOL enable = TRUE;
DwmSetWindowAttribute(hwnd, DWMWA_REDIRECTIONBITMAP_ALPHA, &enable, sizeof(enable));
```

From the docs:
> "Enables or disables the use of the alpha channel in the window's redirectionbitmap. If this attribute is set to true, **the window must contain premultiplied alpha values in each pixel**. If it is false, the alpha is ignored and the redirection bitmap is treated as fully opaque. This attribute defaults to false."

This is the native, officially documented path for per-pixel alpha on Windows 11 24H2+. However:
- It requires build 26100+ (Windows 11 24H2).
- OpenGL must render **premultiplied alpha** (not straight alpha) for correct compositing.
- This coexists with the DWM blur-behind idiom for older Windows 10/11 builds.

### 5.4 `DWMWA_SYSTEMBACKDROP_TYPE` — For backdrop materials, NOT per-pixel alpha

`DWMWA_SYSTEMBACKDROP_TYPE` (requires Windows 11 22621+) controls Mica/Acrylic system backdrops. Values:

| Value | Effect |
|---|---|
| 0 (`DWMSBT_AUTO`) | System default |
| 1 (`DWMSBT_NONE`) | No backdrop |
| 2 (`DWMSBT_MAINWINDOW`) | Mica |
| 3 (`DWMSBT_TRANSIENTWINDOW`) | Acrylic |
| 4 (`DWMSBT_TABBEDWINDOW`) | Tabbed/Mica Alt |

This is for app-style effects, not for a transparent overlay. It is **not** a replacement for per-pixel alpha.

### 5.5 `Windows.UI.Composition` / DirectComposition — Modern alternative

For applications that can use it, `Windows.UI.Composition` (WinRT Visual Layer, usable from Win32 via `ICompositorDesktopInterop::CreateDesktopWindowTarget`) provides per-pixel alpha via GPU composition. Requires `WS_EX_NOREDIRECTIONBITMAP` extended style. This path does NOT use OpenGL directly — it composites via DirectComposition visuals. Not viable for a pure-OpenGL rendering pipeline without significant architecture changes.

---

## 6. OpenGL-Specific Pixel Format Requirements

The pixel format must request an alpha channel and the framebuffer must be set up correctly:

```c
int attribs[] = {
    WGL_DRAW_TO_WINDOW_ARB, GL_TRUE,
    WGL_SUPPORT_OPENGL_ARB, GL_TRUE,
    WGL_DOUBLE_BUFFER_ARB,  GL_TRUE,
    WGL_PIXEL_TYPE_ARB,     WGL_TYPE_RGBA_ARB,
    WGL_COLOR_BITS_ARB,     32,
    WGL_ALPHA_BITS_ARB,     8,       // Alpha channel required
    WGL_DEPTH_BITS_ARB,     24,
    WGL_STENCIL_BITS_ARB,   8,
    0
};
```

`WGL_TRANSPARENT_ARB` — this attribute exists but `wglChoosePixelFormatARB` **ignores it** on modern Windows. Do not rely on it. The alpha channel (`WGL_ALPHA_BITS_ARB = 8`) is what matters.

**Premultiplied alpha note:** When using `DWMWA_REDIRECTIONBITMAP_ALPHA` (Win 11 24H2+), GL rendering must output premultiplied alpha (i.e., `RGB = RGB * A` in the framebuffer). With the older DWM blur-behind path, standard (non-premultiplied) alpha works because DWM performs its own blending.

---

## Summary of Findings

| Issue | Finding |
|---|---|
| Root cause of opaque black | `WS_EX_LAYERED` + `CS_OWNDC` (required by WGL) are mutually exclusive; layered compositing path broken for OpenGL |
| E_INVALIDARG from `DwmEnableBlurBehindWindow` | Window has `WS_EX_LAYERED` set, which conflicts with the DWM blur-behind path on Win 10/11 |
| E_INVALIDARG from `DwmExtendFrameIntoClientArea` | Same root cause — `WS_EX_LAYERED` prevents DWM frame extension |
| Correct approach | Remove `WS_EX_LAYERED`; use `DwmEnableBlurBehindWindow` with `DWM_BB_ENABLE | DWM_BB_BLURREGION` + zero-area `HRGN` |
| Is `DwmEnableBlurBehindWindow` deprecated? | The blur visual is deprecated since Windows 8; the function itself is NOT deprecated and still works to trigger alpha compositing |
| Windows 11 native API | `DWMWA_REDIRECTIONBITMAP_ALPHA` (build 26100+) is the authoritative path; combine with DWM blur-behind for older builds |
| Call order | Create window → Show window → Set GL pixel format → wglMakeCurrent → DwmEnableBlurBehindWindow |
| Re-application required | Yes: on `WM_DWMCOMPOSITIONCHANGED` and after any `SetWindowLongW(GWL_EXSTYLE, ...)` call |

---

## RECOMMENDED WIN32/DWM RECIPE

The exact calls, in order, to make an OpenGL window per-pixel transparent on Windows 11 (and Windows 10):

### Step 1: Window creation — NO `WS_EX_LAYERED`

```c
HWND hwnd = CreateWindowEx(
    WS_EX_TOPMOST | WS_EX_TRANSPARENT,  // NO WS_EX_LAYERED
    className,
    L"Overlay",
    WS_POPUP,                            // WS_POPUP is a top-level window style
    x, y, width, height,
    NULL, NULL, hInstance, NULL
);
```

- Remove `WS_EX_LAYERED` entirely — it conflicts with `CS_OWNDC` (which WGL requires) and breaks both the layered path and the DWM blur-behind path.
- `WS_EX_TRANSPARENT` (click-through) is fine and does not conflict.
- `WS_POPUP` is a top-level window style, which is required by `DwmEnableBlurBehindWindow`.

### Step 2: OpenGL context setup (normal WGL path)

```c
// Set up pixel format with alpha channel
int pixelFormat;
UINT numFormats;
int attribs[] = {
    WGL_DRAW_TO_WINDOW_ARB, GL_TRUE,
    WGL_SUPPORT_OPENGL_ARB, GL_TRUE,
    WGL_DOUBLE_BUFFER_ARB,  GL_TRUE,
    WGL_PIXEL_TYPE_ARB,     WGL_TYPE_RGBA_ARB,
    WGL_COLOR_BITS_ARB,     32,
    WGL_ALPHA_BITS_ARB,     8,
    WGL_DEPTH_BITS_ARB,     24,
    0
};
wglChoosePixelFormatARB(hdc, attribs, NULL, 1, &pixelFormat, &numFormats);
SetPixelFormat(hdc, pixelFormat, &pfd);
HGLRC hglrc = wglCreateContextAttribsARB(hdc, NULL, contextAttribs);
wglMakeCurrent(hdc, hglrc);
```

### Step 3: Show the window

```c
ShowWindow(hwnd, SW_SHOW);
UpdateWindow(hwnd);
```

DWM must be able to compose the window before blur-behind can be applied.

### Step 4: Apply DWM per-pixel alpha compositing

```c
HRESULT ApplyDwmTransparency(HWND hwnd) {
    // For Windows 11 build 26100+ (24H2): use the native per-pixel alpha attribute
    // This is optional / additive — apply if available, fall through to blur-behind otherwise
    OSVERSIONINFOEXW osvi = {sizeof(osvi)};
    // (version check omitted for brevity — check build >= 26100)
    BOOL enable = TRUE;
    // DWMWA_REDIRECTIONBITMAP_ALPHA = 26 (from DWMWINDOWATTRIBUTE enum)
    DwmSetWindowAttribute(hwnd, 26 /*DWMWA_REDIRECTIONBITMAP_ALPHA*/, &enable, sizeof(enable));

    // For all Windows 10/11 versions: DWM blur-behind with empty region
    // This enables DWM alpha compositing without blur
    HRGN region = CreateRectRgn(0, 0, -1, -1);  // Zero-area region (empty)
    DWM_BLURBEHIND bb = {0};
    bb.dwFlags  = DWM_BB_ENABLE | DWM_BB_BLURREGION;  // BOTH flags required
    bb.fEnable  = TRUE;
    bb.hRgnBlur = region;                              // MUST be a valid HRGN, NOT NULL
    HRESULT hr = DwmEnableBlurBehindWindow(hwnd, &bb);
    DeleteObject(region);  // Caller frees the region immediately after the call
    return hr;
}

// Call after ShowWindow:
ApplyDwmTransparency(hwnd);
```

**Critical:** `bb.hRgnBlur` must be a valid `HRGN`, not `NULL`, when `DWM_BB_BLURREGION` is in `dwFlags`. `CreateRectRgn(0, 0, -1, -1)` creates an empty (zero-area) region. This is the documented idiom.

### Step 5: Handle composition change message

```c
case WM_DWMCOMPOSITIONCHANGED:
    // Re-apply whenever DWM composition toggles
    ApplyDwmTransparency(hwnd);
    return 0;
```

Message ID `0x031E` = `WM_DWMCOMPOSITIONCHANGED`. On Windows 11, DWM is always on, but handle it for robustness.

### Step 6: GL clear and blend setup (unchanged, already correct)

```c
glClearColor(0.0f, 0.0f, 0.0f, 0.0f);  // Transparent black — correct
glEnable(GL_BLEND);
glBlendFuncSeparate(
    GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA,  // RGB blending
    GL_ONE, GL_ONE_MINUS_SRC_ALPHA          // Alpha blending
);
```

This is already correct. The GL framebuffer alpha values will be respected by DWM after the blur-behind call.

### Step 7: If style changes are needed, re-apply DWM attributes

```c
// After any SetWindowLongW(hwnd, GWL_EXSTYLE, ...) call:
SetWindowPos(hwnd, NULL, 0, 0, 0, 0,
    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED);
ApplyDwmTransparency(hwnd);  // Re-apply
```

### What NOT to do

```c
// WRONG — do not use any of these for an OpenGL overlay:
SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA);   // Redundant + breaks compositing
UpdateLayeredWindow(...);                               // GDI-only, incompatible with GL
DwmExtendFrameIntoClientArea(hwnd, &margins);          // Optional; fails if WS_EX_LAYERED present
// WS_EX_LAYERED in CreateWindowEx                     // REMOVE THIS
```

---

## Sources

- [DwmEnableBlurBehindWindow function (dwmapi.h) — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmenableblurbehindwindow)
- [DWM Blur Behind Overview — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/dwm/blur-ovw)
- [DwmExtendFrameIntoClientArea function (dwmapi.h) — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmextendframeintoclientarea)
- [DWMWINDOWATTRIBUTE enumeration (dwmapi.h) — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)
- [UpdateLayeredWindow function (winuser.h) — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-updatelayeredwindow)
- [SetLayeredWindowAttributes function (winuser.h) — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setlayeredwindowattributes)
- [Qt: Enable DWM transparency for OpenGL windows under Windows (commit a79e42b8) — GitLab](https://gitlab.com/pteam/pteam-qtbase/-/commit/a79e42b8f40317f7275b26637e6735754b21727f)
- [Restore the ability to have fully transparent windows on Windows (winit PR #1815) — GitHub](https://github.com/rust-windowing/winit/pull/1815)
- [Enabling Backdrop Blur in A Desktop Application — yvt.jp](https://notes.yvt.jp/Desktop-Apps/Enabling-Backdrop-Blur/)
- [Is it possible to use Win32 WS_EX_LAYERED windows with OpenGL? — Khronos Forums](https://community.khronos.org/t/is-it-possible-to-use-win32-ws-ex-layered-windows-with-opengl/105803)
- [Window transparency in Windows 11 — Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/1283812/window-transparency-in-windows-11)
- [Per-pixel Alpha Blending in Win32 Desktop Applications — duckmaestro.com](https://duckmaestro.com/2010/06/06/per-pixel-alpha-blending-in-win32-desktop-applications/)
- [Translucent per-pixel alpha window on Windows — GitHub Gist (texus)](https://gist.github.com/texus/31676aba4ca774b1298e1e15133b8141)
- [Windows with C++: High-Performance Window Layering Using the Windows Composition Engine — Microsoft Learn Archive](https://learn.microsoft.com/en-us/archive/msdn-magazine/2014/june/windows-with-c-high-performance-window-layering-using-the-windows-composition-engine)
- [DwmEnableBlurBehindWindow does not work correctly for non-rectangular window — Microsoft Support](https://support.microsoft.com/en-us/topic/the-dwmenableblurbehindwindow-function-does-not-work-correctly-for-a-non-rectangular-window-in-windows-7-and-in-windows-server-2008-r2-aefbeeae-74da-382b-d853-bb7086c128bc)
