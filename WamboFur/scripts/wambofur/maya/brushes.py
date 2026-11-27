"""Interactive groom tools working directly on the native groom node
(wamboFurGroom) - guides are never converted to Maya curves.

Tools
  create : add (one guide per click), paint (spray guides while dragging)
  delete : remove
  shape  : comb, move, smooth, puff, straighten, clump
  length : length (scale), cut

  Shift ... temporary Smooth            Ctrl ... invert (shorten / flatten / unclump)
  B + LMB drag ... radius               B + MMB drag ... strength

A whole stroke (press -> release) is ONE undo step (wamboFurGroomEdit).
"""
import math
import random
import time

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui

from ..core.guides import bound_guide, bind_point, inherit_shape, tri_frame
from ..core.mathutil import (v_sub, v_add, v_scale, v_lerp, v_madd, v_norm, v_dot, seg_lengths,
                             preserve_lengths, polyline_length, resample)
from ..core.spatial import HashGrid
from . import scene, groom

CONTEXT = "wamboFurGroomCtx"
MODES = ("add", "paint", "remove", "comb", "move", "smooth", "puff", "straighten", "clump",
         "length", "cut")
LABELS = {"add": "Add Guide", "paint": "Paint Guides", "remove": "Delete", "comb": "Comb",
          "move": "Move", "smooth": "Smooth", "puff": "Puff", "straighten": "Straighten",
          "clump": "Clump", "length": "Scale", "cut": "Cut"}


class BrushState(object):
    def __init__(self):
        self.mode = "comb"
        self.radius = 60.0          # pixels
        self.strength = 0.7
        self.preserve_length = True
        self.front_only = True      # ignore guides on the back side of the mesh
        self.new_length = 1.0       # length of created guides when nothing to inherit
        self.new_segments = 6
        self.spacing = 0.0          # min distance between painted guides (0 = auto)
        self.inherit = True         # new guides copy the groom of their neighbours
        self.group = None           # groom transform / shape being edited
        self.on_stroke = None       # callback(group, final) during / after a stroke
        self.on_settings = None     # callback() when radius/strength change (B+drag)
        self.live_interval = 0.15
        # per stroke
        self.shape = None
        self.guides = []
        self.items = []
        self.before = None
        self.last = None
        self.cursor = None
        self.last_live = 0.0
        self.changed = False


STATE = BrushState()
_PENDING = []


class _Item(object):
    """Per-stroke view of one guide."""
    __slots__ = ("g", "screen", "rest", "visible")


# ------------------------------------------------------------- utilities
def _view():
    return omui.M3dView.active3dView()


def _ray(view, sx, sy):
    near, direction = om.MPoint(), om.MVector()
    view.viewToWorld(int(round(sx)), int(round(sy)), near, direction)
    return (near.x, near.y, near.z), (direction.x, direction.y, direction.z)


def _camera(view):
    m = view.getCamera().inclusiveMatrix()
    fwd = v_norm((-m.getElement(2, 0), -m.getElement(2, 1), -m.getElement(2, 2)))
    pos = (m.getElement(3, 0), m.getElement(3, 1), m.getElement(3, 2))
    ortho = om.MFnCamera(view.getCamera()).isOrtho()
    return pos, fwd, ortho


class _Projector(object):
    """World -> viewport pixels. Uses the view matrices (pure Python, fast)
    and falls back to M3dView.worldToView if they do not match."""

    def __init__(self, view):
        self.view = view
        self.M = None
        try:
            mv = view.modelViewMatrix()
            pj = view.projectionMatrix()
            m = mv * pj
            self.M = [[m.getElement(r, c) for c in range(4)] for r in range(4)]
            self.w = float(view.portWidth())
            self.h = float(view.portHeight())
            # validate against Maya on a point in front of the camera
            pos, fwd, _ = _camera(view)
            probe = v_madd(pos, fwd, 10.0)
            a = self._fast(probe)
            b = self._slow(probe)
            if a is None or abs(a[0] - b[0]) > 1.5 or abs(a[1] - b[1]) > 1.5:
                self.M = None
        except Exception:
            self.M = None

    def _slow(self, p):
        r = self.view.worldToView(om.MPoint(p[0], p[1], p[2]))
        return (float(r[0]), float(r[1]))

    def _fast(self, p):
        M = self.M
        x, y, z = p
        cx = x * M[0][0] + y * M[1][0] + z * M[2][0] + M[3][0]
        cy = x * M[0][1] + y * M[1][1] + z * M[2][1] + M[3][1]
        cw = x * M[0][3] + y * M[1][3] + z * M[2][3] + M[3][3]
        if abs(cw) < 1e-12:
            return None
        return ((cx / cw * 0.5 + 0.5) * self.w, (cy / cw * 0.5 + 0.5) * self.h)

    def __call__(self, p):
        if self.M is not None:
            r = self._fast(p)
            if r is not None:
                return r
        return self._slow(p)


