"""Wambo Fur - Maya plug-in entry point (Maya 2024+).

Load it from Windows > Settings/Preferences > Plug-in Manager, or
    cmds.loadPlugin("wamboFur.py")

Registers
  wamboFur               command (editor / update / export / render-time export)
  wamboFurGroomEdit      internal undoable command used by the groom brushes
  wamboFurGroomCtx       groom tool context (draws the brush circle)
  wamboFurGroom          native groom guides (stored in the scene, drawn in VP2)
"""
import sys
import os

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr
import maya.api.OpenMayaUI as omui
import maya.cmds as cmds

# make the package importable even when the module file is not installed
_here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
_scripts = os.path.normpath(os.path.join(_here, "..", "scripts"))
if _here and os.path.isdir(_scripts) and _scripts not in sys.path:
    sys.path.append(_scripts)

VENDOR = "Wambo"
VERSION = "1.3.0"


def maya_useNewAPI():
    pass


# =========================================================================
# wamboFur command
# =========================================================================
class WamboFurCmd(om.MPxCommand):
    """MEL/Python command:
        wamboFur                 -> open the editor
        wamboFur -u              -> update every fur preview
        wamboFur -e              -> export every fur system for render (current frame)
        wamboFur -e -s 1 -f 100  -> export a frame range
        wamboFur -r              -> render-time export (used by the Pre Render Frame hook)
    """
    NAME = "wamboFur"

    @staticmethod
    def creator():
        return WamboFurCmd()

    @staticmethod
    def syntax():
        s = om.MSyntax()
        s.addFlag("-u", "-update")
        s.addFlag("-e", "-export")
        s.addFlag("-r", "-renderTime")
        s.addFlag("-s", "-start", om.MSyntax.kDouble)
        s.addFlag("-f", "-end", om.MSyntax.kDouble)
        return s

    def doIt(self, args):
        db = om.MArgDatabase(self.syntax(), args)
        from wambofur.maya import system, render
        if db.isFlagSet("-u"):
            for n in system.list_systems():
                system.FurSystem(n).update_preview()
            return
        if db.isFlagSet("-r"):
            self.setResult(render.render_time_export())
            return
        if db.isFlagSet("-e"):
            start = db.flagArgumentDouble("-s", 0) if db.isFlagSet("-s") else None
            end = db.flagArgumentDouble("-f", 0) if db.isFlagSet("-f") else start
            out = []
            for n in system.list_systems():
                paths, _ = render.render(system.FurSystem(n), start, end)
                out.extend(paths)
            self.setResult(out)
            return
        from wambofur.ui import main_window
        main_window.show()


# =========================================================================
# undoable groom edit (one brush stroke)
# =========================================================================
class GroomEditCmd(om.MPxCommand):
    """Takes the stroke prepared by wambofur.maya.brushes (PENDING) and makes
    it undoable. The new groom state is already applied when it runs."""
    NAME = "wamboFurGroomEdit"

    def __init__(self):
        om.MPxCommand.__init__(self)
        self.edits = []

    @staticmethod
    def creator():
        return GroomEditCmd()

    def isUndoable(self):
        return True

    def doIt(self, args):
        from wambofur.maya import brushes
        self.edits = brushes.take_pending()

    def redoIt(self):
        self._apply(2)

    def undoIt(self):
        self._apply(1)

    def _apply(self, which):
        from wambofur.maya import brushes
        brushes.apply_edits(self.edits, which)


# =========================================================================
# groom tool (native context): brush circle + mouse events
# =========================================================================
MODE_COLORS = {
    "comb": (1.0, 0.66, 0.23), "length": (0.37, 0.77, 0.94), "smooth": (0.55, 0.9, 0.55),
    "puff": (0.85, 0.6, 1.0), "cut": (1.0, 0.36, 0.36), "add": (0.5, 1.0, 0.6),
    "remove": (1.0, 0.45, 0.45), "straighten": (0.95, 0.95, 0.6), "move": (1.0, 0.85, 0.4),
    "paint": (0.4, 1.0, 0.55), "clump": (0.95, 0.55, 0.85),
}


def _event_xy(event):
    try:
        x, y = event.position
    except Exception:
        x, y = event.getPosition()
    return float(x), float(y)


def _event_mods(event):
    try:
        if event.isModifierControl():
            return "ctrl"
        if event.isModifierShift():
            return "shift"
    except Exception:
        pass
    return "none"


