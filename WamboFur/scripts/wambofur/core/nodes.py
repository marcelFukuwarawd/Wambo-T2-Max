"""Procedural fur operators.

Each operator declares its input ports and parameters and implements
``compute(params, inputs, ctx) -> FurData``. Operators never modify their
inputs (they copy first), so graph results can be cached safely.
"""
import math
import random
from collections import OrderedDict

from .data import FurData, Strand, EvalError
from .mathutil import (v_add, v_sub, v_scale, v_madd, v_norm, v_lerp, v_cross,
                       profile, hash01, resample, seg_lengths,
                       preserve_lengths, polyline_length, perpendicular)
from .noise import vector_noise_array
from .spatial import HashGrid


# --------------------------------------------------------------------------
# parameter description
# --------------------------------------------------------------------------
class P(object):
    """Parameter spec. kind: float int bool string enum vector color
    pick: for string params, what the 'pick from selection' button grabs
    ('mesh', 'curves', 'texture')."""

    def __init__(self, name, kind, default, label=None, lo=None, hi=None,
                 options=None, pick=None, tip=""):
        self.name = name
        self.kind = kind
        self.default = default
        self.label = label or name.replace("_", " ").title()
        self.lo = lo
        self.hi = hi
        self.options = options or []
        self.pick = pick
        self.tip = tip


NODE_TYPES = OrderedDict()

CATEGORY_COLORS = {
    "Import": "#3d8bd6",
    "Generate": "#36b37e",
    "Guide": "#e2a03f",
    "Shape": "#9b6bd6",
    "Modify": "#d65a5a",
    "Utility": "#7a8899",
    "Output": "#e6e6e6",
}


def register(cls):
    NODE_TYPES[cls.type_name] = cls()
    return cls


class Operator(object):
    type_name = ""
    label = ""
    category = "Utility"
    inputs = []          # port names
    params = []          # [P]
    tip = ""

    def defaults(self):
        return dict((p.name, p.default) for p in self.params)

    def param_spec(self, name):
        for p in self.params:
            if p.name == name:
                return p
        return None

    def compute(self, prm, inputs, ctx):
        raise NotImplementedError


def _first(inputs, port=0, required=True, what="input"):
    d = inputs[port] if port < len(inputs) else None
    if d is None and required:
        raise EvalError("missing %s" % what)
    return d


def _profile_params(root=0.0, tip=1.0, bias=1.0):
    return [P("root_weight", "float", root, "Root Weight", 0.0, 1.0, tip="Effect at the root"),
            P("tip_weight", "float", tip, "Tip Weight", 0.0, 1.0, tip="Effect at the tip"),
            P("bias", "float", bias, "Profile Bias", 0.05, 8.0,
              tip="<1 concentrates the effect near the root, >1 near the tip")]


def _affected(strand, fraction, salt):
    return fraction >= 1.0 or hash01(strand.sid, salt) < fraction


def _map_values(ctx, texture, strands):
    if not texture:
        return None
    return ctx.sample_map(texture, [s.uv for s in strands])


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
@register
class ImportGeometry(Operator):
    type_name = "import_geo"
    label = "Import Geometry"
    category = "Import"
    inputs = []
    params = [P("geometry", "string", "", "Mesh", pick="mesh", tip="Growth surface (polygon mesh)")]
    tip = "Brings a polygon mesh in as the growth surface."

    def compute(self, prm, inputs, ctx):
        if not prm["geometry"]:
            raise EvalError("no mesh set")
        return FurData(surface=ctx.get_surface(prm["geometry"]))