def _intersect(fn, near, d):
    """Ray/mesh hit -> (point, face) or None (works across Maya versions)."""
    src, direc = om.MFloatPoint(*near), om.MFloatVector(*d)
    accel = getattr(STATE, "accel", None)
    try:
        hit = fn.closestIntersection(src, direc, om.MSpace.kWorld, 1e7, False,
                                     None, None, False, accel)
    except (TypeError, RuntimeError, ValueError):
        try:
            hit = fn.closestIntersection(src, direc, om.MSpace.kWorld, 1e7, False)
        except RuntimeError:
            return None
    if not hit or hit[0] is None or hit[2] is None or hit[2] < 0:
        return None
    return (hit[0].x, hit[0].y, hit[0].z), hit[2]


def _hit_plane(ray, fwd, p):
    near, d = ray
    den = v_dot(d, fwd)
    if abs(den) < 1e-9:
        return p
    return v_madd(near, d, v_dot(v_sub(p, near), fwd) / den)


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


# ---------------------------------------------------------------- undo
def push_pending(edits):
    global _PENDING
    _PENDING = list(edits)


def take_pending():
    global _PENDING
    edits, _PENDING = _PENDING, []
    return edits


def apply_edits(edits, which):
    """which: 1 = before, 2 = after (called by wamboFurGroomEdit undo/redo)."""
    for shape, before, after in edits:
        if cmds.objExists(shape):
            groom.restore(shape, before if which == 1 else after)
    if STATE.on_stroke:
        try:
            STATE.on_stroke(STATE.group, True)
        except Exception:
            pass


# ------------------------------------------------------------ stroke setup
def _begin(view):
    STATE.shape = groom.groom_shape(STATE.group)
    STATE.guides = groom.read(STATE.shape)
    STATE.before = groom.snapshot(STATE.shape)
    STATE.changed = False
    STATE.proj = _Projector(view)
    STATE.cam = _camera(view)
    STATE.mesh = groom.mesh_of(STATE.shape)
    STATE.surface = None
    _rebuild_items()


def _rebuild_items():
    proj = STATE.proj
    campos, fwd, ortho = STATE.cam
    items = []
    for g in STATE.guides:
        it = _Item()
        it.g = g
        it.screen = [proj(p) for p in g.pts]
        it.rest = seg_lengths(g.pts)
        it.visible = True
        if STATE.front_only and g.frame0 is not None:
            n = g.frame0[1]
            view_dir = fwd if ortho else v_sub(g.pts[0], campos)
            it.visible = v_dot(n, view_dir) < 0.0
        items.append(it)
    STATE.items = items


def _surface():
    if STATE.surface is None and STATE.mesh:
        STATE.surface = scene.read_surface(STATE.mesh)
    return STATE.surface


def _flush():
    """Push the edited guides to the node (redraws the viewport)."""
    groom.write(STATE.shape, STATE.guides)
    STATE.changed = True


# ------------------------------------------------------------- creation
def _new_guide_at(view, sx, sy, spacing_grid=None, spacing=0.0):
    surf = _surface()
    mshape = scene.mesh_shape(STATE.mesh) if STATE.mesh else None
    if surf is None or mshape is None:
        cmds.warning("Wambo Fur: the groom has no growth mesh")
        return None
    near, d = _ray(view, sx, sy)
    fn = STATE.mesh_fn if getattr(STATE, "mesh_fn", None) else om.MFnMesh(scene.dag_path(mshape))
    STATE.mesh_fn = fn
    res = _intersect(fn, near, d)
    if res is None:
        return None
    p, face = res
    if spacing_grid is not None and spacing > 0 and spacing_grid.within(p, spacing):
        return None
    tri, bary = bind_point(surf, p, surf.face_tris(face))
    root, n, _ = tri_frame(surf.points, surf.triangles[tri], bary)
    pts = None
    if STATE.inherit and STATE.guides:
        pts = inherit_shape(root, n, STATE.guides)
    if pts is None:
        seg = max(2, int(STATE.new_segments))
        pts = [v_madd(root, n, STATE.new_length * k / float(seg)) for k in range(seg + 1)]
    g = bound_guide(surf, pts, tri, bary)
    STATE.guides.append(g)
    return g


