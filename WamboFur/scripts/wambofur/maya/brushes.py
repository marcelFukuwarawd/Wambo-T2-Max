"""Interactive groom brushes operating on guide curves in the viewport.

Modes: comb, length, smooth, puff, cut, add, remove.
A whole stroke (press -> release) is one undo step.
"""
import math

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui

from ..core.mathutil import (v_sub, v_add, v_scale, v_lerp, v_madd, v_norm, seg_lengths,
                             preserve_lengths, polyline_length, resample)
from . import scene
from .system import NoUndo, GUIDE_MESH_ATTR

CONTEXT = "wamboFurGroomCtx"
MODES = ("comb", "length", "smooth", "puff", "cut", "add", "remove")


class BrushState(object):
    def __init__(self):
        self.mode = "comb"
        self.radius = 60.0          # pixels
        self.strength = 0.7
        self.preserve_length = True
        self.group = None           # guides group being groomed
        self.on_stroke = None       # callback(group) after each stroke
        # per stroke
        self.curves = []            # [_Curve]
        self.last = None


STATE = BrushState()


class _Curve(object):
    __slots__ = ("shape", "fn", "dag", "orig", "pts", "rest", "screen", "dirty")


def _view():
    return omui.M3dView.active3dView()


def _to_screen(view, p):
    r = view.worldToView(om.MPoint(p[0], p[1], p[2]))
    return (float(r[0]), float(r[1]))


def _unproject(view, sx, sy, plane_point):
    """World point under pixel (sx, sy) on the camera-facing plane through plane_point."""
    near, direction = om.MPoint(), om.MVector()
    view.viewToWorld(int(sx), int(sy), near, direction)
    cam = view.getCamera()
    m = cam.inclusiveMatrix()
    fwd = om.MVector(-m.getElement(2, 0), -m.getElement(2, 1), -m.getElement(2, 2))
    denom = direction * fwd
    if abs(denom) < 1e-9:
        return plane_point
    pp = om.MPoint(plane_point[0], plane_point[1], plane_point[2])
    t = ((pp - near) * fwd) / denom
    hit = near + direction * t
    return (hit.x, hit.y, hit.z)


def _mouse():
    x, y, _ = cmds.draggerContext(CONTEXT, query=True, dragPoint=True)
    return float(x), float(y)


def _load_curves():
    view = _view()
    out = []
    for shp in scene.curve_shapes(STATE.group):
        c = _Curve()
        c.shape = shp
        c.dag = scene.dag_path(shp)
        c.fn = om.MFnNurbsCurve(c.dag)
        c.orig = [(p.x, p.y, p.z) for p in c.fn.cvPositions(om.MSpace.kWorld)]
        c.pts = list(c.orig)
        c.rest = seg_lengths(c.pts)
        c.screen = [_to_screen(view, p) for p in c.pts]
        c.dirty = False
        out.append(c)
    return out


def _falloff(d, r):
    if d >= r:
        return 0.0
    x = 1.0 - d / r
    return x * x * (3.0 - 2.0 * x)


def _write(c, view):
    with NoUndo():
        c.fn.setCVPositions([om.MPoint(*p) for p in c.pts], om.MSpace.kWorld)
        c.fn.updateCurve()
    c.screen = [_to_screen(view, p) for p in c.pts]
    c.dirty = True


def _surface_normal(p):
    mesh = None
    if STATE.group and cmds.attributeQuery(GUIDE_MESH_ATTR, node=STATE.group, exists=True):
        mesh = cmds.getAttr(STATE.group + "." + GUIDE_MESH_ATTR)
    shape = scene.mesh_shape(mesh) if mesh else None
    if not shape:
        return None
    fn = om.MFnMesh(scene.dag_path(shape))
    n, _ = fn.getClosestNormal(om.MPoint(*p), om.MSpace.kWorld)
    return v_norm((n.x, n.y, n.z))


# ---------------------------------------------------------------- strokes
def _press():
    if not STATE.group or not cmds.objExists(STATE.group):
        cmds.warning("Wambo Fur: no guide group to groom - create guides first")
        return
    x, y, _ = cmds.draggerContext(CONTEXT, query=True, anchorPoint=True)
    STATE.last = (float(x), float(y))
    if STATE.mode == "add":
        _add_guide(*STATE.last)
        return
    STATE.curves = _load_curves()
    if STATE.mode in ("remove", "cut"):
        _apply(STATE.last, (0.0, 0.0))


def _drag():
    if STATE.last is None or STATE.mode == "add":
        return
    cur = _mouse()
    delta = (cur[0] - STATE.last[0], cur[1] - STATE.last[1])
    _apply(cur, delta)
    STATE.last = cur
    cmds.refresh(currentView=True, force=True)


def _release():
    if STATE.mode not in ("add", "remove"):
        _commit()
    STATE.curves = []
    STATE.last = None
    if STATE.on_stroke:
        try:
            STATE.on_stroke(STATE.group)
        except Exception as e:  # never break the tool because of the UI
            cmds.warning("Wambo Fur: %s" % e)


def _commit():
    """Turn the interactive (non-undoable) edits into one undoable step."""
    dirty = [c for c in STATE.curves if c.dirty]
    if not dirty:
        return
    finals = [list(c.pts) for c in dirty]
    with NoUndo():
        for c in dirty:
            c.fn.setCVPositions([om.MPoint(*p) for p in c.orig], om.MSpace.kWorld)
            c.fn.updateCurve()
    cmds.undoInfo(openChunk=True, chunkName="wamboFurGroom")
    try:
        for c, pts in zip(dirty, finals):
            inv = c.dag.exclusiveMatrixInverse()
            for i, p in enumerate(pts):
                lp = om.MPoint(*p) * inv
                cmds.setAttr("%s.controlPoints[%d]" % (c.shape, i), lp.x, lp.y, lp.z)
    finally:
        cmds.undoInfo(closeChunk=True)


