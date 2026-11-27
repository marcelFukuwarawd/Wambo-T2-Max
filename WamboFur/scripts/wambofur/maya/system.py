"""Fur system = a transform in the scene that stores the graph (JSON) and
settings, owns the viewport preview curves and points at its guide group.

    wamboFur1                (transform, attrs: wfSystem, wfGraph, wfSettings)
      |- wamboFur1_preview   (viewport curves, preview density)
      |- wamboFur1_render    (aiStandIn / VRayProxy, created on export)
    wamboFur1_guides         (groomable guide curves, edited with the brushes)
"""
import json
import time

import maya.cmds as cmds
import maya.api.OpenMaya as om

from ..core.graph import Graph, EvalContext, default_graph
from ..core.data import EvalError
from . import scene

SYSTEM_ATTR = "wfSystem"
GRAPH_ATTR = "wfGraph"
SETTINGS_ATTR = "wfSettings"
GUIDE_MESH_ATTR = "wfGuideMesh"

DEFAULT_SETTINGS = {
    "preview_density": 0.1,     # fraction of render count shown in the viewport
    "render_density": 1.0,
    "preview_smooth": False,    # degree-3 preview curves (slower)
    "preview_color": (0.85, 0.65, 0.35),
    "width_scale": 1.0,
    "render_subdiv": 1,         # Catmull-Rom points inserted per segment at render
    "renderer": "arnold",       # arnold | vray
    "output_dir": "",           # default: <workspace>/cache/wambofur
    "shader": {"base_color": (1.0, 1.0, 1.0), "melanin": 0.45, "melanin_redness": 0.5,
               "melanin_randomize": 0.1, "roughness": 0.25, "ior": 1.55},
    "live_time": False,
}


class NoUndo(object):
    """API-created preview geometry must stay out of the undo queue."""

    def __enter__(self):
        self.state = cmds.undoInfo(q=True, stateWithoutFlush=True)
        cmds.undoInfo(stateWithoutFlush=False)

    def __exit__(self, *a):
        cmds.undoInfo(stateWithoutFlush=self.state)


def _mobject(name):
    sel = om.MSelectionList()
    sel.add(name)
    return sel.getDependNode(0)


def list_systems():
    return sorted(n for n in (cmds.ls(type="transform") or [])
                  if cmds.attributeQuery(SYSTEM_ATTR, node=n, exists=True))


