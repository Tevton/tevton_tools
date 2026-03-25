"""Qt compatibility shim — supports PySide6 (Houdini 21+) and PySide2 (Houdini 19.x)."""

try:
    from PySide6 import QtCore, QtWidgets, QtGui, QtUiTools

    QT_VERSION = 6
except ImportError:
    from PySide2 import QtCore, QtWidgets, QtGui, QtUiTools  # type: ignore

    QT_VERSION = 2
    # PySide2: QShortcut and QKeySequence live in QtWidgets, not QtGui
    if not hasattr(QtGui, "QShortcut"):
        QtGui.QShortcut = QtWidgets.QShortcut
    if not hasattr(QtGui, "QKeySequence"):
        QtGui.QKeySequence = QtWidgets.QKeySequence

    # PySide2: exec_() → exec() wrappers (direct alias causes segfault in Shiboken C extensions)
    def _dialog_exec(self, *args, **kwargs):
        return self.exec_(*args, **kwargs)

    QtWidgets.QDialog.exec = _dialog_exec

    def _menu_exec(self, *args, **kwargs):
        return self.exec_(*args, **kwargs)

    QtWidgets.QMenu.exec = _menu_exec

    def _drag_exec(self, *args, **kwargs):
        return self.exec_(*args, **kwargs)

    QtGui.QDrag.exec = _drag_exec