def _apply(mouse, delta):
    view = _view()
    R, S, mode = STATE.radius, STATE.strength, STATE.mode
    mx, my = mouse
    removed = []
    for c in STATE.curves:
        n = len(c.pts)
        ws = [_falloff(math.hypot(sx - mx, sy - my), R) for sx, sy in c.screen]
        wmax = max(ws) if ws else 0.0
        if wmax <= 0.0:
            continue
        if mode == "remove":
            if ws[0] > 0.0:
                removed.append(c)
            continue
        pts = list(c.pts)
        if mode == "comb":
            if delta == (0.0, 0.0):
                continue
            for i in range(1, n):
                w = ws[i] * S * (i / float(n - 1)) ** 0.5
                if w <= 0.0:
                    continue
                sx, sy = c.screen[i]
                target = _unproject(view, sx + delta[0], sy + delta[1], pts[i])
                pts[i] = v_lerp(pts[i], target, w)
            if STATE.preserve_length:
                pts = preserve_lengths(pts, c.rest)
        elif mode == "length":
            f = 1.0 + delta[0] * 0.01 * S * wmax
            root = pts[0]
            pts = [v_madd(root, v_sub(p, root), f) for p in pts]
            c.rest = seg_lengths(pts)
        elif mode == "smooth":
            for _ in range(2):
                for i in range(1, n - 1):
                    w = ws[i] * S * 0.5
                    avg = v_scale(v_add(pts[i - 1], pts[i + 1]), 0.5)
                    pts[i] = v_lerp(pts[i], avg, w)
                if n > 2:
                    tip_dir = v_sub(pts[-2], pts[-3])
                    pts[-1] = v_lerp(pts[-1], v_add(pts[-2], tip_dir), ws[-1] * S * 0.5)
            if STATE.preserve_length:
                pts = preserve_lengths(pts, c.rest)
        elif mode == "puff":
            nrm = _surface_normal(pts[0]) or v_norm(v_sub(pts[1], pts[0]))
            L = polyline_length(pts)
            straight = [v_madd(pts[0], nrm, L * i / float(n - 1)) for i in range(n)]
            pts = [v_lerp(p, q, wmax * S * 0.15) for p, q in zip(pts, straight)]
            if STATE.preserve_length:
                pts = preserve_lengths(pts, c.rest)
        elif mode == "cut":
            hit = max(range(1, n), key=lambda i: ws[i]) if n > 1 else 0
            if ws[hit] <= 0.0:
                continue
            cum = polyline_length(pts[:hit + 1]) / max(polyline_length(pts), 1e-9)
            pts = resample(pts, n, max(cum, 0.02))
            c.rest = seg_lengths(pts)
        c.pts = pts
        _write(c, view)
    if removed:
        names = [cmds.listRelatives(c.shape, parent=True, fullPath=True)[0] for c in removed]
        cmds.delete(names)
        STATE.curves = [c for c in STATE.curves if c not in removed]


def _add_guide(sx, sy):
    mesh = cmds.getAttr(STATE.group + "." + GUIDE_MESH_ATTR) \
        if cmds.attributeQuery(GUIDE_MESH_ATTR, node=STATE.group, exists=True) else None
    shape = scene.mesh_shape(mesh) if mesh else None
    if not shape:
        cmds.warning("Wambo Fur: guide group has no source mesh")
        return
    view = _view()
    near, direction = om.MPoint(), om.MVector()
    view.viewToWorld(int(sx), int(sy), near, direction)
    fn = om.MFnMesh(scene.dag_path(shape))
    hit = fn.closestIntersection(om.MFloatPoint(near.x, near.y, near.z),
                                 om.MFloatVector(direction.x, direction.y, direction.z),
                                 om.MSpace.kWorld, 1e7, False)
    if not hit or hit[0] is None or hit[2] < 0:
        return
    p = (hit[0].x, hit[0].y, hit[0].z)
    nrm = _surface_normal(p) or (0.0, 1.0, 0.0)
    curves = scene.curve_shapes(STATE.group)
    if curves:
        sample = [scene.read_curve(s, 8) for s in curves[:20]]
        L = sum(polyline_length(s) for s in sample) / len(sample)
        ncv = len(om.MFnNurbsCurve(scene.dag_path(curves[0])).cvPositions())
    else:
        L, ncv = 1.0, 7
    pts = [v_madd(p, nrm, L * i / float(ncv - 1)) for i in range(ncv)]
    crv = cmds.curve(degree=3 if ncv >= 4 else 1, point=pts, name="wfGuide1")
    cmds.parent(crv, STATE.group)


# ---------------------------------------------------------------- public
def activate(mode, group, on_stroke=None):
    if mode not in MODES:
        raise ValueError(mode)
    STATE.mode = mode
    STATE.group = group
    STATE.on_stroke = on_stroke
    if cmds.draggerContext(CONTEXT, exists=True):
        cmds.deleteUI(CONTEXT)
    cmds.draggerContext(CONTEXT, pressCommand=_press, dragCommand=_drag, releaseCommand=_release,
                        cursor="crossHair", space="screen", undoMode="sequence",
                        name=CONTEXT)
    cmds.setToolTo(CONTEXT)
    cmds.inViewMessage(amg="Wambo Fur brush: <hl>%s</hl>" % mode, pos="topCenter", fade=True)


def deactivate():
    cmds.setToolTo("selectSuperContext")