@register
class ImportGuides(Operator):
    type_name = "import_guides"
    label = "Import Guides"
    category = "Import"
    inputs = []
    params = [P("curves", "string", "", "Guide Curves", pick="curves",
                tip="Group or curve(s) used as groom guides"),
              P("samples", "int", 12, "Samples", 2, 64, tip="Points sampled along each guide")]
    tip = "Brings groomed guide curves into the graph."

    def compute(self, prm, inputs, ctx):
        if not prm["curves"]:
            raise EvalError("no guide curves set")
        polys = ctx.get_guides(prm["curves"], max(2, int(prm["samples"])))
        guides = []
        for i, pts in enumerate(polys):
            if len(pts) < 2:
                continue
            d = v_norm(v_sub(pts[1], pts[0]))
            guides.append(Strand(list(pts), normal=d, tangent=perpendicular(d), sid=i))
        return FurData(guides=guides)   # may be empty: Guides node then passes through


# --------------------------------------------------------------------------
# Generate
# --------------------------------------------------------------------------
@register
class Scatter(Operator):
    type_name = "scatter"
    label = "Scatter"
    category = "Generate"
    inputs = ["geometry"]
    params = [P("count", "int", 20000, "Density (count)", 0, 5000000,
                tip="Number of strands at render density"),
              P("seed", "int", 1, "Seed", 0, 99999),
              P("density_map", "string", "", "Density Map", pick="texture",
                tip="2D texture node (file, ramp...) - white grows, black is bald")]
    tip = "Scatters strand roots across the surface (area weighted)."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="geometry")
        surf = src.surface
        if surf is None or not surf.triangles:
            raise EvalError("input has no surface")
        count = int(prm["count"])
        n = int(round(count * ctx.density)) if count > 0 else 0
        if count > 0 and ctx.density > 0:
            n = max(n, 1)
        rng = random.Random(int(prm["seed"]) * 7919 + 13)
        cands = []
        for i in range(n):
            t = surf.pick_triangle(rng.random())
            r1, r2 = rng.random(), rng.random()
            s = math.sqrt(r1)
            cands.append((i, t, 1.0 - s, s * (1.0 - r2), s * r2, rng.random(), rng.random()))
        strands = []
        for (i, t, b0, b1, b2, rr, _rej) in cands:
            pos, nrm, uv, tan = surf.sample(t, b0, b1, b2)
            strands.append(Strand([pos], nrm, tan, uv, sid=i, rand=rr, tri=t, bary=(b0, b1, b2)))
        if prm["density_map"]:
            vals = ctx.sample_map(prm["density_map"], [s.uv for s in strands])
            strands = [s for s, c, v in zip(strands, cands, vals) if v > c[6]]
        return FurData(surface=surf, strands=strands, guides=src.guides)


@register
class Grow(Operator):
    type_name = "grow"
    label = "Grow"
    category = "Generate"
    inputs = ["roots"]
    params = [P("length", "float", 1.0, "Length", 0.0, 100.0),
              P("length_random", "float", 0.2, "Length Random", 0.0, 1.0),
              P("segments", "int", 8, "Segments", 1, 64),
              P("inclination", "float", 0.0, "Inclination", 0.0, 1.0,
                tip="Tilt from the surface normal toward the surface U direction"),
              P("length_map", "string", "", "Length Map", pick="texture")]
    tip = "Grows straight strands from the roots along the surface normal."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="roots")
        out = FurData(src.surface, [], src.guides)
        seg = max(1, int(prm["segments"]))
        L0, lr, inc = float(prm["length"]), float(prm["length_random"]), float(prm["inclination"])
        mvals = _map_values(ctx, prm["length_map"], src.strands)
        for idx, s in enumerate(src.strands):
            s = s.copy()
            L = L0 * (1.0 - lr * hash01(s.sid, 101))
            if mvals is not None:
                L *= mvals[idx]
            d = v_norm(v_lerp(s.normal, s.tangent, inc))
            root = s.points[0]
            s.points = [v_madd(root, d, L * k / seg) for k in range(seg + 1)]
            out.strands.append(s)
        return out


