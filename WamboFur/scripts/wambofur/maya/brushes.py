"""Interactive groom brushes operating on guide curves in the viewport.

Modes: comb, length, smooth, puff, straighten, move, cut, add, remove.

  Shift  ... temporary Smooth            Ctrl ... invert (shorten / flatten)
  B + LMB drag ... brush radius          B + MMB drag ... brush strength

A whole stroke (press -> release) is ONE undo step (wamboFurGroomEdit).
"""
import math
import time

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui

from ..core.mathutil import (v_sub, v_add, v_scale, v_lerp, v_madd, v_norm, v_dot, seg_lengths,
                             preserve_lengths, polyline_length, resample)
from . import scene
from .system import NoUndo, GUIDE_MESH_ATTR

CONTEXT = "wamboFurGroomCtx"
MODES = ("comb", "length", "smooth", "puff", "straighten", "move", "cut", "add", "remove")


class BrushState(object):
    def __init__(self):
        self.mode = "comb"
        self.radius = 60.0          # pixels
        self.strength = 0.7
        self.preserve_length = True
        self.group = None           # guides group being groomed
        self.on_stroke = None       # callback(group, final) during / after a stroke
        self.on_settings = None     # callback() when radius/strength change (B+drag)
        self.live_interval = 0.15   # seconds between live fur updates while dragging
        # per stroke
        self.curves = []
        self.last = None
        self.cursor = None
        self.last_live = 0.0


STATE = BrushState()
_PENDING = []
_CACHE = {"group": None, "shapes": None, "stamp": None, "fns": None}


class _Curve(object):
    __slots__ = ("shape", "fn", "dag", "orig", "pts", "rest", "screen", "dirty", "n")


# ------------------------------------------------------------- utilities
def _view():
    return omui.M3dView.active3dView()


def _to_screen(view, p):
    r = view.worldToView(om.MPoint(p[0], p[1], p[2]))
    return (float(r[0]), float(r[1]))


def _ray(view, sx, sy):
    near, direction = om.MPoint(), om.MVector()
    view.viewToWorld(int(round(sx)), int(round(sy)), near, direction)
    return (near.x, near.y, near.z), (direction.x, direction.y, direction.z)


def _camera_forward(view):
    m = view.getCamera().inclusiveMatrix()
    return v_norm((-m.getElement(2, 0), -m.getElement(2, 1), -m.getElement(2, 2)))


def _hit(ray, fwd, p):
    """Point of `ray` on the camera-facing plane through p."""
    near, d = ray
    den = v_dot(d, fwd)
    if abs(den) < 1e-9:
        return p
    t = v_dot(v_sub(p, near), fwd) / den
    return v_madd(near, d, t)


def _falloff(d, r):
    if d >= r:
        return 0.0
    x = 1.0 - d / r
    return x * x * (3.0 - 2.0 * x)


def _modifier():
    try:
        return cmds.draggerContext(CONTEXT, query=True, modifier=True) or "none"
    except RuntimeError:
        return "none"


def _guide_mesh():
    if STATE.group and cmds.objExists(STATE.group) and \
            cmds.attributeQuery(GUIDE_MESH_ATTR, node=STATE.group, exists=True):
        return scene.mesh_shape(cmds.getAttr(STATE.group + "." + GUIDE_MESH_ATTR))
    return None


def _shapes():
    """Guide curve shapes with cached MFn objects (rebuilt when the group changes)."""
    shapes = scene.curve_shapes(STATE.group)
    stamp = (STATE.group, tuple(shapes))
    if _CACHE["stamp"] != stamp:
        fns = []
        for s in shapes:
            dag = scene.dag_path(s)
            fns.append((s, dag, om.MFnNurbsCurve(dag)))
        _CACHE["stamp"] = stamp
        _CACHE["fns"] = fns
    return _CACHE["fns"]


