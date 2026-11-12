"""Tunable limits. Krita-free: no krita or Qt imports."""

MIN_INTERVAL_MS = 50
DEFAULT_INTERVAL_MS = 500
MAX_INTERVAL_MS = 10000

MAX_SESSIONS = 8

# 4K UHD. Larger windows are rejected instead of silently truncated.
MAX_PIXELS = 3840 * 2160
