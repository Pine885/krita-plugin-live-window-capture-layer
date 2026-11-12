"""Qt binding shim so the plugin runs on Krita 5.x (PyQt5) and 6.x (PyQt6).

Every Qt import in the plugin goes through this module.
"""

try:
    from PyQt5.QtCore import QByteArray, Qt, QTimer, QUuid
    from PyQt5.QtGui import QColor, QGuiApplication, QImage
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QComboBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PyQt6.QtCore import QByteArray, Qt, QTimer, QUuid
    from PyQt6.QtGui import QColor, QGuiApplication, QImage
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QComboBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

# Krita's integer RGBA paint devices store Blue, Green, Red, Alpha (see
# Node.pixelData() in the API docs). Qt's ARGB32 keeps 0xAARRGGBB in a word,
# which is B,G,R,A in memory on little-endian, so no channel swap is needed.
try:
    PIXEL_FORMAT = QImage.Format_ARGB32
except AttributeError:
    PIXEL_FORMAT = QImage.Format.Format_ARGB32

try:
    APPLICATION_ACTIVE = Qt.ApplicationActive
except AttributeError:
    APPLICATION_ACTIVE = Qt.ApplicationState.ApplicationActive

# Re-exports for the rest of the plugin; __all__ also keeps linters quiet.
__all__ = [
    "QByteArray",
    "Qt",
    "QTimer",
    "QUuid",
    "QColor",
    "QGuiApplication",
    "QImage",
    "QAbstractItemView",
    "QComboBox",
    "QHBoxLayout",
    "QHeaderView",
    "QInputDialog",
    "QLabel",
    "QMessageBox",
    "QPushButton",
    "QTableWidget",
    "QTableWidgetItem",
    "QVBoxLayout",
    "QWidget",
    "PIXEL_FORMAT",
    "APPLICATION_ACTIVE",
]
