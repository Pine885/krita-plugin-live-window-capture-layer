"""Docker UI: session table and controls."""

from krita import DockWidget

from . import backend, debug
from .manager import (
    SessionManager,
    document_key,
    register_document_manager,
    unregister_document_manager,
)
from .qt_compat import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimer,
    QVBoxLayout,
    QWidget,
)
from .ui_text import (
    COLUMNS,
    HELP_TEXT,
    diagnostics_text,
    short_title,
    state_mark,
    tester_text,
)

log = debug.logger


class _WindowCombo(QComboBox):
    """Combo box that refreshes the window list each time the popup opens."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_open = None

    def showPopup(self):
        if self.on_open is not None:
            self.on_open()
        super().showPopup()


class WindowCaptureDocker(DockWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Window Capture")
        self._manager = SessionManager()
        self._doc = None
        self._doc_key = None
        self._row_ids = []
        self._windows = {}

        self._build_ui()
        self._manager.on_refresh = self._refresh_table
        self._refresh_windows()
        self._refresh_table()
        self._announce_backend()

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(500)
        self._ui_timer.timeout.connect(self._refresh_table)
        self._ui_timer.start()

    # -- construction ------------------------------------------------------

    def _build_ui(self):
        root = QWidget(self)
        layout = QVBoxLayout(root)

        top_row = QHBoxLayout()
        self._help_button = QPushButton("@", root)
        self._help_button.setMaximumWidth(32)
        self._help_button.setToolTip("Instructions and credits")
        top_row.addWidget(self._help_button)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        layout.addWidget(QLabel("Window", root))

        window_row = QHBoxLayout()
        self._combo = _WindowCombo(root)
        self._combo.on_open = self._refresh_windows
        window_row.addWidget(self._combo, 1)
        self._add_button = QPushButton("add", root)
        window_row.addWidget(self._add_button)
        self._pick_button = QPushButton("choose", root)
        window_row.addWidget(self._pick_button)
        layout.addLayout(window_row)

        self._table = QTableWidget(0, len(COLUMNS), root)
        self._table.setHorizontalHeaderLabels(list(COLUMNS))
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        layout.addWidget(self._table, 1)

        control_row = QHBoxLayout()
        self._refresh_button = QPushButton("refresh", root)
        control_row.addWidget(self._refresh_button)
        self._refresh_all_button = QPushButton("refresh all", root)
        control_row.addWidget(self._refresh_all_button)
        self._toggle_button = QPushButton("toggle", root)
        control_row.addWidget(self._toggle_button)
        self._toggle_all_button = QPushButton("toggle all", root)
        control_row.addWidget(self._toggle_all_button)
        control_row.addStretch(1)
        self._apply_button = QPushButton("apply", root)
        control_row.addWidget(self._apply_button)
        layout.addLayout(control_row)

        self._status = QLabel("", root)
        layout.addWidget(self._status)

        self.setWidget(root)

        self._help_button.clicked.connect(self._show_help)
        self._refresh_button.clicked.connect(self._refresh_selected)
        self._refresh_all_button.clicked.connect(self._refresh_all)
        self._pick_button.clicked.connect(self._pick_window)
        self._add_button.clicked.connect(self._add_live)
        self._toggle_button.clicked.connect(self._toggle_selected)
        self._toggle_all_button.clicked.connect(self._toggle_all)
        self._apply_button.clicked.connect(self._apply_selected)

    def _show_help(self):
        ok, detail = backend.capture_status()
        tool_ok, tool_detail = self._announce_tools()
        level, log_path = debug.status()
        parts = [
            HELP_TEXT,
            diagnostics_text(
                backend.PLATFORM,
                ok,
                detail,
                debug.notices(),
                level,
                log_path,
                tool_ok=tool_ok,
                tool_detail=tool_detail,
            ),
            tester_text(backend.PLATFORM),
        ]
        QMessageBox.about(
            self,
            "Window Capture Layer",
            "\n\n".join(part for part in parts if part),
        )

    def _announce_tools(self):
        tool_ok, tool_detail = backend.tool_status()
        if not tool_ok:
            debug.warn_once(
                "xdotool-missing",
                "window list and pick need xdotool (%s)",
                tool_detail,
            )
        return tool_ok, tool_detail

    def _announce_backend(self):
        ok, detail = backend.capture_status()
        tool_ok, tool_detail = self._announce_tools()
        messages = []
        if not ok:
            messages.append("Native capture unavailable: %s" % detail)
        if not tool_ok:
            messages.append("Window list unavailable: %s" % tool_detail)
        if messages:
            self._status.setText("; ".join(messages))

    # -- document tracking -------------------------------------------------

    def canvasChanged(self, canvas):
        self._sync_document()

    def _sync_document(self):
        doc = self._current_document()
        key = document_key(doc)
        if key != self._doc_key:
            unregister_document_manager(self._doc_key, self._manager)
            self._doc_key = key
        self._doc = doc
        if self._doc is not None:
            register_document_manager(self._doc, self._manager)
            self._manager.restore(self._doc)
        return self._doc

    def _current_document(self):
        try:
            from krita import Krita

            app = Krita.instance()
        except Exception:
            return None
        return app.activeDocument()

    # -- window list -------------------------------------------------------

    @staticmethod
    def _window_label(info):
        return "%s [%s] %dx%d" % (
            short_title(info.title, 40),
            info.wid,
            info.width,
            info.height,
        )

    def _refresh_windows(self):
        self._windows = {info.wid: info for info in backend.list_windows()}
        self._combo.clear()
        for info in self._windows.values():
            self._combo.addItem(self._window_label(info), info.wid)
        if self._windows:
            last = self._manager.last_title()
            for index, info in enumerate(self._windows.values()):
                if last and last in info.title:
                    self._combo.setCurrentIndex(index)
                    break
        self._status.setText("%d windows" % len(self._windows))
        log.debug("window list refreshed: %d entries", len(self._windows))

    def _pick_window(self):
        wid = backend.click_pick()
        if wid is None:
            self._status.setText("No window picked.")
            return
        info = self._windows.get(wid)
        if info is None:
            info = backend.window_info(wid)
            self._windows[wid] = info
            self._combo.addItem(self._window_label(info), info.wid)
            self._combo.setCurrentIndex(self._combo.count() - 1)
        else:
            self._combo.setCurrentIndex(list(self._windows).index(wid))
        self._status.setText("Picked %s" % info.title)

    def _selected_window(self):
        wid = self._combo.currentData()
        if not wid:
            return None, ""
        info = self._windows.get(wid)
        if info is not None:
            return wid, info.title
        return wid, self._combo.currentText().split(" [")[0]

    # -- session actions ---------------------------------------------------

    def _require_document(self):
        self._sync_document()
        if self._doc is None:
            QMessageBox.information(self, "Window Capture", "Open a document first.")
            return None
        return self._doc

    def _session_target(self):
        doc = self._require_document()
        if doc is None:
            return None
        wid, title = self._selected_window()
        if not title:
            self._status.setText("No window selected.")
            return None
        return doc, wid, title

    def _add_live(self):
        target = self._session_target()
        if target is None:
            return
        doc, wid, title = target
        try:
            self._manager.add(doc, title, wid=wid)
        except ValueError as exc:
            QMessageBox.warning(self, "Window Capture", str(exc))
            return
        self._manager.remember_title(title)
        self._manager.refresh_active()
        log.info("docker: live session requested for %r (wid=%s)", title, wid)
        self._refresh_table()

    def _selected_session_id(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._row_ids):
            return None
        return self._row_ids[row]

    def _toggle_selected(self):
        session_id = self._selected_session_id()
        if session_id is None:
            return
        if self._manager.toggle(session_id):
            self._manager.refresh_active()
            self._refresh_table()

    def _apply_selected(self):
        session_id = self._selected_session_id()
        if session_id is None:
            self._status.setText("Select a session first.")
            return
        if self._manager.detach(session_id):
            self._status.setText("Detached; layer kept.")
        self._refresh_table()

    def _refresh_selected(self):
        session_id = self._selected_session_id()
        if session_id is None:
            self._status.setText("Select a session first.")
            return
        count = self._manager.capture(session_id)
        self._status.setText("Refreshed %d session(s)." % count)
        self._refresh_table()

    def _refresh_all(self):
        doc = self._sync_document()
        count = self._manager.capture_all(doc)
        self._status.setText("Refreshed %d session(s)." % count)
        self._refresh_table()

    def _toggle_all(self):
        self._manager.toggle_all()
        self._manager.refresh_active()
        self._refresh_table()

    # -- table -------------------------------------------------------------

    def _refresh_table(self):
        self._sync_document()
        self._manager.prune_dead(self._doc)
        rows = (
            self._manager.snapshot(self._doc) if self._doc is not None else []
        )
        selected = self._selected_session_id()

        self._row_ids = [row["id"] for row in rows]
        self._table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            state = row["state"]
            state_text = state_mark(state)
            state_hint = state
            if row["message"]:
                state_hint = "%s (%s)" % (state, row["message"])
            values = (
                row["layer_name"] or "-",
                short_title(row["title"]),
                state_text,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setToolTip(row["title"])
                elif column == 2:
                    item.setToolTip(state_hint)
                self._table.setItem(index, column, item)

        if selected in self._row_ids:
            self._table.selectRow(self._row_ids.index(selected))
        elif self._row_ids:
            self._table.selectRow(0)