def _paint(view, sx, sy):
    """Spray new guides inside the brush circle."""
    R = STATE.radius
    tries = max(1, int(round(1 + 5 * STATE.strength)))
    spacing = STATE.spacing
    if spacing <= 0.0:
        # auto: a fifth of the brush radius measured on the surface
        centre = _surface_point(view, sx, sy)
        if centre is not None:
            fwd = STATE.cam[1]
            a = _hit_plane(_ray(view, sx, sy), fwd, centre)
            b = _hit_plane(_ray(view, sx + R, sy), fwd, centre)
            spacing = 0.2 * math.sqrt(v_dot(v_sub(a, b), v_sub(a, b)))
    grid = HashGrid([g.pts[0] for g in STATE.guides], cell=max(spacing, 1e-4)) if STATE.guides else None
    added = 0
    for _ in range(tries):
        a, r = random.random() * 2 * math.pi, R * math.sqrt(random.random())
        g = _new_guide_at(view, sx + r * math.cos(a), sy + r * math.sin(a), grid, spacing)
        if g is not None:
            added += 1
            grid = HashGrid([x.pts[0] for x in STATE.guides], cell=max(spacing, 1e-4))
    return added


def _surface_point(view, sx, sy):
    fn = getattr(STATE, "mesh_fn", None)
    if fn is None:
        return None
    near, d = _ray(view, sx, sy)
    res = _intersect(fn, near, d)
    return res[0] if res else None


# ---------------------------------------------------------------- strokes
def _press():
    try:
        _press_impl()
    except Exception as e:
        STATE.last = None
        import traceback
        traceback.print_exc()
        cmds.warning("Wambo Fur brush: %s" % e)


def _press_impl():
    if not STATE.group or not groom.groom_shape(STATE.group):
        cmds.warning("Wambo Fur: no groom - create guides first (Tools tab)")
        return
    x, y, _ = cmds.draggerContext(CONTEXT, query=True, anchorPoint=True)
    STATE.last = (float(x), float(y))
    if STATE.cursor:
        STATE.cursor.set_pressed(True)
    view = _view()
    _begin(view)
    STATE.last_live = time.time()
    if STATE.mode in ("paint", "add"):
        try:
            mshape = scene.mesh_shape(STATE.mesh)
            STATE.mesh_fn = om.MFnMesh(scene.dag_path(mshape))
            STATE.accel = STATE.mesh_fn.autoUniformGridParams()
        except Exception:
            STATE.mesh_fn, STATE.accel = None, None
        n = _paint(view, *STATE.last) if STATE.mode == "paint" else \
            (1 if _new_guide_at(view, *STATE.last) else 0)
        if n:
            _flush()
            view.refresh(False, False)
        return
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
    if STATE.mode == "paint":
        if math.hypot(cur[0] - STATE.last[0], cur[1] - STATE.last[1]) < STATE.radius * 0.25:
            return
        if _paint(view, *cur):
            _flush()
    else:
        _apply(view, STATE.last, cur)
    STATE.last = cur
    view.refresh(False, False)
    now = time.time()
    if STATE.changed and STATE.on_stroke and now - STATE.last_live > STATE.live_interval:
        STATE.last_live = now
        try:
            STATE.on_stroke(STATE.group, False)
        except Exception as e:
            cmds.warning("Wambo Fur: %s" % e)


def _release():
    if STATE.cursor:
        STATE.cursor.set_pressed(False)
    if STATE.changed and STATE.shape:
        after = groom.snapshot(STATE.shape)
        push_pending([(STATE.shape, STATE.before, after)])
        if cmds.pluginInfo("wamboFur", q=True, loaded=True):
            cmds.wamboFurGroomEdit()      # one undo step for the whole stroke
        else:
            take_pending()
    STATE.items, STATE.guides, STATE.last = [], [], None
    STATE.surface = None
    if STATE.on_stroke:
        try:
            STATE.on_stroke(STATE.group, True)
        except Exception as e:
            cmds.warning("Wambo Fur: %s" % e)


