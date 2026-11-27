"""Render hand-off for Arnold (MtoA) and V-Ray for Maya.

Arnold : evaluates the graph at render density, writes an .ass procedural
         (curves + standard_hair) and loads it with an aiStandIn.
V-Ray  : writes an Alembic curves file with per-point widths and loads it
         with a VRayProxy + VRayHairNextMtl (V-Ray's native hair path).
         The same .abc can also be loaded in an aiStandIn.

Animated surfaces: export a frame range -> one file per frame; the render
node switches files every frame (aiStandIn frame extension / expression for
the VRayProxy), so it also works on render farms.
"""
import os

import maya.cmds as cmds
import maya.mel as mel

from ..core.export import write_ass, render_strands
from . import system as fsys


# ----------------------------------------------------------------- helpers
def output_dir(sysm):
    d = sysm.settings().get("output_dir")
    if not d:
        root = cmds.workspace(q=True, rootDirectory=True) or cmds.internalVar(userTmpDir=True)
        d = os.path.join(root, "cache", "wambofur")
    d = d.replace("\\", "/")
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def _frame_path(base, ext, frame):
    if frame is None:
        return "%s.%s" % (base, ext)
    return "%s.%04d.%s" % (base, int(round(frame)), ext)


def _render_parent(sysm):
    name = sysm.name + "_render"
    for k in cmds.listRelatives(sysm.name, children=True, fullPath=True) or []:
        if k.split("|")[-1] == name:
            return k
    return cmds.ls(cmds.createNode("transform", name=name, parent=sysm.name), long=True)[0]


def _frames(start, end, step=1.0):
    f, out = float(start), []
    while f <= float(end) + 1e-6:
        out.append(f)
        f += step
    return out


def _ensure_plugin(names):
    for n in names:
        try:
            if not cmds.pluginInfo(n, q=True, loaded=True):
                cmds.loadPlugin(n, quiet=True)
            return n
        except RuntimeError:
            continue
    return None


# ------------------------------------------------------------------ ARNOLD
def export_ass(sysm, frame=None):
    st = sysm.settings()
    if frame is not None:
        cmds.currentTime(frame, update=True)
    data = sysm.evaluate_render()
    base = os.path.join(output_dir(sysm), sysm.name).replace("\\", "/")
    path = _frame_path(base, "ass", frame)
    n = write_ass(path, data.strands, node_name=sysm.name, subdiv=int(st["render_subdiv"]),
                  width_scale=float(st["width_scale"]), shader=st["shader"])
    return path, n


def arnold_standin(sysm, path, sequence=False):
    if not _ensure_plugin(["mtoa"]):
        raise RuntimeError("Arnold (mtoa) plug-in is not available")
    parent = _render_parent(sysm)
    shapes = cmds.listRelatives(parent, shapes=True, type="aiStandIn", fullPath=True) or []
    if shapes:
        si = shapes[0]
    else:
        si = cmds.createNode("aiStandIn", name=sysm.name + "_arnoldShape", parent=parent)
    if sequence:
        # aiStandIn resolves #### with the frameNumber attribute
        path = path.rsplit(".", 2)[0] + ".####.ass"
        cmds.setAttr(si + ".useFrameExtension", 1)
        if not cmds.listConnections(si + ".frameNumber", source=True, destination=False):
            cmds.connectAttr("time1.outTime", si + ".frameNumber", force=True)
    else:
        cmds.setAttr(si + ".useFrameExtension", 0)
    cmds.setAttr(si + ".dso", path, type="string")
    return si


def render_arnold(sysm, start=None, end=None, step=1.0):
    """Export .ass file(s) and (re)build the aiStandIn. Returns (paths, curves)."""
    if start is None:
        path, n = export_ass(sysm)
        arnold_standin(sysm, path, sequence=False)
        return [path], n
    cur = cmds.currentTime(q=True)
    paths, n = [], 0
    try:
        for f in _frames(start, end, step):
            p, n = export_ass(sysm, f)
            paths.append(p)
    finally:
        cmds.currentTime(cur, update=True)
    arnold_standin(sysm, paths[0], sequence=True)
    return paths, n


