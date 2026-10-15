"""The borderless window, done so Windows still treats it as a window.

Tk's usual way to drop the title bar, overrideredirect(True), also drops the
taskbar button, Alt+Tab and minimize on Windows. Instead the window stays an
ordinary top-level and only its caption and sizing border are stripped with
SetWindowLong. Dragging hands off to Windows' own move loop (so Aero Snap
works), and maximize fills the monitor's work area, leaving the taskbar
visible.

Everything here is a no-op off Windows, where the native frame stays.
"""

from __future__ import annotations

import ctypes
import sys
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    ctypes.windll.dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                          ctypes.c_void_p, wintypes.DWORD]
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x20
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
MONITOR_DEFAULTTONEAREST = 2
SW_RESTORE = 9
ERROR_ALREADY_EXISTS = 183


def enable_dpi_awareness() -> None:
    """Crisp text on scaled displays. Must run before the Tk root exists."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # system DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_app_id(app_id: str) -> None:
    """Own taskbar group and icon, instead of being lumped under Python."""
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass


def _hwnd(root) -> int:
    # winfo_id() is Tk's inner child; the real top-level is its parent.
    return user32.GetParent(root.winfo_id())


def strip_frame(root, x: int, y: int, w: int, h: int) -> None:
    """Drop the caption and sizing border, then place the window at x, y, w, h.

    From here on, move and size the window only with set_rect(). Tk keeps
    its own idea of the frame it created the window with, so root.geometry()
    after this adds borders that no longer exist -- the window grew 16px on
    every maximize/restore before this rule.
    """
    if not IS_WINDOWS:
        root.geometry(f"{w}x{h}+{x}+{y}")
        return
    root.update_idletasks()
    hwnd = _hwnd(root)
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~(WS_CAPTION | WS_THICKFRAME))
    user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_FRAMECHANGED)


def get_rect(root) -> Tuple[int, int, int, int]:
    """(x, y, width, height) of the whole window."""
    if not IS_WINDOWS:
        return root.winfo_x(), root.winfo_y(), root.winfo_width(), root.winfo_height()
    r = wintypes.RECT()
    user32.GetWindowRect(_hwnd(root), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def set_rect(root, x: int, y: int, w: int, h: int) -> None:
    if not IS_WINDOWS:
        root.geometry(f"{w}x{h}+{x}+{y}")
        return
    user32.SetWindowPos(_hwnd(root), None, x, y, w, h, SWP_NOZORDER)


def round_corners(root, border_color: Optional[str] = None) -> None:
    """Windows 11: rounded corners and a border in our color. Silently nothing
    on Windows 10, where the window keeps square corners and its own outline."""
    if not IS_WINDOWS:
        return
    try:
        dwm = ctypes.windll.dwmapi
        hwnd = _hwnd(root)
        pref = ctypes.c_int(2)                                   # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        if border_color:
            r, g, b = (int(border_color[i:i + 2], 16) for i in (1, 3, 5))
            colorref = ctypes.c_uint(r | (g << 8) | (b << 16))
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(colorref), ctypes.sizeof(colorref))
    except Exception:
        pass


def start_drag(root) -> None:
    """Hand the drag to Windows, as if the user grabbed a real title bar."""
    if not IS_WINDOWS:
        return
    hwnd = _hwnd(root)
    user32.ReleaseCapture()
    user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)


def work_area(root) -> Optional[Tuple[int, int, int, int]]:
    """(x, y, width, height) of the usable area of the monitor the window is on."""
    if not IS_WINDOWS:
        return None
    monitor = user32.MonitorFromWindow(_hwnd(root), MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    r = info.rcWork
    return r.left, r.top, r.right - r.left, r.bottom - r.top


class SingleInstance:
    """A named mutex: one window per install. A second launch brings the first
    one forward instead of fighting it over the Chrome profile. Held for the
    life of the process."""

    def __init__(self, name: str) -> None:
        self.handle = None
        self.already_running = False
        if not IS_WINDOWS:
            return
        self.handle = kernel32.CreateMutexW(None, False, name)
        self.already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def window_titles(pid: int) -> List[str]:
    """Titles of the visible top-level windows process pid owns.

    For Chrome that is "<active tab's title> - Google Chrome": a way to follow
    what the login window shows without touching the browser at all.
    """
    if not IS_WINDOWS:
        return []
    titles: List[str] = []

    @WNDENUMPROC
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(visit, 0)
    return titles


def allow_foreground_handoff() -> None:
    """Let the next window another process opens come to the front.

    Windows keeps background processes from stealing focus, and the login
    Chrome is started by a hidden worker, not by this window -- without this
    it can open behind IXM-VAS and only flash in the taskbar.
    """
    if IS_WINDOWS:
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)      # ASFW_ANY
        except Exception:
            pass


def focus_existing(title: str) -> None:
    if not IS_WINDOWS:
        return
    # By Tk's window class too, so an Explorer window on a folder of the same
    # name is never the one brought forward.
    hwnd = user32.FindWindowW("TkTopLevel", title)
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
