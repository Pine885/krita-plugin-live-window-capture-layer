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

try:
    PIXEL_FORMAT = QImage.Format_ARGB32
except AttributeError:
    PIXEL_FORMAT = QImage.Format.Format_ARGB32

try:
    APPLICATION_ACTIVE = Qt.ApplicationActive
except AttributeError:
    APPLICATION_ACTIVE = Qt.ApplicationState.ApplicationActive

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
