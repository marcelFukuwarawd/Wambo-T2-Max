"""Read Maya scene data into the engine's structures (OpenMaya API 2.0)."""
import maya.cmds as cmds
import maya.api.OpenMaya as om

from ..core.data import Surface, EvalError


def dag_path(name):
    sel = om.MSelectionList()
    try:
        sel.add(name)
    except RuntimeError:
        raise EvalError("'%s' does not exist" % name)
    return sel.getDagPath(0)


def mesh_shape(name):
    """Return the (non-intermediate) mesh shape for a transform or shape name."""
    if not cmds.objExists(name):
        return None
    if cmds.nodeType(name) == "mesh":
        return name
    shapes = cmds.listRelatives(name, shapes=True, type="mesh", fullPath=True, noIntermediate=True) or []
    return shapes[0] if shapes else None


_TOPO = {}   # mesh shape -> (signature, triangles, tri_uvs, rest_points)


def clear_cache():
    _TOPO.clear()


def read_surface(name):
    shape = mesh_shape(name)
    if not shape:
        raise EvalError("'%s' is not a polygon mesh" % name)
    fn = om.MFnMesh(dag_path(shape))
    points = [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)]
    normals = [(n.x, n.y, n.z) for n in fn.getVertexNormals(False, om.MSpace.kWorld)]
    sig = (fn.numVertices, fn.numPolygons, fn.numFaceVertices, fn.numUVs())
    cached = _TOPO.get(shape)
    if cached and cached[0] == sig:
        # topology / UVs unchanged: only positions and normals are re-read
        return Surface(points, cached[1], normals, cached[2], name=name, rest_points=cached[3])
    triangles, tri_uvs = _read_topology(fn)
    rest = read_rest_points(shape, len(points))
    _TOPO[shape] = (sig, triangles, tri_uvs, rest)
    return Surface(points, triangles, normals, tri_uvs, name=name, rest_points=rest)


def _read_topology(fn):
    tri_counts, tri_verts = fn.getTriangles()
    vcounts, vlist = fn.getVertices()
    tri_counts, tri_verts = list(tri_counts), list(tri_verts)
    vcounts, vlist = list(vcounts), list(vlist)
    has_uv = fn.numUVs() > 0
    if has_uv:
        us, vs = fn.getUVs()
        us, vs = list(us), list(vs)
        uvcounts, uvids = fn.getAssignedUVs()
        uvcounts, uvids = list(uvcounts), list(uvids)
    triangles, tri_uvs = [], []
    tv = 0      # cursor into tri_verts
    fv = 0      # cursor into face-vertex arrays
    for f in range(len(vcounts)):
        nv = vcounts[f]
        face_verts = vlist[fv:fv + nv]
        face_uvs = uvids[fv:fv + nv] if has_uv and uvcounts[f] == nv else None
        for _ in range(tri_counts[f]):
            a, b, c = tri_verts[tv], tri_verts[tv + 1], tri_verts[tv + 2]
            tv += 3
            triangles.append((a, b, c))
            if has_uv:
                if face_uvs is not None:
                    corner = []
                    for gid in (a, b, c):
                        uid = face_uvs[face_verts.index(gid)]
                        corner.append((us[uid], vs[uid]))
                    tri_uvs.append(tuple(corner))
                else:
                    tri_uvs.append(((0.0, 0.0), (0.0, 0.0), (0.0, 0.0)))
        fv += nv
    return triangles, (tri_uvs if has_uv else None)


def read_rest_points(shape, count):
    """Undeformed positions from the deformer's intermediate ('Orig') shape,
    or None when the mesh is not deformed."""
    parent = cmds.listRelatives(shape, parent=True, fullPath=True)
    if not parent:
        return None
    for s in cmds.listRelatives(parent[0], shapes=True, type="mesh", fullPath=True) or []:
        if not cmds.getAttr(s + ".intermediateObject"):
            continue
        if cmds.polyEvaluate(s, vertex=True) != count:
            continue
        fn = om.MFnMesh(dag_path(s))
        return [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kObject)]
    return None


def curve_shapes(name):
    if not cmds.objExists(name):
        return []
    if cmds.nodeType(name) == "nurbsCurve":
        return [cmds.ls(name, long=True)[0]]
    shapes = cmds.listRelatives(name, allDescendents=True, type="nurbsCurve", fullPath=True) or []
    shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
    return sorted(shapes)


def read_curve(shape, samples):
    fn = om.MFnNurbsCurve(dag_path(shape))
    length = fn.length()
    pts = []
    for k in range(samples):
        prm = fn.findParamFromLength(length * k / float(samples - 1)) if length > 0 else 0.0
        p = fn.getPointAtParam(prm, om.MSpace.kWorld)
        pts.append((p.x, p.y, p.z))
    return pts


def read_guides(name, samples):
    shapes = curve_shapes(name)
    if not shapes:
        raise EvalError("no curves under '%s'" % name)
    return [read_curve(s, samples) for s in shapes]


def sample_texture(texture, uvs, chunk=4000):
    """Sample a 2D texture node at UVs -> luminance values in [0, 1]."""
    if not texture or not cmds.objExists(texture):
        raise EvalError("texture '%s' does not exist" % texture)
    out = []
    for i in range(0, len(uvs), chunk):
        part = uvs[i:i + chunk]
        us = [uv[0] for uv in part]
        vs = [uv[1] for uv in part]
        rgb = cmds.colorAtPoint(texture, output="RGB", u=us, v=vs) or []
        for k in range(len(part)):
            r, g, b = rgb[3 * k:3 * k + 3] if len(rgb) >= 3 * k + 3 else (1.0, 1.0, 1.0)
            out.append(max(0.0, min(1.0, 0.2126 * r + 0.7152 * g + 0.0722 * b)))
    return out


class MayaResolver(object):
    """Resolver handed to EvalContext; caches reads for one evaluation."""

    def __init__(self):
        self._surf = {}
        self._guides = {}
        self._maps = {}

    def get_surface(self, name):
        if name not in self._surf:
            self._surf[name] = read_surface(name)
        return self._surf[name]

    def get_guides(self, name, samples):
        key = (name, samples)
        if key not in self._guides:
            self._guides[key] = read_guides(name, samples)
        return self._guides[key]

    def sample_map(self, texture, uvs):
        return sample_texture(texture, uvs)


def selected(kind):
    """First selected object matching kind ('mesh', 'curves', 'texture')."""
    sel = cmds.ls(selection=True, long=False) or []
    for s in sel:
        if kind == "mesh" and mesh_shape(s):
            return s.split(".")[0]
        if kind == "curves" and curve_shapes(s):
            return s
        if kind == "texture":
            cls = cmds.getClassification(cmds.nodeType(s)) or [""]
            if any("texture/2d" in c for c in cls):
                return s
    return None
