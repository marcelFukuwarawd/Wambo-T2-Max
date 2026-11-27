"""Brush circle that follows the mouse in every model panel (Viewport 2.0).

A hidden `wamboFurBrushCursor` locator (registered by the plug-in) draws a
screen-space circle. A Qt event filter on the viewport widgets feeds it the
mouse position even when no button is pressed, and implements:

    B + LMB drag (left/right) ... change brush radius
    B + MMB drag (left/right) ... change brush strength
"""
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui

from ..ui.qt import QtCore, QtWidgets, wrapInstance

NODE_TYPE = "wamboFurBrushCursor"
NODE_NAME = "wamboFurBrushCursorShape"

MODE_COLORS = {
    "comb": (1.0, 0.66, 0.23), "length": (0.37, 0.77, 0.94), "smooth": (0.55, 0.9, 0.55),
    "puff": (0.85, 0.6, 1.0), "cut": (1.0, 0.36, 0.36), "add": (0.5, 1.0, 0.6),
    "remove": (1.0, 0.45, 0.45), "straighten": (0.95, 0.95, 0.6), "move": (1.0, 0.85, 0.4),
    "paint": (0.4, 1.0, 0.55), "clump": (0.95, 0.55, 0.85),
}


def _label(mode):
    from .brushes import LABELS
    return LABELS.get(mode, mode).upper()


class _Plugs(object):
    """Cached MPlugs of the cursor node (writing through MPlug is fast and
    never touches the undo queue)."""

    def __init__(self, shape):
        sel = om.MSelectionList()
        sel.add(shape)
        self.obj = sel.getDependNode(0)
        fn = om.MFnDependencyNode(self.obj)
        self.p = dict((n, fn.findPlug(n, False)) for n in
                      ("cursorX", "cursorY", "radius", "strength", "active", "pressed",
                       "camera", "label", "cursorColor"))

    def set(self, **kw):
        for k, v in kw.items():
            plug = self.p[k]
            if isinstance(v, bool):
                plug.setBool(v)
            elif isinstance(v, str):
                plug.setString(v)
            elif isinstance(v, tuple):
                for i in range(3):
                    plug.child(i).setFloat(v[i])
            else:
                plug.setDouble(float(v))