class GroomContext(omui.MPxContext):
    """Wambo Fur groom tool. Maya feeds it every mouse event (including
    pointer moves without a button) together with a Viewport 2.0 draw
    manager, which is used to draw the brush circle."""
    HELP = "Wambo Fur groom: drag to brush. B+drag: size, B+middle-drag: strength, [ ]: size, " \
           "Shift: smooth, Ctrl: invert."

    def __init__(self):
        omui.MPxContext.__init__(self)
        self.setTitleString("Wambo Fur Groom")
        try:
            self.setImage("hairPaint.png", omui.MPxContext.kImage1)
        except Exception:
            pass
        self.pos = None
        self.adjust = None      # (what, start_x, start_value)
        self.pressed = False

    # ------------------------------------------------------------ setup
    def toolOnSetup(self, event):
        from wambofur.maya import keys
        keys.start()
        self.setHelpString(self.HELP)
        self.setCursor(omui.MCursor.crossHairCursor)

    def toolOffCleanup(self):
        from wambofur.maya import keys
        keys.stop()
        self.pos = None
        self.adjust = None
        try:
            omui.M3dView.active3dView().refresh(False, True)
        except Exception:
            pass

    # ------------------------------------------------------------ drawing
    def _draw(self, dm):
        if dm is None or self.pos is None:
            return
        from wambofur.maya import brushes
        st = brushes.STATE
        x, y = self.pos
        if self.adjust is not None:
            x, y = self.adjust[3]           # keep the circle where B+drag started
        r = st.radius
        rgb = MODE_COLORS.get(st.mode, (1.0, 0.66, 0.23))
        col = om.MColor((rgb[0], rgb[1], rgb[2], 1.0))
        centre = om.MPoint(x, y, 0.0)
        dm.beginDrawable()
        dm.setColor(om.MColor((0.0, 0.0, 0.0, 0.6)))
        dm.setLineWidth(3.5)
        dm.circle2d(centre, r, False)
        dm.setColor(col)
        dm.setLineWidth(2.2 if self.pressed else 1.5)
        dm.circle2d(centre, r, False)
        # inner ring shows the strength
        dm.setColor(om.MColor((rgb[0], rgb[1], rgb[2], 0.5)))
        dm.setLineWidth(1.0)
        try:
            dm.setLineStyle(omr.MUIDrawManager.kDashed)
        except Exception:
            pass
        dm.circle2d(centre, max(r * max(st.strength, 0.05), 2.0), False)
        try:
            dm.setLineStyle(omr.MUIDrawManager.kSolid)
        except Exception:
            pass
        dm.setColor(col)
        dm.circle2d(centre, 1.8, True)
        label = brushes.LABELS.get(st.mode, st.mode).upper()
        if self.adjust is not None:
            label = "SIZE %d px" % int(r) if self.adjust[0] == "radius" else "STRENGTH %.2f" % st.strength
        dm.text2d(om.MPoint(x, y + r + 6.0, 0.0), label, omr.MUIDrawManager.kCenter)
        dm.endDrawable()

    def drawFeedback(self, drawMgr, context):
        self._draw(drawMgr)

    # ------------------------------------------------------------ events
    def doPtrMoved(self, event, drawMgr=None, context=None):
        self.pos = _event_xy(event)
        self._draw(drawMgr)

    def doPress(self, event, drawMgr=None, context=None):
        from wambofur.maya import brushes, keys
        self.pos = _event_xy(event)
        button = event.mouseButton()
        if keys.b_down():
            st = brushes.STATE
            if button == omui.MEvent.kMiddleMouse:
                self.adjust = ("strength", self.pos[0], st.strength, self.pos)
            else:
                self.adjust = ("radius", self.pos[0], st.radius, self.pos)
            self._draw(drawMgr)
            return
        if button != omui.MEvent.kLeftMouse:
            return
        self.pressed = True
        brushes.press(self.pos[0], self.pos[1], _event_mods(event))
        self._draw(drawMgr)

    def doDrag(self, event, drawMgr=None, context=None):
        from wambofur.maya import brushes
        self.pos = _event_xy(event)
        if self.adjust is not None:
            what, x0, v0, _ = self.adjust
            st = brushes.STATE
            if what == "radius":
                st.radius = max(4.0, min(1000.0, v0 + (self.pos[0] - x0)))
            else:
                st.strength = max(0.0, min(1.0, v0 + (self.pos[0] - x0) / 300.0))
            brushes.settings_changed()
            self._draw(drawMgr)
            return
        if self.pressed:
            brushes.drag(self.pos[0], self.pos[1], _event_mods(event))
        self._draw(drawMgr)

    def doRelease(self, event, drawMgr=None, context=None):
        from wambofur.maya import brushes
        self.pos = _event_xy(event)
        if self.adjust is not None:
            self.adjust = None
            brushes.settings_changed()
            self._draw(drawMgr)
            return
        if self.pressed:
            self.pressed = False
            brushes.release()
        self._draw(drawMgr)