# ------------------------------------------------------------------- V-RAY
def _build_abc_group(sysm, data, st):
    """Full-density Maya curves with per-CV 'width' and 'riCurves' so
    AbcExport writes them as ONE curves object with widths."""
    grp = cmds.createNode("transform", name=sysm.name + "_abcTmp")
    cmds.addAttr(grp, longName="riCurves", attributeType="bool", defaultValue=True)
    grp = cmds.ls(grp, long=True)[0]
    strands = []
    widths = []
    from ..core.data import Strand
    for pts, ws, s in render_strands(data.strands, int(st["render_subdiv"]), float(st["width_scale"])):
        strands.append(Strand(pts))
        widths.append(ws)
    with fsys.NoUndo():
        fsys.build_curves(grp, strands, smooth=False)  # returns handles; shapes listed below
        shapes = cmds.listRelatives(grp, shapes=True, fullPath=True) or []
        for shp, ws in zip(shapes, widths):
            cmds.addAttr(shp, longName="width", dataType="doubleArray")
            cmds.setAttr(shp + ".width", ws, type="doubleArray")
    return grp, len(shapes)


def export_abc(sysm, frame=None):
    if not _ensure_plugin(["AbcExport"]):
        raise RuntimeError("AbcExport plug-in is not available")
    st = sysm.settings()
    if frame is not None:
        cmds.currentTime(frame, update=True)
    data = sysm.evaluate_render()
    base = os.path.join(output_dir(sysm), sysm.name).replace("\\", "/")
    path = _frame_path(base, "abc", frame)
    grp, n = _build_abc_group(sysm, data, st)
    try:
        f = cmds.currentTime(q=True)
        job = '-frameRange %s %s -dataFormat ogawa -worldSpace -root %s -file "%s"' % (f, f, grp, path)
        cmds.AbcExport(j=job)
    finally:
        with fsys.NoUndo():
            cmds.delete(grp)
    return path, n


def hair_material(sysm, renderer):
    st = sysm.settings()["shader"]
    name = sysm.name + ("_vrayHairMtl" if renderer == "vray" else "_aiHairMtl")
    if cmds.objExists(name):
        return name
    if renderer == "vray":
        mtl = cmds.shadingNode("VRayHairNextMtl", asShader=True, name=name)
        for attr, key in (("melanin", "melanin"),):
            if cmds.attributeQuery(attr, node=mtl, exists=True):
                cmds.setAttr(mtl + "." + attr, float(st[key]))
    else:
        mtl = cmds.shadingNode("aiStandardHair", asShader=True, name=name)
        cmds.setAttr(mtl + ".melanin", float(st["melanin"]))
        cmds.setAttr(mtl + ".roughness", float(st["roughness"]))
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
    cmds.connectAttr(mtl + ".outColor", sg + ".surfaceShader", force=True)
    return mtl


def _assign(mtl, shape):
    sg = (cmds.listConnections(mtl + ".outColor", type="shadingEngine") or [None])[0]
    if sg:
        cmds.sets(shape, edit=True, forceElement=sg)


