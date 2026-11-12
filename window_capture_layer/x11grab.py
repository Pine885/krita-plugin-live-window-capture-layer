"""Capture X11 window pixels through the Composite extension.

Krita- and Qt-free: ctypes on libX11 and libXcomposite only.

Without a compositing manager, the X server keeps no backing store for an
occluded window, so reading the window drawable (and Qt's ``grabWindow``)
returns whatever is on screen at those coordinates. Redirecting the window
with XComposite gives the server a private pixmap even when no compositor
runs, which can then be read with ``XGetImage``.

The result is BGRA byte order to match Krita's integer RGBA memory order,
with the alpha byte forced to 0xff.

A single X display connection is used; multi-desktop / multi-screen support is
future work (see devNotes.md).
"""

import ctypes
import ctypes.util
import threading

from .limits import MAX_PIXELS


class CompositeUnavailable(RuntimeError):
    """XComposite or an X display is not usable here."""


class GrabError(RuntimeError):
    """An X request failed while grabbing."""


AUTOMATIC = 0
ZPIXMAP = 2
LSB_FIRST = 0
ALL_PLANES = 0xFFFFFFFFFFFFFFFF

_STANDARD_MASKS = (0xFF0000, 0xFF00, 0xFF)

_lock = threading.Lock()
_libraries = None
_display = None


class _XImage(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
    ]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


_ERROR_COUNT = 0
_LAST_ERROR = 0

_XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


@_XErrorHandler
def _on_x_error(_display, event):
    """Swallow X errors so a bad drawable cannot terminate Krita."""
    global _ERROR_COUNT, _LAST_ERROR
    _ERROR_COUNT += 1
    try:
        if event:
            _LAST_ERROR = int(
                ctypes.cast(event, ctypes.POINTER(_XErrorEvent)).contents.error_code
            )
    except Exception:
        _LAST_ERROR = 0
    return 0


class _Libraries:
    def __init__(self, x11, composite):
        self.x11 = x11
        self.composite = composite


def _load():
    """Bind the X libraries once. Raises CompositeUnavailable when missing."""
    global _libraries
    if _libraries is not None:
        return _libraries

    x11_name = ctypes.util.find_library("X11")
    composite_name = ctypes.util.find_library("Xcomposite")
    if not x11_name or not composite_name:
        raise CompositeUnavailable("libX11 or libXcomposite not found")

    try:
        x11 = ctypes.CDLL(x11_name)
        composite = ctypes.CDLL(composite_name)
    except OSError as exc:
        raise CompositeUnavailable("cannot load X libraries: %s" % exc)

    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XGetGeometry.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
    ]
    x11.XGetGeometry.restype = ctypes.c_int
    x11.XGetImage.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_ulong,
        ctypes.c_int,
    ]
    x11.XGetImage.restype = ctypes.c_void_p
    x11.XFreePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    x11.XSetErrorHandler.restype = ctypes.c_void_p
    x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
    x11.XDefaultScreen.restype = ctypes.c_int
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XGetSelectionOwner.restype = ctypes.c_ulong

    composite.XCompositeQueryExtension.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    composite.XCompositeQueryExtension.restype = ctypes.c_int
    composite.XCompositeRedirectWindow.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
    ]
    composite.XCompositeUnredirectWindow.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
    ]
    composite.XCompositeNameWindowPixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    composite.XCompositeNameWindowPixmap.restype = ctypes.c_ulong

    _libraries = _Libraries(x11, composite)
    return _libraries


def _open_display(lib):
    global _display
    if _display:
        return _display
    handle = lib.x11.XOpenDisplay(None)
    if not handle:
        raise CompositeUnavailable("XOpenDisplay failed")
    _display = handle
    return _display


def convert_ximage(
    raw,
    width,
    height,
    bytes_per_line,
    bits_per_pixel,
    byte_order,
    red_mask,
    green_mask,
    blue_mask,
):
    """Convert an XImage buffer to BGRA, or return None when unsupported.

    Only the usual little-endian 32bpp TrueColor layout is accepted; anything
    else falls back to the Qt grab so this cannot silently emit bad pixels.
    """
    if width <= 0 or height <= 0:
        return None
    if bits_per_pixel != 32 or byte_order != LSB_FIRST:
        return None
    masks = (red_mask, green_mask, blue_mask)
    # Some servers leave the masks zero on XGetImage results; only reject masks
    # that are present and non-standard.
    if masks != (0, 0, 0) and masks != _STANDARD_MASKS:
        return None
    stride = width * 4
    if bytes_per_line < stride:
        return None
    if len(raw) < bytes_per_line * height:
        return None

    if bytes_per_line == stride:
        out = bytearray(raw[: stride * height])
    else:
        out = bytearray(stride * height)
        for row in range(height):
            start = row * bytes_per_line
            out[row * stride : (row + 1) * stride] = raw[start : start + stride]

    out[3::4] = b"\xff" * (width * height)
    return bytes(out)