class GroomContextCmd(omui.MPxContextCommand):
    NAME = "wamboFurGroomCtx"

    def __init__(self):
        omui.MPxContextCommand.__init__(self)

    @staticmethod
    def creator():
        return GroomContextCmd()

    def makeObj(self):
        return GroomContext()


# =========================================================================
# groom node: guides stored natively (no Maya curves), drawn in VP2
# =========================================================================
class GroomNode(omui.MPxLocatorNode):
    NAME = "wamboFurGroom"
    ID = om.MTypeId(0x0007F7A2)          # local/testing range
    DRAW_CLASSIFICATION = "drawdb/geometry/wamboFurGroom"
    DRAW_REGISTRANT = "wamboFurGroomOverride"

    @staticmethod
    def creator():
        return GroomNode()

    @staticmethod
    def initialize():
        tf = om.MFnTypedAttribute()
        nf = om.MFnNumericAttribute()
        attrs = []
        a = tf.create("inMesh", "inm", om.MFnData.kMesh)
        tf.storable = False
        tf.hidden = True
        attrs.append(a)
        a = tf.create("meshName", "msn", om.MFnData.kString)
        attrs.append(a)
        for ln, sn, kind in (("guidePoints", "gpt", om.MFnData.kDoubleArray),
                             ("guideCounts", "gct", om.MFnData.kIntArray),
                             ("guideTris", "gtr", om.MFnData.kIntArray),
                             ("guideBary", "gby", om.MFnData.kDoubleArray),
                             ("guideFrames", "gfr", om.MFnData.kDoubleArray)):
            # default = empty array, so a fresh node always has valid data
            if kind == om.MFnData.kIntArray:
                empty = om.MFnIntArrayData().create(om.MIntArray())
            else:
                empty = om.MFnDoubleArrayData().create(om.MDoubleArray())
            a = tf.create(ln, sn, kind, empty)
            tf.storable = True
            tf.hidden = True
            attrs.append(a)
        a = nf.createColor("rootColor", "rcl")
        nf.default = (0.15, 0.45, 0.95)
        attrs.append(a)
        a = nf.createColor("tipColor", "tcl")
        nf.default = (0.35, 0.95, 1.0)
        attrs.append(a)
        a = nf.create("showRoots", "shr", om.MFnNumericData.kBoolean, True)
        attrs.append(a)
        a = nf.create("lineWidth", "lwd", om.MFnNumericData.kFloat, 1.5)
        nf.setMin(1.0)
        nf.setMax(6.0)
        attrs.append(a)
        for a in attrs:
            om.MPxNode.addAttribute(a)


def _array(plug, fn_cls):
    try:
        obj = plug.asMObject()
        if obj.isNull():
            return []
        return fn_cls(obj).array()
    except RuntimeError:
        return []


class _GroomData(om.MUserData):
    def __init__(self):
        try:
            om.MUserData.__init__(self, False)
        except TypeError:
            om.MUserData.__init__(self)
        self.lines = om.MPointArray()
        self.colors = om.MColorArray()
        self.roots = om.MPointArray()
        self.show_roots = True
        self.width = 1.5
        self.active = False