# --------------------------------------------------------------------------
# Guides
# --------------------------------------------------------------------------
@register
class GuideShape(Operator):
    type_name = "guides"
    label = "Guides"
    category = "Guide"
    inputs = ["strands", "guides"]
    params = [P("radius", "float", 2.0, "Influence Radius", 0.0001, 1000.0),
              P("max_guides", "int", 3, "Max Guides", 1, 8),
              P("weight", "float", 1.0, "Weight", 0.0, 1.0),
              P("length_weight", "float", 1.0, "Length Weight", 0.0, 1.0,
                tip="1 = strands take the guide length, 0 = keep their own length"),
              P("falloff", "float", 2.0, "Distance Falloff", 0.0, 8.0)]
    tip = "Strands inherit shape (and optionally length) from nearby groomed guides."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, 0, what="strands")
        gsrc = _first(inputs, 1, what="guides")
        guides = gsrc.guides or gsrc.strands
        if not guides:
            # empty groom (e.g. right after creating it): leave strands untouched
            return FurData(src.surface, list(src.strands), src.guides)
        radius = float(prm["radius"])
        kmax = max(1, int(prm["max_guides"]))
        weight = float(prm["weight"])
        lw = float(prm["length_weight"])
        fall = float(prm["falloff"])
        roots = [g.points[0] for g in guides]
        glen = [max(polyline_length(g.points), 1e-9) for g in guides]
        grid = HashGrid(roots, cell=radius)
        cache = {}

        def offsets(gi, n):
            key = (gi, n)
            o = cache.get(key)
            if o is None:
                r = resample(guides[gi].points, n)
                o = [v_sub(p, r[0]) for p in r]
                cache[key] = o
            return o

        out = FurData(src.surface, [], src.guides)
        for s in src.strands:
            s = s.copy()
            root = s.points[0]
            near = grid.within(root, radius)
            if not near:
                near = grid.nearest(root, 1)
            near.sort(key=lambda x: x[1])
            near = near[:kmax]
            n = len(s.points) if len(s.points) > 1 else len(guides[near[0][0]].points)
            own_len = polyline_length(s.points) if len(s.points) > 1 else None
            ws = [1.0 / (math.sqrt(d2) ** fall + 1e-6) if fall > 0 else 1.0 for _, d2 in near]
            wsum = sum(ws)
            acc = [(0.0, 0.0, 0.0)] * n
            for (gi, _), w in zip(near, ws):
                w /= wsum
                if own_len is None:
                    sc = 1.0
                else:
                    sc = (own_len / glen[gi]) * (1.0 - lw) + lw
                off = offsets(gi, n)
                acc = [v_madd(a, o, w * sc) for a, o in zip(acc, off)]
            guided = [v_add(root, a) for a in acc]
            if len(s.points) > 1 and weight < 1.0:
                s.points = [v_lerp(p, g, weight) for p, g in zip(s.points, guided)]
            else:
                s.points = guided
            out.strands.append(s)
        return out


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------
@register
class Clump(Operator):
    type_name = "clump"
    label = "Clump"
    category = "Shape"
    inputs = ["strands"]
    params = [P("clump_count", "int", 300, "Clump Count", 1, 200000),
              P("seed", "int", 3, "Seed", 0, 99999),
              P("flyaways", "float", 0.0, "Flyaway Fraction", 0.0, 1.0,
                tip="Fraction of strands that ignore clumping")] + _profile_params(0.0, 0.85, 1.0)
    tip = "Pulls strands toward the strand at the centre of their clump. Chain several for multi-level clumping."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        strands = src.strands
        out = FurData(src.surface, [], src.guides)
        if not strands:
            return out
        count = max(1, int(prm["clump_count"]))
        seed = int(prm["seed"])
        if src.surface is not None and src.surface.triangles:
            rng = random.Random(seed * 104729 + 7)
            centers = [src.surface.sample(*smp)[0] for smp in src.surface.random_samples(count, rng)]
        else:
            ranked = sorted(strands, key=lambda s: hash01(s.sid, seed + 9001))
            centers = [s.points[0] for s in ranked[:count]]
        grid = HashGrid(centers)
        assign = [grid.nearest(s.points[0], 1)[0] for s in strands]
        leaders = {}
        for si, (ci, d2) in enumerate(assign):
            cur = leaders.get(ci)
            if cur is None or d2 < cur[1]:
                leaders[ci] = (si, d2)
        rw, tw, bias = float(prm["root_weight"]), float(prm["tip_weight"]), float(prm["bias"])
        fly = float(prm["flyaways"])
        res_cache = {}
        for si, s in enumerate(strands):
            s = s.copy()
            ci = assign[si][0]
            li = leaders[ci][0]
            n = len(s.points)
            if li != si and n > 1 and not (fly > 0 and hash01(s.sid, seed + 77) < fly):
                lead = strands[li].points
                if len(lead) != n:
                    key = (li, n)
                    lead = res_cache.get(key) or res_cache.setdefault(key, resample(lead, n))
                pts = []
                for k in range(n):
                    t = k / float(n - 1)
                    w = profile(t, rw, tw, bias)
                    pts.append(v_lerp(s.points[k], lead[k], w))
                pts[0] = s.points[0]
                s.points = pts
            out.strands.append(s)
        return out


