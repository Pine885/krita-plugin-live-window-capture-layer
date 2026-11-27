import os
import sys

if sys.platform == "win32":
    from . import win32grab as _grabber
    from . import win32win as _windows

    PLATFORM = "windows"
else:
    from . import x11grab as _grabber
    from . import xwin as _windows

    PLATFORM = "x11"

WindowInfo = _windows.WindowInfo
GrabError = _grabber.GrabError
BlackFrame = getattr(_grabber, "BlackFrame", None)

ENV_NATIVE = "WINDOW_CAPTURE_LAYER_NATIVE"
_DISABLED_VALUES = ("0", "off", "false", "no")

_status_cache = None


def list_windows():
    return _windows.list_windows()


def find_windows(title):
    return _windows.find_windows(title)


def window_info(wid):
    return _windows.window_info(wid)


def best_window(title):
    return _windows.best_window(title)


def click_pick():
    return _windows.click_pick()


def grab(wid, max_pixels):
    return _grabber.grab(wid, max_pixels)


def available():
    try:
        return _grabber.available()
    except Exception:
        return False


def capture_status():
    global _status_cache
    if _status_cache is not None:
        return _status_cache

    raw = os.environ.get(ENV_NATIVE, "").strip().lower()
    if raw in _DISABLED_VALUES:
        _status_cache = (
            False,
            "disabled by %s=%s" % (ENV_NATIVE, raw),
        )
        return _status_cache

    try:
        status_fn = getattr(_grabber, "status", None)
        if status_fn is None:
            ok = available()
            detail = "available" if ok else "not available"
            _status_cache = (bool(ok), detail)
        else:
            ok, detail = status_fn()
            _status_cache = (bool(ok), str(detail))
    except Exception as exc:
        _status_cache = (False, "status check failed: %s" % exc)
    return _status_cache


def native_usable():
    return capture_status()[0]


def tool_status():
    if PLATFORM == "windows":
        return True, "no external tools required"
    try:
        ok = bool(_windows.xdotool_available())
    except Exception as exc:
        return False, "xdotool check failed: %s" % exc
    if ok:
        return True, "xdotool ready"
    return False, "xdotool not found on PATH"


def reset_status_cache():
    global _status_cache
    _status_cache = None