def _apply(view, prev, cur):
    R, S = STATE.radius, STATE.strength
    mod = _modifier()
    mode = STATE.mode
    invert = "ctrl" in mod
    if "shift" in mod and mode not in ("remove", "cut"):
        mode = "smooth"
    mx, my = cur
    dx, dy = cur[0] - prev[0], cur[1] - prev[1]
    moved = dx != 0.0 or dy != 0.0
    fwd = STATE.cam[1]
    ray0 = ray1 = None
    if mode in ("comb", "move") and moved:
        ray0, ray1 = _ray(view, prev[0], prev[1]), _ray(view, cur[0], cur[1])
    R2 = R * R
    hits = []
    for idx, it in enumerate(STATE.items):
        if not it.visible:
            continue
        for sx, sy in it.screen:
            if (sx - mx) * (sx - mx) + (sy - my) * (sy - my) < R2:
                hits.append(idx)
                break
    if not hits:
        return
    if mode == "remove":
        dead = set(i for i in hits if _falloff(math.hypot(STATE.items[i].screen[0][0] - mx,
                                                         STATE.items[i].screen[0][1] - my), R) > 0.0)
        if dead:
            STATE.guides = [g for i, g in enumerate(STATE.guides) if i not in dead]
            STATE.items = [it for i, it in enumerate(STATE.items) if i not in dead]
            _flush()
        return
    clump_tip = None
    if mode == "clump":
        tips = [STATE.items[i].g.pts[-1] for i in hits]
        clump_tip = v_scale((sum(p[0] for p in tips), sum(p[1] for p in tips), sum(p[2] for p in tips)),
                            1.0 / len(tips))
    proj = STATE.proj
    for idx in hits:
        it = STATE.items[idx]
        g = it.g
        pts = list(g.pts)
        n = len(pts)
        ws = [_falloff(math.hypot(sx - mx, sy - my), R) for sx, sy in it.screen]
        wmax = max(ws)
        if wmax <= 0.0 or n < 2:
            continue
        if mode in ("comb", "move"):
            if not moved:
                continue
            for i in range(1, n):
                w = ws[i] * S * ((i / float(n - 1)) ** 0.5 if mode == "comb" else 1.0)
                if w > 0.0:
                    p = pts[i]
                    pts[i] = v_madd(p, v_sub(_hit_plane(ray1, fwd, p), _hit_plane(ray0, fwd, p)), w)
            if STATE.preserve_length and mode == "comb":
                pts = preserve_lengths(pts, it.rest)
            else:
                it.rest = seg_lengths(pts)
        elif mode == "length":
            amount = dx * 0.01 * S * wmax if moved else 0.0
            if invert:
                amount = -abs(amount) if amount else -0.02 * S * wmax
            elif not moved:
                continue
            f = max(0.05, 1.0 + amount)
            pts = [v_madd(pts[0], v_sub(p, pts[0]), f) for p in pts]
            it.rest = seg_lengths(pts)
        elif mode == "smooth":
            for _ in range(2):
                for i in range(1, n - 1):
                    w = ws[i] * S * 0.5
                    if w > 0.0:
                        pts[i] = v_lerp(pts[i], v_scale(v_add(pts[i - 1], pts[i + 1]), 0.5), w)
                if n > 2 and ws[-1] > 0.0:
                    pts[-1] = v_lerp(pts[-1], v_add(pts[-2], v_sub(pts[-2], pts[-3])), ws[-1] * S * 0.5)
            if STATE.preserve_length:
                pts = preserve_lengths(pts, it.rest)
        elif mode in ("puff", "straighten"):
            if mode == "puff":
                axis = g.frame0[1] if g.frame0 else v_norm(v_sub(pts[1], pts[0]))
                if invert:   # flatten toward the surface
                    d = v_norm(v_sub(pts[-1], pts[0]))
                    axis = v_norm(v_sub(d, v_scale(axis, v_dot(d, axis))), fallback=axis)
            else:
                axis = v_norm(v_sub(pts[1], pts[0]))
            L = polyline_length(pts)
            straight = [v_madd(pts[0], axis, L * i / float(n - 1)) for i in range(n)]
            k = wmax * S * 0.15
            pts = [v_lerp(p, q, k) for p, q in zip(pts, straight)]
            if STATE.preserve_length:
                pts = preserve_lengths(pts, it.rest)
        elif mode == "clump":
            for i in range(1, n):
                t = i / float(n - 1)
                target = v_add(clump_tip, v_sub(pts[i], pts[-1]))   # shift so the tip meets the clump
                w = wmax * S * 0.12 * t * t * (-1.0 if invert else 1.0)
                pts[i] = v_lerp(pts[i], target, w)
            if STATE.preserve_length:
                pts = preserve_lengths(pts, it.rest)
        elif mode == "cut":
            hit_i = max(range(1, n), key=lambda i: ws[i])
            if ws[hit_i] <= 0.0:
                continue
            frac = polyline_length(pts[:hit_i + 1]) / max(polyline_length(pts), 1e-9)
            pts = resample(pts, n, max(frac, 0.02))
            it.rest = seg_lengths(pts)
        g.pts = pts
        it.screen = [proj(p) for p in pts]
    _flush()


