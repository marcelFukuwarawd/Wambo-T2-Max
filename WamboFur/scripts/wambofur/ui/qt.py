"""PySide2 (Maya 2024) / PySide6 (Maya 2025+) compatibility."""
try:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
    from shiboken6 import wrapInstance            # noqa: F401
    QT6 = True
except ImportError:  # Maya 2024
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
    from shiboken2 import wrapInstance            # noqa: F401
    QT6 = False

QAction = QtGui.QAction if QT6 else QtWidgets.QAction
Signal = QtCore.Signal
Qt = QtCore.Qt


def exec_(obj, *args):
    fn = getattr(obj, "exec", None) or getattr(obj, "exec_")
    return fn(*args)


def event_pos(event):
    """Widget-local position of a mouse event as QPoint (Qt5 & Qt6)."""
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


def maya_main_window():
    try:
        from maya import OpenMayaUI as omui
    except ImportError:
        return None
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None
