"""Native groom node (wamboFurGroom): guides live inside the node, not as
Maya curves. Read / write / create / convert helpers."""
import random

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr

from ..core.guides import Guide, bound_guide, bind_point, tri_frame
from ..core.mathutil import v_madd
from . import scene

NODE_TYPE = "wamboFurGroom"


# ------------------------------------------------------------------ lookup
def groom_shape(name):
    """Groom shape for a groom transform or shape name, else None."""
    if not name or not cmds.objExists(name):
        return None
    if cmds.nodeType(name) == NODE_TYPE:
        return cmds.ls(name, long=True)[0]
    kids = cmds.listRelatives(name, shapes=True, type=NODE_TYPE, fullPath=True) or []
    return kids[0] if kids else None


def is_groom(name):
    return groom_shape(name) is not None


def mesh_of(shape):
    return cmds.getAttr(shape + ".meshName") or ""


def _fn(shape):
    return om.MFnDependencyNode(scene.dag_path(shape).node())


# ------------------------------------------------------------- read/write
def _get(fn, name, fn_cls):
    # a typed array attribute that was never set has no data object:
    # Maya raises kFailure on asMObject() - treat it as empty
    try:
        obj = fn.findPlug(name, False).asMObject()
    except RuntimeError:
        return []
    if obj.isNull():
        return []
    try:
        return list(fn_cls(obj).array())
    except RuntimeError:
        return []


def snapshot(shape):
    """Raw arrays (pts, counts, tris, bary, frames) - used for undo."""
    fn = _fn(shape)
    return (_get(fn, "guidePoints", om.MFnDoubleArrayData),
            _get(fn, "guideCounts", om.MFnIntArrayData),
            _get(fn, "guideTris", om.MFnIntArrayData),
            _get(fn, "guideBary", om.MFnDoubleArrayData),
            _get(fn, "guideFrames", om.MFnDoubleArrayData))


def restore(shape, snap):
    pts, counts, tris, bary, frames = snap
    fn = _fn(shape)
    fn.findPlug("guidePoints", False).setMObject(om.MFnDoubleArrayData().create(om.MDoubleArray(pts)))
    fn.findPlug("guideCounts", False).setMObject(om.MFnIntArrayData().create(om.MIntArray(counts)))
    fn.findPlug("guideTris", False).setMObject(om.MFnIntArrayData().create(om.MIntArray(tris)))
    fn.findPlug("guideBary", False).setMObject(om.MFnDoubleArrayData().create(om.MDoubleArray(bary)))
    fn.findPlug("guideFrames", False).setMObject(om.MFnDoubleArrayData().create(om.MDoubleArray(frames)))
    try:
        omr.MRenderer.setGeometryDrawDirty(scene.dag_path(shape).node())
    except Exception:
        pass


def from_snapshot(snap):
    pts, counts, tris, bary, frames = snap
    guides, i = [], 0
    for g, n in enumerate(counts):
        p = [(pts[i + 3 * k], pts[i + 3 * k + 1], pts[i + 3 * k + 2]) for k in range(n)]
        i += 3 * n
        tri = tris[g] if g < len(tris) else -1
        b = tuple(bary[3 * g:3 * g + 3]) if len(bary) >= 3 * g + 3 else (1.0, 0.0, 0.0)
        fr = frames[9 * g:9 * g + 9] if len(frames) >= 9 * g + 9 else None
        frame = (tuple(fr[0:3]), tuple(fr[3:6]), tuple(fr[6:9])) if fr else None
        guides.append(Guide(p, tri, b, frame))
    return guides


def to_snapshot(guides):
    pts, counts, tris, bary, frames = [], [], [], [], []
    for g in guides:
        counts.append(len(g.pts))
        for p in g.pts:
            pts.extend(p)
        tris.append(int(g.tri))
        bary.extend(g.bary)
        if g.frame0:
            frames.extend(g.frame0[0])
            frames.extend(g.frame0[1])
            frames.extend(g.frame0[2])
        else:
            r = g.pts[0]
            frames.extend((r[0], r[1], r[2], 0.0, 1.0, 0.0, 1.0, 0.0, 0.0))
    return pts, counts, tris, bary, frames


def read(shape):
    return from_snapshot(snapshot(shape))


def write(shape, guides):
    restore(shape, to_snapshot(guides))


# ------------------------------------------------------------------ create
def create_node(name, mesh):
    tr = cmds.createNode("transform", name=name)
    shape = cmds.createNode(NODE_TYPE, name=tr + "Shape", parent=tr)
    for a in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
        cmds.setAttr(tr + "." + a, lock=True, keyable=False, channelBox=False)
    cmds.setAttr(shape + ".meshName", mesh, type="string")
    mshape = scene.mesh_shape(mesh)
    if mshape:
        cmds.connectAttr(mshape + ".worldMesh[0]", shape + ".inMesh", force=True)
    return cmds.ls(shape, long=True)[0]


def straight_guides(surface, count, length, segments, seed=1):
    rng = random.Random(seed)
    out = []
    for (t, b0, b1, b2) in surface.random_samples(int(count), rng):
        root, n, _ = tri_frame(surface.points, surface.triangles[t], (b0, b1, b2))
        pts = [v_madd(root, n, length * k / float(segments)) for k in range(segments + 1)]
        out.append(bound_guide(surface, pts, t, (b0, b1, b2)))
    return out


def bind(surface, pts, face=None):
    """Bind loose points (e.g. converted curves) to the surface."""
    cands = surface.face_tris(face) if (face is not None and hasattr(surface, "face_tris")) else None
    tri, bary = bind_point(surface, pts[0], cands)
    return bound_guide(surface, pts, tri, bary)


def closest_face(mesh_shape, p):
    fn = om.MFnMesh(scene.dag_path(mesh_shape))
    _, face = fn.getClosestPoint(om.MPoint(*p), om.MSpace.kWorld)
    return face


def convert_curves(curve_group, groom, samples=None):
    """Turn a group of Maya curves into native guides (one undo-less step)."""
    mesh = mesh_of(groom)
    surf = scene.read_surface(mesh)
    mshape = scene.mesh_shape(mesh)
    guides = []
    for shp in scene.curve_shapes(curve_group):
        n = samples or max(4, len(om.MFnNurbsCurve(scene.dag_path(shp)).cvPositions()))
        pts = scene.read_curve(shp, n)
        guides.append(bind(surf, pts, closest_face(mshape, pts[0])))
    write(groom, read(groom) + guides)
    return len(guides)


def export_curves(groom, name=None):
    """Bake the guides to Maya curves (for other tools / simulation)."""
    grp = cmds.createNode("transform", name=name or "wamboFurGuideCurves")
    for i, g in enumerate(read(groom)):
        crv = cmds.curve(degree=1, point=g.pts, name="wfGuideCrv%d" % (i + 1))
        cmds.parent(crv, grp)
    return grp