class BrushCursor(QtCore.QObject):
    def __init__(self, state):
        super(BrushCursor, self).__init__()
        self.state = state          # brushes.BrushState (radius, strength, mode)
        self.plugs = None
        self.widgets = {}           # QWidget -> camera full path
        self.b_down = False
        self.adjust = None          # (button, start_x, start_value)
        self.last_pos = None

    # ---------------------------------------------------------------- node
    def _ensure_node(self):
        if not cmds.pluginInfo("wamboFur", q=True, loaded=True):
            return False
        if not cmds.objExists(NODE_NAME):
            state = cmds.undoInfo(q=True, stateWithoutFlush=True)
            cmds.undoInfo(stateWithoutFlush=False)
            try:
                sel = cmds.ls(selection=True)
                tr = cmds.createNode("transform", name="wamboFurBrushCursor", skipSelect=True)
                cmds.createNode(NODE_TYPE, name=NODE_NAME, parent=tr, skipSelect=True)
                for n in (tr, NODE_NAME):
                    cmds.setAttr(n + ".hiddenInOutliner", True)
                cmds.setAttr(tr + ".doNotWrite", True)   # never saved with the scene
                # reference display = never picked by viewport selection clicks
                cmds.setAttr(tr + ".overrideEnabled", 1)
                cmds.setAttr(tr + ".overrideDisplayType", 2)
                if sel:
                    cmds.select(sel)
            finally:
                cmds.undoInfo(stateWithoutFlush=state)
        self.plugs = _Plugs(NODE_NAME)
        return True

    def _remove_node(self):
        if cmds.objExists(NODE_NAME):
            state = cmds.undoInfo(q=True, stateWithoutFlush=True)
            cmds.undoInfo(stateWithoutFlush=False)
            try:
                tr = cmds.listRelatives(NODE_NAME, parent=True)
                cmds.delete(tr or NODE_NAME)
            finally:
                cmds.undoInfo(stateWithoutFlush=state)
        self.plugs = None

    # -------------------------------------------------------------- start
    def start(self):
        if not self._ensure_node():
            return False
        self.sync_style()
        self.widgets = {}
        for panel in cmds.getPanel(type="modelPanel") or []:
            try:
                view = omui.M3dView.getM3dViewFromModelPanel(panel)
                w = wrapInstance(int(view.widget()), QtWidgets.QWidget)
                cam = view.getCamera().fullPathName()
            except Exception:
                continue
            for sub in [w] + w.findChildren(QtWidgets.QWidget):
                sub.setMouseTracking(True)
                sub.installEventFilter(self)
                self.widgets[sub] = [panel, cam, w]
        # keys (B) are caught application-wide: Maya hotkeys never reach the viewport widget
        QtWidgets.QApplication.instance().installEventFilter(self)
        return True

    def stop(self):
        try:
            QtWidgets.QApplication.instance().removeEventFilter(self)
        except RuntimeError:
            pass
        for w in list(self.widgets):
            try:
                w.removeEventFilter(self)
            except RuntimeError:
                pass
        self.widgets = {}
        self._remove_node()

    def sync_style(self):
        if self.plugs is None:
            return
        self.plugs.set(radius=self.state.radius, strength=self.state.strength,
                       cursorColor=MODE_COLORS.get(self.state.mode, (1.0, 0.66, 0.23)),
                       label=_label(self.state.mode))
        self._refresh()

    def set_pressed(self, on):
        if self.plugs is not None:
            self.plugs.set(pressed=bool(on))

    def _refresh(self, widget=None):
        try:
            omui.M3dView.active3dView().refresh(False, False)
        except Exception:
            pass

    # ------------------------------------------------------------- events
    def _port_xy(self, w, pos):
        root = self.widgets[w][2]
        if w is not root:
            pos = w.mapTo(root, pos)
        dpr = root.devicePixelRatioF() if hasattr(root, "devicePixelRatioF") else 1.0
        return pos.x() * dpr, (root.height() - pos.y()) * dpr

    def eventFilter(self, w, e):
        if self.plugs is None:
            return False
        t = e.type()
        QE = QtCore.QEvent
        if t in (QE.KeyPress, QE.KeyRelease, QE.ShortcutOverride):
            # only while the mouse is over a viewport
            if self.last_pos is None or e.key() != QtCore.Qt.Key_B or e.modifiers():
                return False
            if t == QE.ShortcutOverride:
                e.accept()           # stop Maya's 'B' (soft select) hotkey
                return True
            if e.isAutoRepeat():
                return True
            self.b_down = (t == QE.KeyPress)
            if not self.b_down:
                self.adjust = None
            return True
        if w not in self.widgets:
            return False
        if t in (QE.MouseMove, QE.MouseButtonPress, QE.MouseButtonRelease):
            pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
            x, y = self._port_xy(w, pos)
            if t == QE.MouseButtonPress and self.b_down:
                which = "radius" if e.button() == QtCore.Qt.LeftButton else "strength"
                start = self.state.radius if which == "radius" else self.state.strength
                self.adjust = (which, x, start)
                return True                      # do not start a stroke
            if t == QE.MouseButtonRelease and self.adjust is not None:
                self.adjust = None
                if self.state.on_settings:
                    self.state.on_settings()
                return True
            if t == QE.MouseMove and self.adjust is not None:
                which, x0, v0 = self.adjust
                if which == "radius":
                    self.state.radius = max(4.0, min(1000.0, v0 + (x - x0)))
                else:
                    self.state.strength = max(0.0, min(1.0, v0 + (x - x0) / 300.0))
                self.sync_style()
                if self.state.on_settings:
                    self.state.on_settings()
                return True
            if self.adjust is None:
                self.last_pos = (x, y)
                self.plugs.set(cursorX=x, cursorY=y, active=True, camera=self.widgets[w][1])
                self._refresh()
            return False
        if t == QE.Enter:
            # camera may have changed since start (look-through etc.)
            panel = self.widgets[w][0]
            try:
                cam = omui.M3dView.getM3dViewFromModelPanel(panel).getCamera().fullPathName()
                for k, v in self.widgets.items():
                    if v[0] == panel:
                        v[1] = cam
            except Exception:
                pass
            return False
        if t == QE.Leave and w is self.widgets[w][2]:
            self.last_pos = None
            self.b_down = False
            self.plugs.set(active=False)
            self._refresh()
        return False
