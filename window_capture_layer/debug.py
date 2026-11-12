"""Developer logging, gated by environment variables.

Krita-free: no krita or Qt imports.

    WINDOW_CAPTURE_LAYER_DEBUG=1     INFO
    WINDOW_CAPTURE_LAYER_DEBUG=2     DEBUG (verbose)
    WINDOW_CAPTURE_LAYER_LOG=<path>  append to a file instead of stderr
"""

import logging
import os
import sys

LOGGER_NAME = "window_capture_layer"
ENV_LEVEL = "WINDOW_CAPTURE_LAYER_DEBUG"
ENV_LOG = "WINDOW_CAPTURE_LAYER_LOG"

_LOG_FORMAT = "window_capture_layer: %(levelname)s %(message)s"

logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())
logger.propagate = False

_configured = False
_log_path = None
_notices = []
_warned = set()

NOTICE_LIMIT = 5


def env_level(raw=None):
    """Map the env value to a logging level. Empty/unknown means off."""
    value = os.environ.get(ENV_LEVEL, "") if raw is None else raw
    value = (value or "").strip().lower()
    if value in ("2", "debug", "verbose"):
        return logging.DEBUG
    if value in ("1", "info", "true", "yes", "on"):
        return logging.INFO
    return logging.WARNING


def configure():
    """Attach the real handler once. Safe to call repeatedly."""
    global _configured, _log_path
    if _configured:
        return logger
    _configured = True
    _log_path = None

    level = env_level()
    logger.setLevel(level)
    if level == logging.WARNING:
        return logger

    formatter = logging.Formatter(_LOG_FORMAT)
    handler = None
    path = os.environ.get(ENV_LOG, "").strip()
    if path:
        try:
            path = os.path.abspath(os.path.expanduser(path))
            handler = logging.FileHandler(path, encoding="utf-8")
            _log_path = path
        except OSError as exc:
            handler = None
            logger.warning("cannot open log file %r: %s", path, exc)

    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.debug("logging configured, level=%d", level)
    return logger


def warn_once(key, message, *args):
    """Log a warning once per *key* and keep it for the docker dialog.

    Returns True when this call logged, False for a repeat. Use for conditions
    the user must see (native capture unusable, black frames) without flooding
    the log on every capture.
    """
    if key in _warned:
        return False
    _warned.add(key)
    text = message % args if args else message
    logger.warning(text)
    _notices.append(text)
    del _notices[:-NOTICE_LIMIT]
    return True


def notices():
    """Recent warn-once messages, oldest first, capped at NOTICE_LIMIT."""
    return list(_notices)


def status():
    """Return ``(level_name, log_path)`` for the docker diagnostics dialog."""
    level = logger.level
    if level == logging.NOTSET:
        # configure() has not run yet; NullHandler drops everything.
        return "not configured", _log_path
    return logging.getLevelName(level).lower(), _log_path


def reset():
    """Tear down handlers and notices. Test helper only."""
    global _configured, _log_path
    _configured = False
    _log_path = None
    _notices[:] = []
    _warned.clear()
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.WARNING)
