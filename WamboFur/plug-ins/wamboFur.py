"""Wambo Fur - Maya plug-in entry point (Maya 2024+).

Load it from Windows > Settings/Preferences > Plug-in Manager, or
    cmds.loadPlugin("wamboFur.py")

Registers
  wamboFur               command (editor / update / export / render-time export)
  wamboFurGroomEdit      internal undoable command used by the groom brushes
  wamboFurBrushCursor    locator drawn in Viewport 2.0 (brush circle)
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
VERSION = "1.2.1"


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
# brush cursor locator (Viewport 2.0)
# =========================================================================
class BrushCursorNode(omui.MPxLocatorNode):
    NAME = "wamboFurBrushCursor"
    ID = om.MTypeId(0x0007F7A1)          # local/testing range
    DRAW_CLASSIFICATION = "drawdb/geometry/wamboFurBrushCursor"
    DRAW_REGISTRANT = "wamboFurBrushCursorOverride"

    @staticmethod
    def creator():
        return BrushCursorNode()

    @staticmethod
    def initialize():
        nf = om.MFnNumericAttribute()
        tf = om.MFnTypedAttribute()
        attrs = []
        for ln, sn, typ, dv in (("cursorX", "cux", om.MFnNumericData.kDouble, 0.0),
                                ("cursorY", "cuy", om.MFnNumericData.kDouble, 0.0),
                                ("radius", "rad", om.MFnNumericData.kDouble, 60.0),
                                ("strength", "stg", om.MFnNumericData.kDouble, 0.7),
                                ("active", "act", om.MFnNumericData.kBoolean, False),
                                ("pressed", "prs", om.MFnNumericData.kBoolean, False)):
            a = nf.create(ln, sn, typ, dv)
            nf.storable = False
            attrs.append(a)
        a = nf.createColor("cursorColor", "cuc")
        nf.default = (1.0, 0.66, 0.23)
        attrs.append(a)
        for ln, sn in (("camera", "cam"), ("label", "lbl")):
            a = tf.create(ln, sn, om.MFnData.kString)
            tf.storable = False
            attrs.append(a)
        for a in attrs:
            om.MPxNode.addAttribute(a)


class _CursorData(om.MUserData):
    def __init__(self):
        try:
            om.MUserData.__init__(self, False)
        except TypeError:          # newer Maya: no deleteAfterUse argument
            om.MUserData.__init__(self)
        self.active = False
        self.x = self.y = 0.0
        self.radius = 60.0
        self.strength = 0.7
        self.pressed = False
        self.camera = ""
        self.label = ""
        self.color = om.MColor((1.0, 0.66, 0.23, 1.0))


class BrushCursorOverride(omr.MPxDrawOverride):
    @staticmethod
    def creator(obj):
        return BrushCursorOverride(obj)

    def __init__(self, obj):
        omr.MPxDrawOverride.__init__(self, obj, None, True)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kAllDevices

    def isBounded(self, objPath, cameraPath):
        return False

    def hasUIDrawables(self):
        return True

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData if isinstance(oldData, _CursorData) else _CursorData()
        node = objPath.node()
        fn = om.MFnDependencyNode(node)

        def plug(n):
            return fn.findPlug(n, False)
        data.active = plug("active").asBool()
        data.pressed = plug("pressed").asBool()
        data.x = plug("cursorX").asDouble()
        data.y = plug("cursorY").asDouble()
        data.radius = plug("radius").asDouble()
        data.strength = plug("strength").asDouble()
        data.camera = plug("camera").asString()
        data.label = plug("label").asString()
        c = plug("cursorColor")
        data.color = om.MColor((c.child(0).asFloat(), c.child(1).asFloat(), c.child(2).asFloat(), 1.0))
        return data

    def addUIDrawables(self, objPath, dm, frameContext, data):
        if not isinstance(data, _CursorData) or not data.active:
            return
        try:
            cam = frameContext.getCurrentCameraPath().fullPathName()
        except Exception:
            cam = data.camera
        if data.camera and cam and cam != data.camera:
            return  # only draw in the viewport under the mouse
        centre = om.MPoint(data.x, data.y, 0.0)
        dm.beginDrawable()
        # soft outer shadow for readability on any background
        dm.setColor(om.MColor((0.0, 0.0, 0.0, 0.55)))
        dm.setLineWidth(3.5)
        dm.circle2d(centre, data.radius, False)
        dm.setColor(data.color)
        dm.setLineWidth(2.0 if data.pressed else 1.5)
        dm.circle2d(centre, data.radius, False)
        # inner falloff ring = strength
        col = om.MColor((data.color.r, data.color.g, data.color.b, 0.45))
        dm.setColor(col)
        dm.setLineWidth(1.0)
        dm.setLineStyle(omr.MUIDrawManager.kDashed)
        dm.circle2d(centre, max(data.radius * (1.0 - 0.5 * data.strength), 2.0), False)
        dm.setLineStyle(omr.MUIDrawManager.kSolid)
        dm.setColor(data.color)
        dm.circle2d(centre, 1.8, True)
        if data.label:
            dm.text2d(om.MPoint(data.x, data.y + data.radius + 6, 0.0), data.label,
                      omr.MUIDrawManager.kCenter)
        dm.endDrawable()


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
    plugin.registerNode(BrushCursorNode.NAME, BrushCursorNode.ID, BrushCursorNode.creator,
                        BrushCursorNode.initialize, om.MPxNode.kLocatorNode,
                        BrushCursorNode.DRAW_CLASSIFICATION)
    omr.MDrawRegistry.registerDrawOverrideCreator(BrushCursorNode.DRAW_CLASSIFICATION,
                                                  BrushCursorNode.DRAW_REGISTRANT,
                                                  BrushCursorOverride.creator)
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
        from wambofur.maya import brushes
        brushes.deactivate(silent=True)
    except Exception:
        pass
    omr.MDrawRegistry.deregisterDrawOverrideCreator(BrushCursorNode.DRAW_CLASSIFICATION,
                                                    BrushCursorNode.DRAW_REGISTRANT)
    plugin.deregisterNode(BrushCursorNode.ID)
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