class FurSystem(object):
    def __init__(self, name):
        if not cmds.objExists(name) or not cmds.attributeQuery(SYSTEM_ATTR, node=name, exists=True):
            raise ValueError("%s is not a Wambo Fur system" % name)
        self.name = name
        self.last_stats = {}
        self.last_errors = {}
        self.last_time = 0.0
        self.last_draw_time = 0.0

    # ------------------------------------------------------------ creation
    @classmethod
    def create(cls, mesh, name="wamboFur1"):
        if not scene.mesh_shape(mesh):
            raise ValueError("select a polygon mesh")
        node = cmds.createNode("transform", name=name)
        for attr in (GRAPH_ATTR, SETTINGS_ATTR):
            cmds.addAttr(node, longName=attr, dataType="string")
        cmds.addAttr(node, longName=SYSTEM_ATTR, attributeType="bool", defaultValue=True)
        cmds.setAttr(node + "." + SYSTEM_ATTR, lock=True)
        for a in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
            cmds.setAttr(node + "." + a, lock=True, keyable=False, channelBox=False)
        sysm = cls(node)
        sysm.save_graph(default_graph(mesh))
        sysm.save_settings(dict(DEFAULT_SETTINGS))
        return sysm

    # ------------------------------------------------------------ storage
    def load_graph(self):
        return Graph.from_json(cmds.getAttr(self.name + "." + GRAPH_ATTR) or "")

    def save_graph(self, graph):
        cmds.setAttr(self.name + "." + GRAPH_ATTR, graph.to_json(), type="string")

    def settings(self):
        st = json.loads(json.dumps(DEFAULT_SETTINGS))
        raw = cmds.getAttr(self.name + "." + SETTINGS_ATTR)
        if raw:
            st.update(json.loads(raw))
        return st

    def save_settings(self, st):
        cmds.setAttr(self.name + "." + SETTINGS_ATTR, json.dumps(st), type="string")

    def set_setting(self, key, value):
        st = self.settings()
        st[key] = value
        self.save_settings(st)

    # ---------------------------------------------------------- evaluation
    def evaluate(self, density, node_id=None, graph=None):
        graph = graph or self.load_graph()
        nid = node_id or graph.display_id or graph.output_id()
        if not nid:
            raise EvalError("graph has no output node")
        ctx = EvalContext(density=density, resolver=scene.MayaResolver())
        t0 = time.time()
        try:
            result = graph.evaluate(nid, ctx)
        finally:
            self.last_stats = ctx.stats
            self.last_errors = ctx.errors
        self.last_time = time.time() - t0
        return result

    def evaluate_render(self):
        graph = self.load_graph()
        return self.evaluate(self.settings()["render_density"], graph.output_id(), graph)

    # ------------------------------------------------------------ preview
    def preview_node(self):
        name = self.name + "_preview"
        full = "|%s|%s" % (self.name, name) if not self.name.startswith("|") else self.name + "|" + name
        if cmds.objExists(full):
            return full
        node = cmds.createNode("transform", name=name, parent=self.name)
        return cmds.ls(node, long=True)[0]

    def clear_preview(self):
        name = self.name + "_preview"
        kids = cmds.listRelatives(self.name, children=True, fullPath=True) or []
        with NoUndo():
            for k in kids:
                if k.split("|")[-1] == name:
                    cmds.delete(k)

    def update_preview(self, graph=None):
        """Evaluate at preview density and rebuild the viewport curves.
        Returns the FurData shown."""
        st = self.settings()
        data = self.evaluate(st["preview_density"], graph=graph)
        smooth = bool(st["preview_smooth"])
        strands = [s for s in data.strands if len(s.points) >= 2]
        sig = (tuple(len(s.points) for s in strands), smooth)
        t0 = time.time()
        with NoUndo():
            cache = _PREVIEW.get(self.name)
            if not (cache and cache["sig"] == sig and update_curves(cache, strands)):
                # topology changed (count / segments): rebuild the shapes
                self.clear_preview()
                node = self.preview_node()
                handles = build_curves(node, strands, smooth=smooth)
                cmds.setAttr(node + ".overrideEnabled", 1)
                cmds.setAttr(node + ".overrideRGBColors", 1)
                cmds.setAttr(node + ".overrideColorRGB", *st["preview_color"])
                _PREVIEW[self.name] = {"sig": sig, "handles": handles}
        self.last_draw_time = time.time() - t0
        return data

    # ------------------------------------------------------------- guides
    def mesh(self):
        graph = self.load_graph()
        imp = next((n for n in graph.nodes.values() if n.type_name == "import_geo"), None)
        if imp is None or not imp.params["geometry"]:
            raise EvalError("the graph has no Import Geometry node with a mesh")
        return imp.params["geometry"]

    def groom(self, create=False):
        """Native groom shape of this system (created on demand)."""
        from . import groom
        name = self.name + "_groom"
        shape = groom.groom_shape(name)
        if shape is None and create:
            shape = groom.create_node(name, self.mesh())
            self.wire_guides(cmds.listRelatives(shape, parent=True)[0])
        return shape

    def guides_group(self):
        """Groom node (preferred) or a legacy curve group."""
        shape = self.groom()
        if shape:
            return cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        name = self.name + "_guides"
        return name if cmds.objExists(name) else None

    def legacy_curve_group(self):
        name = self.name + "_guides"
        return name if cmds.objExists(name) and not cmds.listRelatives(
            name, shapes=True, type="wamboFurGroom") else None

    def create_guides(self, count=150, length=1.0, segments=6, seed=1, replace=True):
        """Scatter straight guides on the mesh into the native groom node."""
        from . import groom
        shape = self.groom(create=True)
        surf = scene.read_surface(self.mesh())
        new = groom.straight_guides(surf, count, length, max(2, int(segments)), seed)
        before = groom.snapshot(shape)
        groom.write(shape, new if replace else groom.read(shape) + new)
        _push_groom_undo(shape, before)
        return cmds.listRelatives(shape, parent=True, fullPath=True)[0]

    def wire_guides(self, group, graph=None):
        graph = graph or self.load_graph()
        ig = next((n for n in graph.nodes.values() if n.type_name == "import_guides"), None)
        if ig is None:
            grow = next((n for n in graph.nodes.values() if n.type_name == "grow"), None)
            ig = graph.add_node("import_guides", pos=(grow.pos[0] if grow else 0, (grow.pos[1] if grow else 0) + 140))
            gd = graph.add_node("guides", pos=((grow.pos[0] + 100) if grow else 200, (grow.pos[1] if grow else 0) - 90))
            if grow is not None:
                # splice the Guides node right after Grow
                for s, d, p in list(graph.connections):
                    if s == grow.id:
                        graph.disconnect(d, p)
                        graph.connect(gd.id, d, p)
                graph.connect(grow.id, gd.id, 0)
            graph.connect(ig.id, gd.id, 1)
        ig.params["curves"] = group
        self.save_graph(graph)
        return graph

    def delete(self):
        nodes = [self.name, self.guides_group(), self.legacy_curve_group()]
        cmds.delete([n for n in nodes if n and cmds.objExists(n)])