def vray_proxy(sysm, path, sequence=False):
    if not _ensure_plugin(["vrayformaya"]):
        raise RuntimeError("V-Ray for Maya (vrayformaya) plug-in is not available")
    proxy = sysm.name + "_vrayProxy"
    if not cmds.objExists(proxy):
        before = set(cmds.ls(type="VRayProxy") or [])
        try:
            # official V-Ray command: import an existing proxy file
            mel.eval('vrayCreateProxy -node "%s" -dir "%s" -existing -createProxyNode' % (proxy, path))
        except RuntimeError:
            pass
        created = sorted(set(cmds.ls(type="VRayProxy") or []) - before)
        if created:
            proxy = created[0]
        else:
            # manual fallback: VRayProxy node driving a mesh
            proxy = cmds.createNode("VRayProxy", name=proxy)
            mesh = cmds.createNode("mesh", name=sysm.name + "_vrayProxyShape", parent=_render_parent(sysm))
            cmds.connectAttr(proxy + ".outMesh", mesh + ".inMesh", force=True)
        meshes = cmds.listConnections(proxy + ".outMesh", shapes=True, type="mesh") or []
        for m in meshes:
            _assign(hair_material(sysm, "vray"), m)
    expr = sysm.name + "_vrayProxySeq"
    if cmds.objExists(expr):
        cmds.delete(expr)
    if sequence:
        base = path.rsplit(".", 2)[0]
        cmds.expression(name=expr, alwaysEvaluate=True, string=(
            'int $fr = frame;\n'
            'string $pad = $fr;\n'
            'while (size($pad) < 4) $pad = "0" + $pad;\n'
            'setAttr -type "string" %s.fileName ("%s." + $pad + ".abc");' % (proxy, base)))
    else:
        cmds.setAttr(proxy + ".fileName", path, type="string")
    return proxy


def render_vray(sysm, start=None, end=None, step=1.0):
    if start is None:
        path, n = export_abc(sysm)
        vray_proxy(sysm, path, sequence=False)
        return [path], n
    cur = cmds.currentTime(q=True)
    paths, n = [], 0
    try:
        for f in _frames(start, end, step):
            p, n = export_abc(sysm, f)
            paths.append(p)
    finally:
        cmds.currentTime(cur, update=True)
    vray_proxy(sysm, paths[0], sequence=True)
    return paths, n


def render(sysm, start=None, end=None, step=1.0):
    renderer = sysm.settings().get("renderer", "arnold")
    if renderer == "vray":
        return render_vray(sysm, start, end, step)
    return render_arnold(sysm, start, end, step)


# ------------------------------------------------------- render-time export
HOOK = 'if (!`pluginInfo -q -loaded wamboFur`) loadPlugin wamboFur; wamboFur -renderTime;'


def render_hook_enabled():
    cur = cmds.getAttr("defaultRenderGlobals.preRenderMel") or ""
    return HOOK in cur


def set_render_hook(enabled):
    """Add/remove the Pre Render Frame MEL hook so every rendered frame
    (Render View, batch render, farm) exports the fur first - no manual
    Export step, like a render-time procedural."""
    cur = cmds.getAttr("defaultRenderGlobals.preRenderMel") or ""
    cur = cur.replace(HOOK, "").strip()
    if enabled:
        cur = (cur + ("; " if cur and not cur.endswith(";") else " ") + HOOK).strip()
    cmds.setAttr("defaultRenderGlobals.preRenderMel", cur, type="string")


def render_time_export():
    """Called by the hook before each frame: export every fur system at the
    current frame and make sure its render node reads per-frame files."""
    frame = cmds.currentTime(q=True)
    paths = []
    for name in fsys.list_systems():
        sysm = fsys.FurSystem(name)
        if sysm.settings().get("renderer", "arnold") == "vray":
            path, _ = export_abc(sysm)
            path = _rename_to_frame(path, "abc", frame)
            proxy = sysm.name + "_vrayProxy"
            if not (cmds.objExists(proxy) and cmds.objExists(sysm.name + "_vrayProxySeq")):
                vray_proxy(sysm, path, sequence=True)
            if cmds.objExists(proxy):
                cmds.setAttr(proxy + ".fileName", path, type="string")
        else:
            path, _ = export_ass(sysm, frame)
            arnold_standin(sysm, path, sequence=True)
        paths.append(path)
        print("Wambo Fur: render-time export %s -> %s" % (name, path))
    return paths


def _rename_to_frame(path, ext, frame):
    """export_abc without a frame writes <name>.abc: move it to <name>.####.abc."""
    target = path[:-(len(ext) + 1)] + ".%04d.%s" % (int(round(frame)), ext)
    if os.path.exists(target):
        os.remove(target)
    os.rename(path, target)
    return target
