"""Grab Windows window pixels through GDI ``PrintWindow``.

Krita- and Qt-free: ctypes on user32/gdi32 only. The libraries load lazily, so
this module imports on every platform and its pure helpers stay unit-testable.

``PrintWindow`` with ``PW_RENDERFULLCONTENT`` renders a window into a memory DC
even while Krita covers it. Windows that refuse to render (some
hardware-accelerated clients) fall back to a BitBlt of the window DC, which
only sees on-screen pixels.

Output is BGRA, matching Krita's integer RGBA memory order, with alpha forced
to 0xff.
"""

import ctypes
import sys
import time
from ctypes import wintypes

from . import debug
from .limits import MAX_PIXELS

log = debug.logger


class GrabError(RuntimeError):
    """A Win32 call failed while grabbing."""


class BlackFrame(GrabError):
    """The window rendered entirely black, so the frame is unusable."""


class Unavailable(RuntimeError):
    """The Win32 capture path is not usable here."""


BI_RGB = 0
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020

_DPI_NAMES = {0: "unaware", 1: "system aware", 2: "per-monitor aware"}

# Windows whose first successful grab was already logged at INFO.
_LOGGED_WIDS = set()


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class _Libraries:
    def __init__(self, user32, gdi32):
        self.user32 = user32
        self.gdi32 = gdi32


_libraries = None


def _load():
    """Bind user32/gdi32 once. Raises Unavailable when not on Windows."""
    global _libraries
    if _libraries is not None:
        return _libraries.user32, _libraries.gdi32
    if sys.platform != "win32":
        raise Unavailable("Win32 capture needs Windows")

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    except OSError as exc:
        raise Unavailable("cannot load user32/gdi32: %s" % exc)

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL

    _libraries = _Libraries(user32, gdi32)
    log.info("win32 grab ready: %s", environment_note())
    return user32, gdi32


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


def _class_name(user32, hwnd):
    try:
        buffer = ctypes.create_unicode_buffer(256)
        length = user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value if length else ""
    except Exception:
        return ""


