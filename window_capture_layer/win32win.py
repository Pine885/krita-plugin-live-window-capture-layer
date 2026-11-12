"""List Windows windows through user32, without external tools.

Krita-free: ctypes only. Lazy library loading keeps the pure helpers
unit-testable on any platform; the Win32 calls themselves need Windows.

Window ids are ``HWND`` handles. Size is the client area, matching the captured
pixels.
"""

import ctypes
import sys
import time
from collections import namedtuple
from ctypes import wintypes

from . import debug

log = debug.logger

PICK_TIMEOUT_S = 30
GA_ROOT = 2
VK_LBUTTON = 0x01

DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

_WINFUNC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_EnumProc = _WINFUNC(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

WindowInfo = namedtuple("WindowInfo", "wid title width height")

_DPI_NAMES = {0: "unaware", 1: "system aware", 2: "per-monitor aware"}

_user32 = None
_dwm = None


class Unavailable(RuntimeError):
    """The Win32 window lookup is not usable here."""


def _load():
    global _user32, _dwm
    if _user32 is not None:
        return _user32
    if sys.platform != "win32":
        raise Unavailable("Win32 window lookup needs Windows")

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError as exc:
        raise Unavailable("cannot load user32: %s" % exc)

    user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long

    _dwm = _load_dwm()
    _user32 = user32
    log.info("win32 window list ready: %s", environment_note())
    return _user32


def _load_dwm():
    """Load dwmapi for the cloaked check; None when unavailable."""
    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwm.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    except OSError as exc:
        log.debug("dwmapi unavailable, cloak check off: %s", exc)
        return None
    return dwm


def environment_note():
    """One-line platform summary for the log. Safe to call anywhere."""
    try:
        version = sys.getwindowsversion()
        version_text = "%d.%d.%d" % (version.major, version.minor, version.build)
    except Exception:
        version_text = "unknown"
    return "windows %s, dpi %s" % (version_text, _dpi_awareness())


def _dpi_awareness():
    """Best-effort process DPI awareness name; never raises."""
    try:
        shcore = ctypes.windll.shcore
        value = ctypes.c_int(0)
        if shcore.GetProcessDpiAwareness(0, ctypes.byref(value)) == 0:
            return _DPI_NAMES.get(value.value, str(value.value))
    except Exception as exc:
        return "unknown (%s)" % exc
    return "unknown"


def should_list(cloaked, exstyle):
    """False for DWM-cloaked windows and tool windows with no taskbar entry."""
    if cloaked:
        return False
    if exstyle & WS_EX_TOOLWINDOW and not exstyle & WS_EX_APPWINDOW:
        return False
    return True


def to_infos(rows):
    """Build sorted WindowInfo entries from (wid, title, width, height) rows.

    Keeps the largest window first and drops duplicates and empty rectangles.
    """
    infos = []
    seen = set()
    for wid, title, width, height in rows:
        if wid is None:
            continue
        wid = int(wid)
        if wid in seen or width <= 0 or height <= 0:
            continue
        seen.add(wid)
        infos.append(WindowInfo(wid, title or "", width, height))
    infos.sort(key=lambda info: info.width * info.height, reverse=True)
    return infos


def matching(infos, title):
    """Entries whose title contains *title*, case-insensitively."""
    needle = (title or "").lower()
    return [info for info in infos if needle in info.title.lower()]


def choose_best(infos, title):
    """Return the id of the largest title match, or None."""
    best = None
    best_area = 0
    for info in matching(infos, title):
        area = info.width * info.height
        if area > best_area:
            best, best_area = info.wid, area
    return best


def enumerate_windows():
    """Return (wid, title, client width, client height) rows for open windows."""
    user32 = _load()
    rows = []

    @_EnumProc
    def _callback(hwnd, _lparam):
        try:
            _collect(user32, hwnd, rows)
        except Exception as exc:
            log.debug("window enumeration skipped one window: %s", exc)
        return True

    if not user32.EnumWindows(_callback, 0):
        log.warning("EnumWindows failed")
    log.debug("enumerated %d visible window(s)", len(rows))
    return rows


def _is_cloaked(user32, hwnd):
    """True when DWM reports the window cloaked; never raises."""
    if _dwm is None:
        return False
    value = wintypes.DWORD(0)
    try:
        result = _dwm.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception as exc:
        log.debug("DwmGetWindowAttribute failed: %s", exc)
        return False
    if result != 0:
        return False
    return value.value != 0


def _exstyle(user32, hwnd):
    try:
        return int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    except Exception:
        return 0


def _collect(user32, hwnd, rows):
    if not hwnd:
        return
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return
    if not should_list(_is_cloaked(user32, hwnd), _exstyle(user32, hwnd)):
        return
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return
    rows.append((int(hwnd), buffer.value, width, height))


def list_windows():
    """Return every visible window, largest first. Empty when unavailable."""
    try:
        rows = enumerate_windows()
    except Unavailable as exc:
        log.debug("window list unavailable: %s", exc)
        return []
    return to_infos(rows)


def find_windows(title):
    """Return all visible window ids whose title contains *title*."""
    return [info.wid for info in matching(list_windows(), title)]


def window_name(wid):
    user32 = _load()
    hwnd = wintypes.HWND(int(wid))
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def window_size(wid):
    user32 = _load()
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(int(wid)), ctypes.byref(rect)):
        return 0, 0
    return rect.right - rect.left, rect.bottom - rect.top


def window_info(wid):
    wid = int(wid)
    for info in list_windows():
        if info.wid == wid:
            return info
    width, height = window_size(wid)
    return WindowInfo(wid, window_name(wid), width, height)


def best_window(title):
    """Pick the largest matching window; toolbar/utility windows are small."""
    wid = choose_best(list_windows(), title)
    log.debug("best_window(%r) -> %s", title, wid)
    return wid


def click_pick(timeout=PICK_TIMEOUT_S):
    """Block up to *timeout* seconds, then return the clicked root window id."""
    try:
        user32 = _load()
    except Unavailable:
        return None

    if user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
        log.debug("pick cancelled: left button held before the pick started")
        return None

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
            point = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return None
            hwnd = user32.WindowFromPoint(point)
            root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else 0
            wid = int(root or hwnd or 0)
            if wid:
                log.info(
                    "click_pick chose %s (%r)", wid, _title_of(user32, wid)
                )
            return wid or None
        time.sleep(0.02)
    return None


def _title_of(user32, wid):
    try:
        hwnd = wintypes.HWND(int(wid))
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except Exception:
        return ""