def _load_curves(view):
    out = []
    for shp, dag, fn in _shapes():
        c = _Curve()
        c.shape, c.dag, c.fn = shp, dag, fn
        c.orig = [(p.x, p.y, p.z) for p in fn.cvPositions(om.MSpace.kWorld)]
        c.pts = list(c.orig)
        c.n = len(c.pts)
        c.rest = seg_lengths(c.pts)
        c.screen = [_to_screen(view, p) for p in c.pts]
        c.dirty = False
        out.append(c)
    return out


def _write(c, view):
    c.fn.setCVPositions([om.MPoint(*p) for p in c.pts], om.MSpace.kWorld)
    c.fn.updateCurve()
    c.screen = [_to_screen(view, p) for p in c.pts]
    c.dirty = True


class _NormalCache(object):
    def __init__(self):
        self.fn = None
        shape = _guide_mesh()
        if shape:
            self.fn = om.MFnMesh(scene.dag_path(shape))
        self.cache = {}

    def get(self, key, p):
        n = self.cache.get(key)
        if n is None and self.fn is not None:
            v, _ = self.fn.getClosestNormal(om.MPoint(*p), om.MSpace.kWorld)
            n = v_norm((v.x, v.y, v.z))
            self.cache[key] = n
        return n


# ---------------------------------------------------------------- undo
def take_pending():
    global _PENDING
    edits, _PENDING = _PENDING, []
    return edits


def apply_edits(edits, which):
    """which: 1 = before, 2 = after (called by wamboFurGroomEdit undo/redo)."""
    for e in edits:
        shape, pts = e[0], e[which]
        if not cmds.objExists(shape):
            continue
        try:
            fn = om.MFnNurbsCurve(scene.dag_path(shape))
            fn.setCVPositions([om.MPoint(*p) for p in pts], om.MSpace.kWorld)
            fn.updateCurve()
        except RuntimeError:
            pass
    _CACHE["stamp"] = None
    if STATE.on_stroke:
        try:
            STATE.on_stroke(STATE.group, True)
        except Exception:
            pass


def _commit():
    global _PENDING
    dirty = [c for c in STATE.curves if c.dirty]
    if not dirty:
        return
    _PENDING = [(c.shape, list(c.orig), list(c.pts)) for c in dirty]
    if cmds.pluginInfo("wamboFur", q=True, loaded=True):
        cmds.wamboFurGroomEdit()          # one undo step, no per-CV setAttr
        return
    # fallback without the plug-in: undoable setAttr
    take_pending()
    with NoUndo():
        for c in dirty:
            c.fn.setCVPositions([om.MPoint(*p) for p in c.orig], om.MSpace.kWorld)
            c.fn.updateCurve()
    cmds.undoInfo(openChunk=True, chunkName="wamboFurGroom")
    try:
        for c in dirty:
            inv = c.dag.exclusiveMatrixInverse()
            for i, p in enumerate(c.pts):
                lp = om.MPoint(*p) * inv
                cmds.setAttr("%s.controlPoints[%d]" % (c.shape, i), lp.x, lp.y, lp.z)
    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------------- strokes
def _press():
    if not STATE.group or not cmds.objExists(STATE.group):
        cmds.warning("Wambo Fur: no guide group to groom - create guides first")
        return
    x, y, _ = cmds.draggerContext(CONTEXT, query=True, anchorPoint=True)
    STATE.last = (float(x), float(y))
    if STATE.cursor:
        STATE.cursor.set_pressed(True)
    if STATE.mode == "add":
        _add_guide(*STATE.last)
        return
    view = _view()
    STATE.curves = _load_curves(view)
    STATE.normals = _NormalCache()
    STATE.fwd = _camera_forward(view)
    STATE.last_live = time.time()
    if STATE.mode in ("remove", "cut"):
        _apply(view, STATE.last, STATE.last)


def _drag():
    if STATE.last is None or STATE.mode == "add":
        return
    x, y, _ = cmds.draggerContext(CONTEXT, query=True, dragPoint=True)
    cur = (float(x), float(y))
    if STATE.cursor and STATE.cursor.plugs:
        STATE.cursor.plugs.set(cursorX=cur[0], cursorY=cur[1], active=True)
    view = _view()
    _apply(view, STATE.last, cur)
    STATE.last = cur
    view.refresh(False, False)
    now = time.time()
    if STATE.on_stroke and now - STATE.last_live > STATE.live_interval:
        STATE.last_live = now
        try:
            STATE.on_stroke(STATE.group, False)
        except Exception as e:
            cmds.warning("Wambo Fur: %s" % e)