def is_black_bgra(raw):
    """True when every pixel's red, green and blue bytes are zero.

    Alpha is forced to 0xff by the grab path, so only colour bytes can be zero.
    """
    if not raw or len(raw) % 4:
        return False
    return raw.count(b"\x00") == (len(raw) // 4) * 3


def crop_bgra(raw, stride, width, height, x, y, crop_w, crop_h):
    """Copy a BGRA rectangle out of a top-down 32bpp buffer.

    Alpha is forced to 0xff. Returns None for out-of-range or short input.
    """
    if crop_w <= 0 or crop_h <= 0:
        return None
    if x < 0 or y < 0 or x + crop_w > width or y + crop_h > height:
        return None
    if stride < width * 4 or len(raw) < stride * height:
        return None

    out = bytearray(crop_w * crop_h * 4)
    row_bytes = crop_w * 4
    for row in range(crop_h):
        start = (y + row) * stride + x * 4
        out[row * row_bytes : (row + 1) * row_bytes] = raw[start : start + row_bytes]
    out[3::4] = b"\xff" * (crop_w * crop_h)
    return bytes(out)


def _rect(user32, hwnd):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _client_box(user32, hwnd):
    """Return (width, height, offset_x, offset_y) of the client area."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    point = wintypes.POINT()
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
        return None
    window = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(window)):
        return None
    return (
        rect.right - rect.left,
        rect.bottom - rect.top,
        point.x - window.left,
        point.y - window.top,
    )


def _capture_window(user32, gdi32, hwnd, width, height):
    """Render the whole window into BGRA bytes (title bar included).

    Returns ``(raw, method)`` where *method* is ``"printwindow"`` or
    ``"bitblt"``.
    """
    memory_dc = gdi32.CreateCompatibleDC(None)
    if not memory_dc:
        raise GrabError("CreateCompatibleDC failed")

    bitmap = None
    raw = b""
    method = "printwindow"
    try:
        header = _BITMAPINFO()
        header.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.bmiHeader.biWidth = width
        header.bmiHeader.biHeight = -height  # top-down rows
        header.bmiHeader.biPlanes = 1
        header.bmiHeader.biBitCount = 32
        header.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            memory_dc,
            ctypes.byref(header),
            DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        if not bitmap or not bits:
            raise GrabError("CreateDIBSection failed")

        previous = gdi32.SelectObject(memory_dc, bitmap)
        try:
            rendered = user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
            if rendered:
                method = "printwindow"
            else:
                log.debug("PrintWindow refused %s, using BitBlt", hwnd)
                method = "bitblt"
                source = user32.GetWindowDC(hwnd)
                if not source:
                    raise GrabError(
                        "GetWindowDC failed after PrintWindow was refused"
                    )
                try:
                    if not gdi32.BitBlt(
                        memory_dc, 0, 0, width, height, source, 0, 0, SRCCOPY
                    ):
                        raise GrabError(
                            "BitBlt failed after PrintWindow was refused"
                        )
                finally:
                    user32.ReleaseDC(hwnd, source)
            raw = ctypes.string_at(bits, width * height * 4)
        finally:
            gdi32.SelectObject(memory_dc, previous)
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)

    out = bytearray(raw)
    out[3::4] = b"\xff" * (width * height)
    return bytes(out), method


def grab(wid, max_pixels=MAX_PIXELS):
    """Return ``(width, height, bgra)`` for a window, raising on failure."""
    if not wid:
        raise GrabError("no window id")

    started = time.monotonic()
    user32, gdi32 = _load()
    hwnd = wintypes.HWND(int(wid))

    bounds = _rect(user32, hwnd)
    if bounds is None:
        raise GrabError("GetWindowRect failed")
    _, _, width, height = bounds
    if width <= 0 or height <= 0:
        raise GrabError("window has no area")
    if width * height > max_pixels:
        raise GrabError("window too large (%dx%d)" % (width, height))

    raw, method = _capture_window(user32, gdi32, hwnd, width, height)

    box = _client_box(user32, hwnd)
    out_w, out_h, pixels = width, height, raw
    if box is not None:
        client_w, client_h, offset_x, offset_y = box
        if client_w > 0 and client_h > 0:
            cropped = crop_bgra(
                raw, width * 4, width, height, offset_x, offset_y,
                client_w, client_h,
            )
            if cropped is not None:
                out_w, out_h, pixels = client_w, client_h, cropped

    black = is_black_bgra(pixels)
    if int(wid) not in _LOGGED_WIDS:
        _LOGGED_WIDS.add(int(wid))
        log.info(
            "grab %s first frame: class=%r window=%dx%d client=%s "
            "method=%s black=%s",
            wid,
            _class_name(user32, hwnd),
            width,
            height,
            box,
            method,
            black,
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.debug(
        "grabbed window %s in %dms (%dx%d, %s)", wid, elapsed_ms,
        out_w, out_h, method,
    )
    if black:
        raise BlackFrame("window rendered black")
    return out_w, out_h, pixels


def available():
    """True when the Win32 capture path can be attempted here."""
    try:
        _load()
    except Exception:
        return False
    return True


def status():
    """Return ``(ok, detail)`` for the native Win32 capture path.

    Composition is informational: ``PrintWindow`` still works with DWM off for
    most windows, so it never gates the native path.
    """
    try:
        _load()
    except Unavailable as exc:
        return False, str(exc)
    except Exception as exc:
        return False, "cannot load user32/gdi32: %s" % exc

    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        enabled = ctypes.c_int(0)
        result = dwm.DwmIsCompositionEnabled(ctypes.byref(enabled))
    except Exception as exc:
        return True, "GDI PrintWindow ready; dwmapi unavailable (%s)" % exc
    if result != 0:
        return True, "GDI PrintWindow ready; DWM query failed (%d)" % result
    if not enabled.value:
        return True, "GDI PrintWindow ready; DWM composition off"
    return True, "GDI PrintWindow ready; DWM composition on"