def _push_groom_undo(shape, before):
    """Register a groom change (already applied) as one undo step."""
    from . import groom, brushes
    after = groom.snapshot(shape)
    if cmds.pluginInfo("wamboFur", q=True, loaded=True):
        brushes.push_pending([(shape, before, after)])
        cmds.wamboFurGroomEdit()


_PREVIEW = {}   # system name -> {"sig": ..., "handles": [MObjectHandle]}


def update_curves(cache, strands):
    """Move the CVs of existing preview curves in place (much faster than
    deleting and re-creating thousands of shapes). False if stale."""
    handles = cache["handles"]
    if len(handles) != len(strands):
        return False
    if handles and not (handles[0].isValid() and handles[-1].isValid()):
        return False
    fn = om.MFnNurbsCurve()
    try:
        for h, s in zip(handles, strands):
            fn.setObject(h.object())
            fn.setCVPositions(om.MPointArray([om.MPoint(p[0], p[1], p[2]) for p in s.points]),
                              om.MSpace.kObject)
            fn.updateCurve()
    except RuntimeError:
        return False
    return True


def build_curves(parent, strands, smooth=False):
    """Create one nurbsCurve shape per strand under `parent` (fast, API).
    Returns MObjectHandles of the created shapes."""
    pobj = _mobject(parent)
    fn = om.MFnNurbsCurve()
    knots1 = {}
    handles = []
    for s in strands:
        n = len(s.points)
        if n < 2:
            continue
        cvs = om.MPointArray([om.MPoint(p[0], p[1], p[2]) for p in s.points])
        deg = 3 if (smooth and n >= 4) else 1
        key = (n, deg)
        kn = knots1.get(key)
        if kn is None:
            if deg == 1:
                kn = om.MDoubleArray([float(i) for i in range(n)])
            else:
                spans = n - 3
                kv = [0.0, 0.0] + [float(i) for i in range(spans + 1)] + [float(spans)] * 2
                kn = om.MDoubleArray(kv)
            knots1[key] = kn
        tr = fn.create(cvs, kn, deg, om.MFnNurbsCurve.kOpen, False, False, pobj)
        # create() returns the parent transform when one is given: keep the new shape
        shape = fn.object() if not tr.hasFn(om.MFn.kNurbsCurve) else tr
        handles.append(om.MObjectHandle(shape))
    return handles
