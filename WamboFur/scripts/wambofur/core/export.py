"""Renderer exporters that do not need Maya.

* write_ass(): Arnold scene-source (.ass) with a `curves` node + embedded
  `standard_hair` shader. Load it in Maya with an aiStandIn (exactly how
  commercial fur tools hand procedurals to Arnold).
* render_strands(): shared helper giving smoothed points + per-point widths,
  also used by the Alembic (V-Ray Proxy) path in maya/render.py.
"""
import os

from .mathutil import catmull_rom_subdivide

DEFAULT_SHADER = {
    "base_color": (1.0, 1.0, 1.0),
    "melanin": 0.45,
    "melanin_redness": 0.5,
    "melanin_randomize": 0.1,
    "roughness": 0.25,
    "ior": 1.55,
}


def render_strands(strands, subdiv=1, width_scale=1.0):
    """Yield (points, widths, strand) with Catmull-Rom smoothing applied."""
    for s in strands:
        if len(s.points) < 2:
            continue
        pts = catmull_rom_subdivide(s.points, subdiv)
        n = len(pts)
        rb = max(s.width_bias, 1e-3)
        ws = [(s.root_width + (s.tip_width - s.root_width) * ((k / float(n - 1)) ** rb)) * width_scale
              for k in range(n)]
        yield pts, ws, s


def _fmt(v):
    return "%.6g" % v


def _write_array(f, name, typ, values, per_line=12):
    f.write(" %s %d 1 %s\n" % (name, len(values), typ))
    line = []
    for v in values:
        line.append(v)
        if len(line) >= per_line:
            f.write("  " + " ".join(line) + "\n")
            line = []
    if line:
        f.write("  " + " ".join(line) + "\n")


def write_ass(path, strands, node_name="wamboFur", subdiv=1, width_scale=1.0,
              shader=None, min_pixel_width=0.0):
    """Write an Arnold .ass file. Returns number of curves written."""
    sh = dict(DEFAULT_SHADER)
    if shader:
        sh.update(shader)
    counts, pts_out, radius, uparam, vparam, rand = [], [], [], [], [], []
    for pts, ws, s in render_strands(strands, subdiv, width_scale):
        counts.append(str(len(pts)))
        for p in pts:
            pts_out.append("%s %s %s" % (_fmt(p[0]), _fmt(p[1]), _fmt(p[2])))
        radius.extend(_fmt(max(w * 0.5, 0.0)) for w in ws)
        uparam.append(_fmt(s.uv[0]))
        vparam.append(_fmt(s.uv[1]))
        rand.append(_fmt(s.rand))
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    shader_name = node_name + "_hairShader"
    with open(path, "w") as f:
        f.write("### Wambo Fur Arnold procedural\n")
        f.write("### curves: %d\n\n" % len(counts))
        bc = sh["base_color"]
        f.write("standard_hair\n{\n name %s\n" % shader_name)
        f.write(" base_color %s %s %s\n" % (_fmt(bc[0]), _fmt(bc[1]), _fmt(bc[2])))
        for k in ("melanin", "melanin_redness", "melanin_randomize", "roughness", "ior"):
            f.write(" %s %s\n" % (k, _fmt(float(sh[k]))))
        f.write("}\n\n")
        if not counts:
            return 0
        f.write("curves\n{\n name %s\n" % node_name)
        _write_array(f, "num_points", "UINT", counts, 24)
        _write_array(f, "points", "VECTOR", pts_out, 4)
        _write_array(f, "radius", "FLOAT", radius, 16)
        f.write(' basis "linear"\n mode "thick"\n')
        if min_pixel_width > 0:
            f.write(" min_pixel_width %s\n" % _fmt(min_pixel_width))
        # user data (read in shading with aiUserDataVec2 / aiUserDataFloat)
        f.write(" declare wf_rootuv uniform VECTOR2\n")
        _write_array(f, "wf_rootuv", "VECTOR2", ["%s %s" % uv for uv in zip(uparam, vparam)], 8)
        f.write(" declare wf_rand uniform FLOAT\n")
        _write_array(f, "wf_rand", "FLOAT", rand, 16)
        f.write(" shader %s\n" % shader_name)
        f.write("}\n")
    return len(counts)