def _release():
    if STATE.cursor:
        STATE.cursor.set_pressed(False)
    if STATE.mode not in ("add", "remove"):
        _commit()
    STATE.curves = []
    STATE.last = None
    if STATE.on_stroke:
        try:
            STATE.on_stroke(STATE.group, True)
        except Exception as e:  # never break the tool because of the UI
            cmds.warning("Wambo Fur: %s" % e)


def _apply(view, prev, cur):
    R, S = STATE.radius, STATE.strength
    mod = _modifier()
    mode = STATE.mode
    invert = "ctrl" in mod
    if "shift" in mod and mode not in ("add", "remove", "cut"):
        mode = "smooth"
    mx, my = cur
    dx, dy = cur[0] - prev[0], cur[1] - prev[1]
    moved = dx != 0.0 or dy != 0.0
    ray0 = ray1 = None
    if mode in ("comb", "move") and moved:
        ray0, ray1 = _ray(view, prev[0], prev[1]), _ray(view, cur[0], cur[1])
    fwd = STATE.fwd
    removed = []
    R2 = R * R
    for c in STATE.curves:
        # quick reject: no CV inside the brush
        scr = c.screen
        hit = False
        for sx, sy in scr:
            if (sx - mx) * (sx - mx) + (sy - my) * (sy - my) < R2:
                hit = True
                break
        if not hit:
            continue
        n = c.n
        ws = [_falloff(math.hypot(sx - mx, sy - my), R) for sx, sy in scr]
        wmax = max(ws)
        if mode == "remove":
            if ws[0] > 0.0:
                removed.append(c)
            continue
        pts = list(c.pts)
        if mode in ("comb", "move"):
            if not moved:
                continue
            for i in range(1, n):
                # comb: tips move most; move: brush area moves as a whole
                w = ws[i] * S * ((i / float(n - 1)) ** 0.5 if mode == "comb" else 1.0)
                if w <= 0.0:
                    continue
                p = pts[i]
                delta = v_sub(_hit(ray1, fwd, p), _hit(ray0, fwd, p))
                pts[i] = v_madd(p, delta, w)
            if STATE.preserve_length and mode == "comb":
                pts = preserve_lengths(pts, c.rest)
            elif mode == "move":
                c.rest = seg_lengths(pts)
        elif mode == "length":
            amount = (dx if moved else 0.0) * 0.01 * S * wmax
            if invert:
                amount = -abs(amount) if amount else -0.02 * S * wmax
            f = max(0.05, 1.0 + amount)
            root = pts[0]
            pts = [v_madd(root, v_sub(p, root), f) for p in pts]
            c.rest = seg_lengths(pts)
        elif mode == "smooth":
            for _ in range(2):
                for i in range(1, n - 1):
                    w = ws[i] * S * 0.5
                    if w > 0.0:
                        pts[i] = v_lerp(pts[i], v_scale(v_add(pts[i - 1], pts[i + 1]), 0.5), w)
                if n > 2 and ws[-1] > 0.0:
                    pts[-1] = v_lerp(pts[-1], v_add(pts[-2], v_sub(pts[-2], pts[-3])), ws[-1] * S * 0.5)
            if STATE.preserve_length:
                pts = preserve_lengths(pts, c.rest)
        elif mode in ("puff", "straighten"):
            if mode == "puff":
                axis = STATE.normals.get(c.shape, pts[0]) or v_norm(v_sub(pts[1], pts[0]))
                if invert:      # flatten: push toward the surface tangent plane
                    d = v_norm(v_sub(pts[-1], pts[0]))
                    axis = v_norm(v_sub(d, v_scale(axis, v_dot(d, axis))), fallback=axis)
            else:
                axis = v_norm(v_sub(pts[1], pts[0]))
            L = polyline_length(pts)
            straight = [v_madd(pts[0], axis, L * i / float(n - 1)) for i in range(n)]
            k = wmax * S * 0.15
            pts = [v_lerp(p, q, k) for p, q in zip(pts, straight)]
            if STATE.preserve_length:
                pts = preserve_lengths(pts, c.rest)
        elif mode == "cut":
            hit_i = max(range(1, n), key=lambda i: ws[i]) if n > 1 else 0
            if ws[hit_i] <= 0.0:
                continue
            frac = polyline_length(pts[:hit_i + 1]) / max(polyline_length(pts), 1e-9)
            pts = resample(pts, n, max(frac, 0.02))
            c.rest = seg_lengths(pts)
        c.pts = pts
        _write(c, view)
    if removed:
        names = [cmds.listRelatives(c.shape, parent=True, fullPath=True)[0] for c in removed]
        cmds.delete(names)
        STATE.curves = [c for c in STATE.curves if c not in removed]
        _CACHE["stamp"] = None


