"""Keyboard state for the groom tool: B held (resize like Maya's brushes)
and [ / ] to step the brush size.

Maya hotkeys never reach the viewport widget, so an application-level Qt
event filter is used. It only reacts while the Wambo Fur groom tool is the
current tool, so Maya's own 'B' (soft select) keeps working elsewhere.
"""
import maya.cmds as cmds

from ..ui.qt import QtCore, QtWidgets


class _KeyFilter(QtCore.QObject):
    def __init__(self):
        super(_KeyFilter, self).__init__()
        self.b_down = False

    def _tool_active(self):
        from . import brushes
        try:
            return cmds.currentCtx() == brushes.CONTEXT
        except RuntimeError:
            return False

    def eventFilter(self, obj, e):
        t = e.type()
        QE = QtCore.QEvent
        if t not in (QE.KeyPress, QE.KeyRelease, QE.ShortcutOverride):
            return False
        key = e.key()
        if key not in (QtCore.Qt.Key_B, QtCore.Qt.Key_BracketLeft, QtCore.Qt.Key_BracketRight):
            return False
        if e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier):
            return False
        # never steal keys from text fields
        focus = QtWidgets.QApplication.focusWidget()
        if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox, QtWidgets.QTextEdit,
                              QtWidgets.QPlainTextEdit)):
            return False
        if not self._tool_active():
            self.b_down = False
            return False
        if t == QE.ShortcutOverride:
            e.accept()               # keep Maya's hotkey (soft select etc.) from firing
            return True
        if key == QtCore.Qt.Key_B:
            if not e.isAutoRepeat():
                self.b_down = (t == QE.KeyPress)
            return True
        if t == QE.KeyPress:
            from . import brushes
            f = 1.0 / 1.15 if key == QtCore.Qt.Key_BracketLeft else 1.15
            brushes.STATE.radius = max(4.0, min(1000.0, brushes.STATE.radius * f))
            brushes.settings_changed()
        return True


_FILTER = None


def start():
    global _FILTER
    if _FILTER is None:
        _FILTER = _KeyFilter()
        QtWidgets.QApplication.instance().installEventFilter(_FILTER)


def stop():
    global _FILTER
    if _FILTER is not None:
        try:
            QtWidgets.QApplication.instance().removeEventFilter(_FILTER)
        except RuntimeError:
            pass
        _FILTER = None


def b_down():
    return bool(_FILTER and _FILTER.b_down)