# ------------------------------------------------------------- global ops
def _edit_all(fn):
    """Apply fn(guides) -> guides to the whole groom as one undo step."""
    shape = groom.groom_shape(STATE.group)
    if not shape:
        return 0
    before = groom.snapshot(shape)
    guides = fn(groom.read(shape))
    groom.write(shape, guides)
    push_pending([(shape, before, groom.snapshot(shape))])
    if cmds.pluginInfo("wamboFur", q=True, loaded=True):
        cmds.wamboFurGroomEdit()
    return len(guides)


def clear_all():
    return _edit_all(lambda gs: [])


def smooth_all(iterations=2):
    def run(gs):
        for g in gs:
            rest = seg_lengths(g.pts)
            pts = list(g.pts)
            for _ in range(iterations):
                for i in range(1, len(pts) - 1):
                    pts[i] = v_lerp(pts[i], v_scale(v_add(pts[i - 1], pts[i + 1]), 0.5), 0.5)
            g.pts = preserve_lengths(pts, rest)
        return gs
    return _edit_all(run)


def reset_straight():
    def run(gs):
        for g in gs:
            L = polyline_length(g.pts)
            n = len(g.pts)
            axis = g.frame0[1] if g.frame0 else v_norm(v_sub(g.pts[1], g.pts[0]))
            g.pts = [v_madd(g.pts[0], axis, L * i / float(n - 1)) for i in range(n)]
        return gs
    return _edit_all(run)


def resample_all(points):
    def run(gs):
        for g in gs:
            g.pts = resample(g.pts, max(2, int(points)))
        return gs
    return _edit_all(run)


def scale_all(factor):
    def run(gs):
        for g in gs:
            g.pts = [v_madd(g.pts[0], v_sub(p, g.pts[0]), factor) for p in g.pts]
        return gs
    return _edit_all(run)


# ---------------------------------------------------------------- public
def activate(mode, group, on_stroke=None, on_settings=None):
    from .cursor import BrushCursor
    if mode not in MODES:
        raise ValueError(mode)
    STATE.mode = mode
    STATE.group = group
    STATE.on_stroke = on_stroke
    STATE.on_settings = on_settings
    if cmds.draggerContext(CONTEXT, exists=True):
        cmds.deleteUI(CONTEXT)
    cmds.draggerContext(CONTEXT, pressCommand=_press, dragCommand=_drag, releaseCommand=_release,
                        cursor="crossHair", space="screen", undoMode="sequence", name=CONTEXT)
    cmds.setToolTo(CONTEXT)
    if STATE.cursor is None:
        STATE.cursor = BrushCursor(STATE)
        if not STATE.cursor.start():
            STATE.cursor = None
    else:
        STATE.cursor.sync_style()
    cmds.scriptJob(event=["ToolChanged", _on_tool_changed], runOnce=True)
    cmds.inViewMessage(amg="Wambo Fur: <hl>%s</hl>  ·  B+drag radius  ·  Shift smooth  ·  "
                           "Ctrl invert" % LABELS.get(mode, mode), pos="topCenter", fade=True)


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
