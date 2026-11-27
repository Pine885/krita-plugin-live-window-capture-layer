from collections import namedtuple

from . import backend, debug
from .limits import MAX_PIXELS
from .qt_compat import PIXEL_FORMAT, QGuiApplication

log = debug.logger

Frame = namedtuple("Frame", "w h data")


def encode_pixel(red, green, blue, alpha):
    return bytes((blue & 0xFF, green & 0xFF, red & 0xFF, alpha & 0xFF))


def image_bytes(image):
    size = image.sizeInBytes()
    pointer = image.constBits()
    try:
        pointer.setsize(size)
        return bytes(pointer)
    except TypeError:
        data = pointer.asstring(size)
        return data.encode("latin-1") if isinstance(data, str) else data


def _is_black_frame(exc):
    black = getattr(backend, "BlackFrame", None)
    return black is not None and isinstance(exc, black)


def _native_frame(wid, max_pixels):
    try:
        width, height, data = backend.grab(wid, max_pixels)
    except Exception as exc:
        if _is_black_frame(exc):
            debug.warn_once(
                "native-black",
                "native grab returned a black frame; using Qt fallback (%s)",
                exc,
            )
        else:
            log.debug("native grab of %s failed: %s", wid, exc)
        return None
    log.debug("grabbed window %s natively: %dx%d", wid, width, height)
    return Frame(width, height, data)


def grab_frame(wid, max_pixels=MAX_PIXELS):
    if not wid:
        return None

    usable, detail = backend.capture_status()
    if not usable:
        debug.warn_once(
            "native-unavailable",
            "native capture unavailable; using Qt fallback (%s)",
            detail,
        )
    else:
        frame = _native_frame(wid, max_pixels)
        if frame is not None:
            return frame

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        log.warning("no primary screen")
        return None

    image = screen.grabWindow(int(wid)).toImage()
    if image.isNull():
        log.debug("grab of window %s returned a null image", wid)
        return None

    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        log.debug("grab of window %s is empty (%dx%d)", wid, width, height)
        return None
    if width * height > max_pixels:
        log.warning(
            "skipping %dx%d window (over %d pixels)", width, height, max_pixels
        )
        return None

    image = image.convertToFormat(PIXEL_FORMAT)
    if image.bytesPerLine() != width * 4:
        log.warning(
            "unexpected row stride %d for width %d", image.bytesPerLine(), width
        )
        return None

    data = image_bytes(image)
    if len(data) < width * height * 4:
        log.warning(
            "short pixel buffer: %d bytes for %dx%d", len(data), width, height
        )
        return None
    log.debug("grabbed window %s: %dx%d", wid, width, height)
    return Frame(width, height, data)