def _add_guide(sx, sy):
    shape = _guide_mesh()
    if not shape:
        cmds.warning("Wambo Fur: guide group has no source mesh")
        return
    view = _view()
    near, d = _ray(view, sx, sy)
    fn = om.MFnMesh(scene.dag_path(shape))
    hit = fn.closestIntersection(om.MFloatPoint(*near), om.MFloatVector(*d),
                                 om.MSpace.kWorld, 1e7, False)
    if not hit or hit[2] < 0:
        return
    p = (hit[0].x, hit[0].y, hit[0].z)
    v, _ = fn.getClosestNormal(om.MPoint(*p), om.MSpace.kWorld)
    nrm = v_norm((v.x, v.y, v.z))
    fns = _shapes()
    if fns:
        sample = [[(q.x, q.y, q.z) for q in f.cvPositions(om.MSpace.kWorld)] for _, _, f in fns[:20]]
        L = sum(polyline_length(s) for s in sample) / len(sample)
        ncv = len(sample[0])
    else:
        L, ncv = 1.0, 7
    pts = [v_madd(p, nrm, L * i / float(ncv - 1)) for i in range(ncv)]
    crv = cmds.curve(degree=3 if ncv >= 4 else 1, point=pts, name="wfGuide1")
    cmds.parent(crv, STATE.group)
    _CACHE["stamp"] = None


# ---------------------------------------------------------------- public
def activate(mode, group, on_stroke=None, on_settings=None):
    from .cursor import BrushCursor
    if mode not in MODES:
        raise ValueError(mode)
    STATE.mode = mode
    STATE.group = group
    STATE.on_stroke = on_stroke
    STATE.on_settings = on_settings
    _CACHE["stamp"] = None
    if cmds.draggerContext(CONTEXT, exists=True):
        cmds.deleteUI(CONTEXT)
    cmds.draggerContext(CONTEXT, pressCommand=_press, dragCommand=_drag, releaseCommand=_release,
                        cursor="crossHair", space="screen",
                        undoMode="sequence", name=CONTEXT)
    cmds.setToolTo(CONTEXT)
    if STATE.cursor is None:
        STATE.cursor = BrushCursor(STATE)
        if not STATE.cursor.start():
            STATE.cursor = None
    else:
        STATE.cursor.sync_style()
    cmds.scriptJob(event=["ToolChanged", _on_tool_changed], runOnce=True)
    cmds.inViewMessage(amg="Wambo Fur: <hl>%s</hl>  ·  B+drag radius  ·  Shift smooth  ·  "
                           "Ctrl invert" % mode, pos="topCenter", fade=True)


def _on_tool_changed():
    if cmds.currentCtx() != CONTEXT:
        deactivate(silent=True)
    else:
        cmds.scriptJob(event=["ToolChanged", _on_tool_changed], runOnce=True)


def sync_settings():
    if STATE.cursor:
        STATE.cursor.sync_style()


def deactivate(silent=False):
    if STATE.cursor is not None:
        STATE.cursor.stop()
        STATE.cursor = None
    if not silent and cmds.currentCtx() == CONTEXT:
        cmds.setToolTo("selectSuperContext")
