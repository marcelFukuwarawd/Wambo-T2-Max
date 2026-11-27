"""Fur system = a transform in the scene that stores the graph (JSON) and
settings, owns the viewport preview curves and points at its guide group.

    wamboFur1                (transform, attrs: wfSystem, wfGraph, wfSettings)
      |- wamboFur1_preview   (viewport curves, preview density)
      |- wamboFur1_render    (aiStandIn / VRayProxy, created on export)
    wamboFur1_guides         (groomable guide curves, edited with the brushes)
"""
import json
import random
import time

import maya.cmds as cmds
import maya.api.OpenMaya as om

from ..core.graph import Graph, EvalContext, default_graph
from ..core.data import EvalError
from ..core.mathutil import v_madd
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
        with NoUndo():
            self.clear_preview()
            node = self.preview_node()
            build_curves(node, data.strands, smooth=st["preview_smooth"])
            cmds.setAttr(node + ".overrideEnabled", 1)
            cmds.setAttr(node + ".overrideRGBColors", 1)
            cmds.setAttr(node + ".overrideColorRGB", *st["preview_color"])
        return data

    # ------------------------------------------------------------- guides
    def guides_group(self):
        name = self.name + "_guides"
        return name if cmds.objExists(name) else None

    def create_guides(self, count=150, length=1.0, segments=6, seed=1):
        """Scatter straight guide curves on the system's mesh and wire them
        into the graph (Import Guides -> Guides node after Grow)."""
        graph = self.load_graph()
        imp = next((n for n in graph.nodes.values() if n.type_name == "import_geo"), None)
        if imp is None or not imp.params["geometry"]:
            raise EvalError("the graph has no Import Geometry node with a mesh")
        mesh = imp.params["geometry"]
        surf = scene.read_surface(mesh)
        rng = random.Random(seed)
        segments = max(3, int(segments))
        grp = self.guides_group()
        if grp:
            cmds.delete(grp)
        grp = cmds.createNode("transform", name=self.name + "_guides")
        cmds.addAttr(grp, longName=GUIDE_MESH_ATTR, dataType="string")
        cmds.setAttr(grp + "." + GUIDE_MESH_ATTR, mesh, type="string")
        for i, smp in enumerate(surf.random_samples(int(count), rng)):
            pos, nrm, uv, tan = surf.sample(*smp)
            pts = [v_madd(pos, nrm, length * k / float(segments)) for k in range(segments + 1)]
            crv = cmds.curve(degree=3 if segments >= 3 else 1, point=pts, name="wfGuide%d" % (i + 1))
            cmds.parent(crv, grp)
        cmds.setAttr(grp + ".overrideEnabled", 1)
        cmds.setAttr(grp + ".overrideRGBColors", 1)
        cmds.setAttr(grp + ".overrideColorRGB", 0.2, 0.85, 1.0)
        self.wire_guides(grp, graph)
        return grp

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
        g = self.guides_group()
        cmds.delete([n for n in (self.name, g) if n])


def build_curves(parent, strands, smooth=False):
    """Create one nurbsCurve shape per strand under `parent` (fast, API)."""
    pobj = _mobject(parent)
    fn = om.MFnNurbsCurve()
    knots1 = {}
    count = 0
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
        fn.create(cvs, kn, deg, om.MFnNurbsCurve.kOpen, False, False, pobj)
        count += 1
    return count