@register
class Comb(Operator):
    type_name = "comb"
    label = "Comb"
    category = "Shape"
    inputs = ["strands"]
    params = [P("direction", "vector", (0.0, -1.0, 0.0), "Direction"),
              P("amount", "float", 0.5, "Amount", 0.0, 1.0),
              P("space", "enum", "world", "Space", options=["world", "surface_u"],
                tip="world: fixed direction. surface_u: along the surface U tangent")]
    tip = "Lays strands down toward a direction while keeping their length."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        out = FurData(src.surface, [], src.guides)
        amt = float(prm["amount"])
        wdir = v_norm(tuple(prm["direction"]))
        for s in src.strands:
            s = s.copy()
            n = len(s.points)
            if n > 1 and amt > 0:
                lens = seg_lengths(s.points)
                L = sum(lens)
                d0 = v_norm(v_sub(s.points[-1], s.points[0]))
                cd = s.tangent if prm["space"] == "surface_u" else wdir
                nd = v_norm(v_lerp(d0, cd, amt), fallback=d0)
                delta = v_sub(nd, d0)
                pts = [v_madd(p, delta, L * (k / float(n - 1))) for k, p in enumerate(s.points)]
                s.points = preserve_lengths(pts, lens)
            out.strands.append(s)
        return out


@register
class Gravity(Operator):
    type_name = "gravity"
    label = "Gravity"
    category = "Shape"
    inputs = ["strands"]
    params = [P("amount", "float", 0.3, "Amount", 0.0, 5.0),
              P("direction", "vector", (0.0, -1.0, 0.0), "Direction"),
              P("stiffness", "float", 1.5, "Root Stiffness", 0.1, 6.0,
                tip="Higher values keep the root area straighter")]
    tip = "Bends strands under a force while preserving their length."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        out = FurData(src.surface, [], src.guides)
        g = v_norm(tuple(prm["direction"]))
        amt, stiff = float(prm["amount"]), float(prm["stiffness"])
        for s in src.strands:
            s = s.copy()
            n = len(s.points)
            if n > 1:
                lens = seg_lengths(s.points)
                L = sum(lens)
                pts = [v_madd(p, g, amt * L * (k / float(n - 1)) ** stiff) for k, p in enumerate(s.points)]
                s.points = preserve_lengths(pts, lens)
            out.strands.append(s)
        return out