class GroomOverride(omr.MPxDrawOverride):
    @staticmethod
    def creator(obj):
        return GroomOverride(obj)

    def __init__(self, obj):
        # not always dirty: redrawn only when the groom changes (fast tumbling)
        omr.MPxDrawOverride.__init__(self, obj, None, False)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kAllDevices

    def isBounded(self, objPath, cameraPath):
        return False

    def hasUIDrawables(self):
        return True

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData if isinstance(oldData, _GroomData) else _GroomData()
        fn = om.MFnDependencyNode(objPath.node())
        pts = _array(fn.findPlug("guidePoints", False), om.MFnDoubleArrayData)
        counts = _array(fn.findPlug("guideCounts", False), om.MFnIntArrayData)

        def col(name):
            p = fn.findPlug(name, False)
            return [p.child(i).asFloat() for i in range(3)]
        rc, tc = col("rootColor"), col("tipColor")
        status = omr.MGeometryUtilities.displayStatus(objPath)
        data.active = status in (omr.MGeometryUtilities.kLead, omr.MGeometryUtilities.kActive)
        if data.active:
            tc = [min(1.0, c * 0.5 + 0.5) for c in tc]
        data.show_roots = fn.findPlug("showRoots", False).asBool()
        data.width = fn.findPlug("lineWidth", False).asFloat()
        lines, colors, roots = om.MPointArray(), om.MColorArray(), om.MPointArray()
        i = 0
        cache = {}
        for n in counts:
            if i + 3 * n > len(pts):
                break
            gp = [om.MPoint(pts[i + 3 * k], pts[i + 3 * k + 1], pts[i + 3 * k + 2]) for k in range(n)]
            i += 3 * n
            if not gp:
                continue
            roots.append(gp[0])
            grad = cache.get(n)
            if grad is None:
                grad = []
                for k in range(n):
                    t = k / float(max(n - 1, 1))
                    grad.append(om.MColor((rc[0] + (tc[0] - rc[0]) * t, rc[1] + (tc[1] - rc[1]) * t,
                                           rc[2] + (tc[2] - rc[2]) * t, 1.0)))
                cache[n] = grad
            for k in range(n - 1):
                lines.append(gp[k])
                lines.append(gp[k + 1])
                colors.append(grad[k])
                colors.append(grad[k + 1])
        data.lines, data.colors, data.roots = lines, colors, roots
        return data

    def addUIDrawables(self, objPath, dm, frameContext, data):
        if not isinstance(data, _GroomData) or len(data.lines) == 0:
            return
        dm.beginDrawable()
        dm.setLineWidth(data.width + (0.5 if data.active else 0.0))
        dm.mesh(omr.MUIDrawManager.kLines, data.lines, None, data.colors)
        if data.show_roots and len(data.roots):
            dm.setColor(om.MColor((0.1, 0.25, 0.6, 1.0)))
            dm.setPointSize(3.0)
            dm.points(data.roots, False)
        dm.endDrawable()


# =========================================================================
def initializePlugin(obj):
    plugin = om.MFnPlugin(obj, VENDOR, VERSION, "Any")
    plugin.registerCommand(WamboFurCmd.NAME, WamboFurCmd.creator, WamboFurCmd.syntax)
    plugin.registerCommand(GroomEditCmd.NAME, GroomEditCmd.creator)
    plugin.registerContextCommand(GroomContextCmd.NAME, GroomContextCmd.creator)
    plugin.registerNode(GroomNode.NAME, GroomNode.ID, GroomNode.creator, GroomNode.initialize,
                        om.MPxNode.kLocatorNode, GroomNode.DRAW_CLASSIFICATION)
    omr.MDrawRegistry.registerDrawOverrideCreator(GroomNode.DRAW_CLASSIFICATION,
                                                  GroomNode.DRAW_REGISTRANT, GroomOverride.creator)
    if om.MGlobal.mayaState() != om.MGlobal.kBatch:
        from wambofur.maya import menu
        cmds.evalDeferred(menu.create)


def uninitializePlugin(obj):
    plugin = om.MFnPlugin(obj)
    try:
        from wambofur.maya import brushes, keys
        brushes.deactivate()
        keys.stop()
        if cmds.contextInfo(brushes.CONTEXT, exists=True):
            cmds.deleteUI(brushes.CONTEXT)
    except Exception:
        pass
    plugin.deregisterContextCommand(GroomContextCmd.NAME)
    omr.MDrawRegistry.deregisterDrawOverrideCreator(GroomNode.DRAW_CLASSIFICATION,
                                                    GroomNode.DRAW_REGISTRANT)
    plugin.deregisterNode(GroomNode.ID)
    plugin.deregisterCommand(GroomEditCmd.NAME)
    plugin.deregisterCommand(WamboFurCmd.NAME)
    try:
        from wambofur.maya import menu
        menu.remove()
    except Exception:
        pass
