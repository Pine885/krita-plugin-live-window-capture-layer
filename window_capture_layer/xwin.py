"""X11 window lookup through xdotool.

Krita-free: no krita or Qt imports. Every function takes an injectable
``runner`` so parsing can be unit-tested without X11.

Runner contract: ``runner(argv, timeout=5)`` returns an object with ``stdout``
(str) and ``returncode`` (int). Failures return ``returncode != 0``.

``xdotool search --onlyvisible`` lists only the current virtual desktop; see
devNotes.md for the multi-desktop work item.
"""

import shutil
import subprocess
from collections import namedtuple

from . import debug

log = debug.logger

DEFAULT_TIMEOUT_S = 5
PICK_TIMEOUT_S = 30

# An empty --name pattern is unverified on some xdotool builds; "." is a regex
# that matches every title.
MATCH_ALL = "."

WindowInfo = namedtuple("WindowInfo", "wid title width height")


def xdotool_available(which=shutil.which):
    """Return True when the xdotool binary can be found on PATH."""
    try:
        return bool(which("xdotool"))
    except Exception:
        return False


def _default_runner(argv, timeout=DEFAULT_TIMEOUT_S):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _run(runner, argv, timeout=DEFAULT_TIMEOUT_S):
    try:
        return runner(argv, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("xdotool call failed: %s (%s)", argv, exc)
        return None


def _stdout(result):
    if result is None:
        return ""
    if getattr(result, "returncode", 1) != 0:
        log.debug("xdotool returned %s", getattr(result, "returncode", "?"))
        return ""
    return result.stdout or ""


def parse_search(text):
    """Parse ``xdotool search`` output into integer window ids."""
    ids = []
    for token in text.split():
        try:
            ids.append(int(token))
        except ValueError:
            continue
    return ids


def parse_geometry(text):
    """Parse ``xdotool getwindowgeometry --shell`` output into (w, h)."""
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    try:
        return int(values["WIDTH"]), int(values["HEIGHT"])
    except (KeyError, ValueError):
        return 0, 0


def parse_name(text):
    """Parse ``xdotool getwindowname`` output."""
    return text.strip()


def find_windows(title, runner=_default_runner):
    """Return all visible window ids whose title contains *title*."""
    result = _run(runner, ["xdotool", "search", "--onlyvisible", "--name", title])
    return parse_search(_stdout(result))


def window_name(wid, runner=_default_runner):
    result = _run(runner, ["xdotool", "getwindowname", str(wid)])
    return parse_name(_stdout(result))


def window_size(wid, runner=_default_runner):
    result = _run(
        runner, ["xdotool", "getwindowgeometry", "--shell", str(wid)]
    )
    return parse_geometry(_stdout(result))


def window_info(wid, runner=_default_runner):
    width, height = window_size(wid, runner)
    return WindowInfo(wid, window_name(wid, runner), width, height)


def list_windows(runner=_default_runner):
    """Return every visible window, largest first. Empty if xdotool fails."""
    windows = []
    seen = set()
    for wid in find_windows(MATCH_ALL, runner):
        if wid in seen:
            continue
        seen.add(wid)
        info = window_info(wid, runner)
        if info.width > 0 and info.height > 0:
            windows.append(info)
    windows.sort(key=lambda info: info.width * info.height, reverse=True)
    log.debug("listed %d visible windows", len(windows))
    return windows


def best_window(title, runner=_default_runner):
    """Pick the largest matching window; toolbar/utility windows are small."""
    best = None
    best_area = 0
    for wid in find_windows(title, runner):
        width, height = window_size(wid, runner)
        if width * height > best_area:
            best, best_area = wid, width * height
    log.debug("best_window(%r) -> %s (%d px)", title, best, best_area)
    return best


def click_pick(runner=_default_runner):
    """Ask the user to click a window; return its id or None on timeout."""
    result = _run(runner, ["xdotool", "selectwindow"], timeout=PICK_TIMEOUT_S)
    ids = parse_search(_stdout(result))
    return ids[0] if ids else None