@register
class Curl(Operator):
    type_name = "curl"
    label = "Curl"
    category = "Shape"
    inputs = ["strands"]
    params = [P("radius", "float", 0.05, "Radius", 0.0, 100.0),
              P("turns", "float", 3.0, "Turns", 0.0, 100.0),
              P("random_phase", "bool", True, "Random Phase"),
              P("radius_random", "float", 0.3, "Radius Random", 0.0, 1.0),
              P("fraction", "float", 1.0, "Fraction", 0.0, 1.0)] + _profile_params(0.0, 1.0, 0.6)
    tip = "Spiral curls around each strand's main axis."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        out = FurData(src.surface, [], src.guides)
        R, turns = float(prm["radius"]), float(prm["turns"])
        rw, tw, bias = float(prm["root_weight"]), float(prm["tip_weight"]), float(prm["bias"])
        for s in src.strands:
            s = s.copy()
            n = len(s.points)
            if n > 1 and _affected(s, float(prm["fraction"]), 303):
                d = v_norm(v_sub(s.points[-1], s.points[0]))
                u = v_norm(v_cross(d, s.tangent), fallback=perpendicular(d))
                w = v_cross(d, u)
                ph = hash01(s.sid, 304) * 2 * math.pi if prm["random_phase"] else 0.0
                r = R * (1.0 - float(prm["radius_random"]) * hash01(s.sid, 305))
                pts = []
                for k, p in enumerate(s.points):
                    t = k / float(n - 1)
                    a = 2 * math.pi * turns * t + ph
                    amp = r * profile(t, rw, tw, bias)
                    pts.append(v_add(p, v_add(v_scale(u, math.cos(a) * amp), v_scale(w, math.sin(a) * amp))))
                pts[0] = s.points[0]
                s.points = pts
            out.strands.append(s)
        return out


# --------------------------------------------------------------------------
# Modify
# --------------------------------------------------------------------------
@register
class Noise(Operator):
    type_name = "noise"
    label = "Noise"
    category = "Modify"
    inputs = ["strands"]
    params = [P("amplitude", "float", 0.1, "Amplitude", 0.0, 100.0),
              P("frequency", "float", 1.0, "Frequency", 0.0, 1000.0),
              P("octaves", "int", 2, "Octaves", 1, 6),
              P("seed", "int", 5, "Seed", 0, 99999),
              P("fraction", "float", 1.0, "Fraction", 0.0, 1.0,
                tip="Portion of strands affected (low fraction + high amplitude = frizz/flyaways)")] \
        + _profile_params(0.0, 1.0, 1.0)
    tip = "Fractal noise displacement - use for frizz, waviness and breakup."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        out = FurData(src.surface, [], src.guides)
        amp, freq = float(prm["amplitude"]), float(prm["frequency"])
        octv, seed = int(prm["octaves"]), int(prm["seed"])
        rw, tw, bias = float(prm["root_weight"]), float(prm["tip_weight"]), float(prm["bias"])
        frac = float(prm["fraction"])
        strands = [s.copy() for s in src.strands]
        # gather every displaced point, evaluate noise in one batch
        idx, pts_in, wts = [], [], []
        for si, s in enumerate(strands):
            n = len(s.points)
            if n > 1 and _affected(s, frac, seed + 401):
                for k in range(1, n):
                    w = amp * profile(k / float(n - 1), rw, tw, bias)
                    if w != 0.0:
                        idx.append((si, k))
                        pts_in.append(s.points[k])
                        wts.append(w)
        scaled = [(p[0] * freq, p[1] * freq, p[2] * freq) for p in pts_in]
        nvs = vector_noise_array(scaled, seed, octv)
        for (si, k), p, nv, w in zip(idx, pts_in, nvs, wts):
            strands[si].points[k] = (p[0] + nv[0] * w, p[1] + nv[1] * w, p[2] + nv[2] * w)
        out.strands = strands
        return out


@register
class Scale(Operator):
    type_name = "scale"
    label = "Length / Scale"
    category = "Modify"
    inputs = ["strands"]
    params = [P("scale", "float", 1.0, "Scale", 0.0, 100.0),
              P("random", "float", 0.0, "Random", 0.0, 1.0),
              P("scale_map", "string", "", "Scale Map", pick="texture")]
    tip = "Scales strand length from the root."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        out = FurData(src.surface, [], src.guides)
        mvals = _map_values(ctx, prm["scale_map"], src.strands)
        for i, s in enumerate(src.strands):
            s = s.copy()
            f = float(prm["scale"]) * (1.0 - float(prm["random"]) * hash01(s.sid, 501))
            if mvals is not None:
                f *= mvals[i]
            r = s.points[0]
            s.points = [v_madd(r, v_sub(p, r), f) for p in s.points]
            out.strands.append(s)
        return out


