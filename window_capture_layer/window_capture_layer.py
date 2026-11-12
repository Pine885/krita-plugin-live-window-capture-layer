"""Krita extension: the three original actions, routed through the manager."""

from krita import Extension, Krita

from . import backend, debug
from .frame import Frame, encode_pixel
from .layer_target import LayerTarget
from .manager import document_error, manager_for_document
from .qt_compat import QInputDialog, QMessageBox

log = debug.logger

PLUGIN_VERSION = "0.8.0"

ACTION_CAPTURE = "window_capture_layer_capture"
ACTION_LIVE = "window_capture_layer_live_toggle"
ACTION_TEST = "window_capture_layer_test_pattern"

TEST_BANDS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))


class WindowCaptureExtension(Extension):

    def __init__(self, parent):
        super().__init__(parent)
        self._window = None

    # -- Krita entry points ------------------------------------------------

    def setup(self):
        pass

    def createActions(self, window):
        self._window = window

        action = window.createAction(
            ACTION_CAPTURE, "Capture Window to New Layer", "tools/scripts"
        )
        action.triggered.connect(self.capture_once)

        action = window.createAction(
            ACTION_LIVE, "Toggle Live Window Capture", "tools/scripts"
        )
        action.triggered.connect(self.toggle_live)

        action = window.createAction(
            ACTION_TEST, "Insert Channel Test Pattern", "tools/scripts"
        )
        action.triggered.connect(self.insert_test_pattern)

    # -- helpers -----------------------------------------------------------

    def _parent_widget(self):
        try:
            return self._window.qwindow()
        except Exception:
            return None

    def _message(self, text, title="Window Capture Layer"):
        log.info("%s: %s", title, text)
        QMessageBox.information(self._parent_widget(), title, text)

    def _error(self, text, title="Window Capture Layer"):
        log.warning("%s: %s", title, text)
        QMessageBox.warning(self._parent_widget(), title, text)

    def _document(self):
        doc = Krita.instance().activeDocument()
        if doc is None:
            self._error("Open a document first.")
            return None
        session_manager = manager_for_document(doc)
        session_manager.restore(doc)
        problem = document_error(doc)
        if problem:
            self._error(problem)
            return None
        return doc

    def _ask_window(self, session_manager):
        default = session_manager.last_title()
        title, accepted = QInputDialog.getText(
            self._parent_widget(),
            "Window Capture Layer",
            "Window title contains:",
            text=default,
        )
        if not accepted or not title.strip():
            return None, ""
        title = title.strip()
        wid = backend.best_window(title)
        if wid is None:
            self._error("No visible window matching %r." % title)
            return None, ""
        session_manager.remember_title(title)
        return wid, title

    # -- actions -----------------------------------------------------------

    def capture_once(self):
        log.debug("action: capture_once")
        doc = self._document()
        if doc is None:
            return
        session_manager = manager_for_document(doc)
        wid, title = self._ask_window(session_manager)
        if wid is None:
            return
        capture_session = session_manager.snapshot_once(doc, title, wid=wid)
        if capture_session.state == "error":
            self._error(capture_session.message or "Capture failed.")

    def toggle_live(self):
        doc = self._document()
        if doc is None:
            return
        session_manager = manager_for_document(doc)
        log.debug("action: toggle_live (active=%s)", session_manager.has_active())
        if session_manager.has_active():
            session_manager.pause_all()
            self._message("All live sessions paused.")
            return

        wid, title = self._ask_window(session_manager)
        if wid is None:
            return
        try:
            session_manager.add(doc, title, wid=wid)
        except ValueError as exc:
            self._error(str(exc))
            return
        session_manager.refresh_active()

    def insert_test_pattern(self):
        log.debug("action: insert_test_pattern")
        doc = self._document()
        if doc is None:
            return

        width, height = doc.width(), doc.height()
        if width <= 0 or height <= 0:
            self._error("Document has no size.")
            return

        band_width = max(1, width // len(TEST_BANDS))
        row = bytearray()
        for x in range(width):
            red, green, blue = TEST_BANDS[min(len(TEST_BANDS) - 1, x // band_width)]
            row += encode_pixel(red, green, blue, 255)

        target = LayerTarget(doc, "Channel test pattern")
        if not target.create():
            self._error("Could not create the test layer.")
            return
        if not target.write(Frame(width, height, bytes(row) * height)):
            self._error("setPixelData() refused the write.")
            return

        doc.refreshProjection()
        log.info("test pattern written: %dx%d", width, height)
        self._message(
            "Bands must read left to right: red, green, blue, white.\n"
            "Any other order means PIXEL_FORMAT / encode_pixel disagree with "
            "Krita's integer RGBA memory order."
        )