def _read_pixels(lib, display, drawable, width, height):
    image_pointer = lib.x11.XGetImage(
        display, drawable, 0, 0, width, height, ALL_PLANES, ZPIXMAP
    )
    if not image_pointer:
        raise GrabError("XGetImage returned no image")

    image = ctypes.cast(image_pointer, ctypes.POINTER(_XImage)).contents
    try:
        raw = ctypes.string_at(image.data, image.bytes_per_line * image.height)
        pixels = convert_ximage(
            raw,
            image.width,
            image.height,
            image.bytes_per_line,
            image.bits_per_pixel,
            image.byte_order,
            image.red_mask,
            image.green_mask,
            image.blue_mask,
        )
    finally:
        if image.data:
            lib.x11.XFree(image.data)
        lib.x11.XFree(image_pointer)

    if pixels is None:
        raise GrabError("unsupported XImage layout")
    return pixels


def _grab_locked(lib, display, wid, max_pixels):
    global _ERROR_COUNT, _LAST_ERROR

    previous_handler = lib.x11.XSetErrorHandler(
        ctypes.cast(_on_x_error, ctypes.c_void_p)
    )
    _ERROR_COUNT = 0
    _LAST_ERROR = 0
    redirected = False
    pixmap = 0
    try:
        # Always try to redirect. With a compositing manager the window may
        # already be redirected, in which case this returns BadAccess and the
        # name-pixmap call below still succeeds.
        lib.composite.XCompositeRedirectWindow(display, wid, AUTOMATIC)
        lib.x11.XSync(display, 0)
        if not _ERROR_COUNT:
            redirected = True
        _ERROR_COUNT = 0
        _LAST_ERROR = 0

        pixmap = lib.composite.XCompositeNameWindowPixmap(display, wid)
        lib.x11.XSync(display, 0)
        if _ERROR_COUNT or not pixmap:
            raise GrabError(
                "XCompositeNameWindowPixmap failed (error %d)" % _LAST_ERROR
            )

        root = ctypes.c_ulong()
        x = ctypes.c_int()
        y = ctypes.c_int()
        width = ctypes.c_uint()
        height = ctypes.c_uint()
        border = ctypes.c_uint()
        depth = ctypes.c_uint()
        if not lib.x11.XGetGeometry(
            display, pixmap, ctypes.byref(root), ctypes.byref(x), ctypes.byref(y),
            ctypes.byref(width), ctypes.byref(height), ctypes.byref(border),
            ctypes.byref(depth),
        ):
            raise GrabError("XGetGeometry failed")
        if width.value <= 0 or height.value <= 0:
            raise GrabError("window has no area")
        if width.value * height.value > max_pixels:
            raise GrabError("window too large (%dx%d)" % (width.value, height.value))

        pixels = _read_pixels(
            lib, display, pixmap, width.value, height.value
        )
        return width.value, height.value, pixels
    finally:
        if pixmap:
            lib.x11.XFreePixmap(display, pixmap)
        if redirected:
            lib.composite.XCompositeUnredirectWindow(display, wid, AUTOMATIC)
        lib.x11.XSync(display, 0)
        lib.x11.XSetErrorHandler(ctypes.c_void_p(previous_handler))


def grab(wid, max_pixels=MAX_PIXELS):
    """Return ``(width, height, bgra)`` for a window, raising on failure."""
    if not wid:
        raise GrabError("no window id")

    with _lock:
        lib = _load()
        display = _open_display(lib)
        return _grab_locked(lib, display, int(wid), int(max_pixels))


def available():
    """True when the XComposite path can be attempted on this machine."""
    try:
        _load()
        _open_display(_libraries)
    except CompositeUnavailable:
        return False
    except Exception:
        return False
    return True


def _compositor_owner(lib, display):
    """Informational: who owns ``_NET_WM_CM_S<screen>``, or None."""
    try:
        screen = lib.x11.XDefaultScreen(display)
        name = ("_NET_WM_CM_S%d" % screen).encode("ascii")
        atom = lib.x11.XInternAtom(display, name, 1)  # only-if-exists
        if not atom:
            return None
        owner = lib.x11.XGetSelectionOwner(display, atom)
        return owner or None
    except Exception:
        return None


def status():
    """Return ``(ok, detail)`` for the native XComposite capture path."""
    try:
        lib = _load()
    except CompositeUnavailable as exc:
        return False, str(exc)
    except Exception as exc:
        return False, "cannot load X libraries: %s" % exc

    try:
        display = _open_display(lib)
    except CompositeUnavailable as exc:
        return False, str(exc)
    except Exception as exc:
        return False, "cannot open X display: %s" % exc

    try:
        event_base = ctypes.c_int()
        error_base = ctypes.c_int()
        supported = bool(
            lib.composite.XCompositeQueryExtension(
                display, ctypes.byref(event_base), ctypes.byref(error_base)
            )
        )
    except Exception as exc:
        return False, "XCompositeQueryExtension failed: %s" % exc
    if not supported:
        return False, "XComposite extension is not available"

    detail = "XComposite ready"
    owner = _compositor_owner(lib, display)
    detail += "; compositing manager %s (informational)" % (
        "running" if owner else "not detected"
    )
    return True, detail