@register
class Cut(Operator):
    type_name = "cut"
    label = "Cut"
    category = "Modify"
    inputs = ["strands"]
    params = [P("amount", "float", 0.3, "Max Cut", 0.0, 1.0, tip="Maximum fraction of length removed"),
              P("fraction", "float", 1.0, "Fraction", 0.0, 1.0),
              P("seed", "int", 7, "Seed", 0, 99999)]
    tip = "Randomly trims strand tips (keeps the point count)."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        out = FurData(src.surface, [], src.guides)
        seed = int(prm["seed"])
        for s in src.strands:
            s = s.copy()
            if len(s.points) > 1 and _affected(s, float(prm["fraction"]), seed + 601):
                f = 1.0 - float(prm["amount"]) * hash01(s.sid, seed + 602)
                s.points = resample(s.points, len(s.points), max(f, 0.001))
            out.strands.append(s)
        return out


@register
class Width(Operator):
    type_name = "width"
    label = "Width"
    category = "Modify"
    inputs = ["strands"]
    params = [P("root_width", "float", 0.01, "Root Width", 0.0, 10.0),
              P("tip_width", "float", 0.001, "Tip Width", 0.0, 10.0),
              P("random", "float", 0.1, "Random", 0.0, 1.0),
              P("bias", "float", 1.0, "Taper Bias", 0.05, 8.0)]
    tip = "Sets strand thickness (root to tip taper) used at render time."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        out = FurData(src.surface, [], src.guides)
        for s in src.strands:
            s = s.copy()
            f = 1.0 - float(prm["random"]) * hash01(s.sid, 701)
            s.root_width = float(prm["root_width"]) * f
            s.tip_width = float(prm["tip_width"]) * f
            s.width_bias = float(prm["bias"])
            out.strands.append(s)
        return out


@register
class Prune(Operator):
    type_name = "prune"
    label = "Prune"
    category = "Modify"
    inputs = ["strands"]
    params = [P("fraction", "float", 0.2, "Remove Fraction", 0.0, 1.0),
              P("seed", "int", 11, "Seed", 0, 99999),
              P("map", "string", "", "Keep Map", pick="texture", tip="Black removes strands")]
    tip = "Randomly removes strands."

    def compute(self, prm, inputs, ctx):
        src = _first(inputs, what="strands")
        seed, frac = int(prm["seed"]), float(prm["fraction"])
        mvals = _map_values(ctx, prm["map"], src.strands)
        keep = []
        for i, s in enumerate(src.strands):
            h = hash01(s.sid, seed + 801)
            if h < frac:
                continue
            if mvals is not None and mvals[i] < hash01(s.sid, seed + 802):
                continue
            keep.append(s)
        return FurData(src.surface, keep, src.guides)   # strands untouched -> no copy


# --------------------------------------------------------------------------
# Utility / Output
# --------------------------------------------------------------------------
@register
class Merge(Operator):
    type_name = "merge"
    label = "Merge"
    category = "Utility"
    inputs = ["a", "b"]
    params = []
    tip = "Combines two strand streams (e.g. body fur + mane)."

    def compute(self, prm, inputs, ctx):
        a = _first(inputs, 0, what="input A")
        b = _first(inputs, 1, required=False)
        strands = list(a.strands)
        if b is not None:
            for s in b.strands:
                s = s.copy()
                s.sid += 10000000
                strands.append(s)
        return FurData(a.surface, strands, a.guides)


@register
class Output(Operator):
    type_name = "output"
    label = "Output"
    category = "Output"
    inputs = ["strands"]
    params = []
    tip = "Final result - this is what gets rendered."

    def compute(self, prm, inputs, ctx):
        return _first(inputs, what="strands")
