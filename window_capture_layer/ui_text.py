"""Docker table text helpers. Krita- and Qt-free."""

COLUMNS = ("Layer", "Window", "State")

TITLE_LIMIT = 30

STATE_MARKS = {
    "active": "\u2713",
    "paused": "\u2717",
    "lost": "!",
    "error": "\u26a0",
}


def short_title(title, limit=TITLE_LIMIT):
    """Trim a window title for the table; tooltips keep the full text."""
    text = (title or "").strip() or "-"
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def state_mark(state):
    """Short glyph for a session state, falling back to the raw value."""
    return STATE_MARKS.get(state, state)


def diagnostics_text(
    platform, ok, detail, notices, level, log_path, tool_ok=None, tool_detail=""
):
    """Multi-line capture backend summary for the ``@`` dialog."""
    lines = [
        "Capture: %s (%s)" % ("native" if ok else "Qt fallback", platform or "?")
    ]
    if detail:
        lines.append("  %s" % detail)
    if tool_ok is False:
        lines.append("Window listing: %s" % (tool_detail or "xdotool not found"))
        lines.append("  Debian/Ubuntu: sudo apt install xdotool")
        lines.append("  Arch: sudo pacman -S xdotool")
        lines.append("  Fedora: sudo dnf install xdotool")
    if notices:
        lines.append("Recent warnings:")
        for notice in notices:
            lines.append("  - %s" % notice)
    else:
        lines.append("Recent warnings: none")
    logging_line = "Log: %s" % (level or "unknown")
    if log_path:
        logging_line += " -> %s" % log_path
    lines.append(logging_line)
    lines.append(
        "Dev logging: WINDOW_CAPTURE_LAYER_DEBUG=1 (INFO) or 2 (DEBUG)"
    )
    lines.append(
        "  WINDOW_CAPTURE_LAYER_LOG=<path> writes to a file"
    )
    return "\n".join(lines)


def tester_text(platform):
    """Windows tester instructions; empty elsewhere."""
    if platform != "windows":
        return ""
    return "\n".join(
        [
            "1. Close Krita.",
            "2. In Command Prompt run:",
            "   set WINDOW_CAPTURE_LAYER_DEBUG=2",
            "   set WINDOW_CAPTURE_LAYER_LOG=%TEMP%\\wcl.log",
            "3. Start Krita from that same prompt.",
            "4. Capture Notepad, a GPU browser and a covered window.",
            "5. Optional A/B: set WINDOW_CAPTURE_LAYER_NATIVE=0 and repeat.",
            "6. Send the log file and this dialog text.",
        ]
    )


HELP_TEXT = """Capture a window into a paint layer.

add: new capture layer for the selected window
refresh: capture the selected session now
refresh all: capture every session of this document now
toggle: pause or resume; toggle all: every row
apply: keep the layer, forget the session

Move or scale a capture with its transform mask, not the Move tool.
Live sessions update when Krita gets focus again.

Credits: pine885"""
