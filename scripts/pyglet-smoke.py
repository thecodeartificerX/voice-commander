"""Pyglet smoke test: open a 256x256 borderless window, draw a red square.

Usage: python scripts/pyglet-smoke.py
"""
import ctypes
import platform

import pyglet

win = pyglet.window.Window(
    256, 256,
    style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
    vsync=False,
)

# Position bottom-right
screen = pyglet.canvas.get_display().get_default_screen()
win.set_location(screen.width - 256 - 16, 16)

if platform.system() == "Windows":
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    HWND_TOPMOST = -1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    LWA_ALPHA = 0x00000002

    hwnd = win.canvas.hwnd if hasattr(win.canvas, "hwnd") else getattr(win, "_hwnd", None)
    if hwnd:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
        print(f"Win32 flags applied to hwnd={hwnd}")
    else:
        print("WARNING: Could not get HWND")


@win.event
def on_draw():
    win.clear()
    batch = pyglet.graphics.Batch()
    pyglet.shapes.Rectangle(64, 64, 128, 128, color=(255, 0, 0), batch=batch)
    batch.draw()


print("Smoke test window open. Close to exit.")
pyglet.app.run()
