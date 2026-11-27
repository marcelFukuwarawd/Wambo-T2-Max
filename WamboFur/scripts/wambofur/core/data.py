"""Data flowing through the fur graph."""
import bisect
import math
from .mathutil import (v_sub, v_scale, v_cross, v_norm, v_len, v_dot,
                       perpendicular, polyline_length)


class EvalError(Exception):
    pass


class Surface(object):
    """Triangulated growth surface (world space).

    points    : [(x, y, z)]       vertex positions
    normals   : [(x, y, z)]       per-vertex normals
    triangles : [(a, b, c)]       vertex indices
    tri_uvs   : [((u,v),(u,v),(u,v))] per-triangle-corner UVs, or None
    """

    def __init__(self, points, triangles, normals=None, tri_uvs=None, name="", rest_points=None):
        self.name = name
        self.points = points
        # rest pose (undeformed) positions: used for area weighting so that
        # roots stay on the same triangles while the mesh deforms
        self.rest_points = rest_points if rest_points and len(rest_points) == len(points) else None
        self.triangles = triangles
        self.tri_uvs = tri_uvs
        self.normals = normals or self._compute_normals()
        self._cum = None
        self._tangents = {}

    def _compute_normals(self):
        acc = [[0.0, 0.0, 0.0] for _ in self.points]
        for a, b, c in self.triangles:
            n = v_cross(v_sub(self.points[b], self.points[a]), v_sub(self.points[c], self.points[a]))
            for i in (a, b, c):
                acc[i][0] += n[0]
                acc[i][1] += n[1]
                acc[i][2] += n[2]
        return [v_norm(tuple(n)) for n in acc]

    def tri_area(self, t, rest=True):
        a, b, c = self.triangles[t]
        P = self.rest_points if (rest and self.rest_points) else self.points
        return 0.5 * v_len(v_cross(v_sub(P[b], P[a]), v_sub(P[c], P[a])))

    @property
    def cumulative_areas(self):
        if self._cum is None:
            cum, total = [], 0.0
            for t in range(len(self.triangles)):
                total += self.tri_area(t)
                cum.append(total)
            self._cum = cum
        return self._cum

    @property
    def total_area(self):
        cum = self.cumulative_areas
        return cum[-1] if cum else 0.0

    def pick_triangle(self, r):
        """r in [0, 1) -> triangle index, area weighted."""
        cum = self.cumulative_areas
        i = bisect.bisect_right(cum, r * cum[-1])
        return min(i, len(cum) - 1)

    def tangent(self, t):
        """Per-triangle tangent along +U (falls back to an arbitrary
        perpendicular when there are no UVs)."""
        tan = self._tangents.get(t)
        if tan is not None:
            return tan
        a, b, c = self.triangles[t]
        pa, pb, pc = self.points[a], self.points[b], self.points[c]
        e1, e2 = v_sub(pb, pa), v_sub(pc, pa)
        n = v_norm(v_cross(e1, e2))
        tan = None
        if self.tri_uvs:
            (u0, v0), (u1, v1), (u2, v2) = self.tri_uvs[t]
            du1, dv1, du2, dv2 = u1 - u0, v1 - v0, u2 - u0, v2 - v0
            det = du1 * dv2 - du2 * dv1
            if abs(det) > 1e-12:
                r = 1.0 / det
                tan = v_scale(v_sub(v_scale(e1, dv2), v_scale(e2, dv1)), r)
                # orthogonalise against normal
                tan = v_norm(v_sub(tan, v_scale(n, v_dot(tan, n))), fallback=None)
        if tan is None:
            tan = perpendicular(n)
        self._tangents[t] = tan
        return tan

    def sample(self, t, b0, b1, b2):
        """Barycentric sample -> (position, normal, uv, tangent)."""
        a, b, c = self.triangles[t]
        P, N = self.points, self.normals
        pos = (P[a][0] * b0 + P[b][0] * b1 + P[c][0] * b2,
               P[a][1] * b0 + P[b][1] * b1 + P[c][1] * b2,
               P[a][2] * b0 + P[b][2] * b1 + P[c][2] * b2)
        nrm = v_norm((N[a][0] * b0 + N[b][0] * b1 + N[c][0] * b2,
                      N[a][1] * b0 + N[b][1] * b1 + N[c][1] * b2,
                      N[a][2] * b0 + N[b][2] * b1 + N[c][2] * b2))
        if self.tri_uvs:
            (u0, v0), (u1, v1), (u2, v2) = self.tri_uvs[t]
            uv = (u0 * b0 + u1 * b1 + u2 * b2, v0 * b0 + v1 * b1 + v2 * b2)
        else:
            uv = (0.0, 0.0)
        tan = self.tangent(t)
        # make the tangent perpendicular to the smooth normal
        tan = v_norm(v_sub(tan, v_scale(nrm, v_dot(tan, nrm))), fallback=perpendicular(nrm))
        return pos, nrm, uv, tan

    def random_samples(self, count, rng):
        """count area-weighted random samples: [(tri, b0, b1, b2)]."""
        out = []
        if not self.triangles:
            return out
        for _ in range(count):
            t = self.pick_triangle(rng.random())
            r1, r2 = rng.random(), rng.random()
            s = math.sqrt(r1)
            out.append((t, 1.0 - s, s * (1.0 - r2), s * r2))
        return out


class Strand(object):
    """A single hair. points[0] is the root."""
    __slots__ = ("points", "normal", "tangent", "uv", "sid", "rand",
                 "tri", "bary", "root_width", "tip_width", "width_bias")

    def __init__(self, points, normal=(0.0, 1.0, 0.0), tangent=(1.0, 0.0, 0.0),
                 uv=(0.0, 0.0), sid=0, rand=0.0, tri=-1, bary=(1.0, 0.0, 0.0),
                 root_width=0.01, tip_width=0.001, width_bias=1.0):
        self.points = points
        self.normal = normal
        self.tangent = tangent
        self.uv = uv
        self.sid = sid
        self.rand = rand
        self.tri = tri
        self.bary = bary
        self.root_width = root_width
        self.tip_width = tip_width
        self.width_bias = width_bias

    @property
    def root(self):
        return self.points[0]

    def length(self):
        return polyline_length(self.points)

    def copy(self):
        return Strand(list(self.points), self.normal, self.tangent, self.uv, self.sid,
                      self.rand, self.tri, self.bary, self.root_width, self.tip_width,
                      self.width_bias)

    def widths(self):
        n = len(self.points)
        if n < 2:
            return [self.root_width]
        out = []
        for k in range(n):
            t = k / float(n - 1)
            out.append(self.root_width + (self.tip_width - self.root_width) * (t ** max(self.width_bias, 1e-3)))
        return out


class FurData(object):
    """Value passed between graph nodes."""

    def __init__(self, surface=None, strands=None, guides=None):
        self.surface = surface
        self.strands = strands if strands is not None else []
        self.guides = guides

    def copy(self):
        """Copy with independent strands (surface/guides are shared, read-only)."""
        return FurData(self.surface, [s.copy() for s in self.strands], self.guides)

    def point_count(self):
        return sum(len(s.points) for s in self.strands)

    def __repr__(self):
        return "<FurData strands=%d guides=%s surface=%s>" % (
            len(self.strands), len(self.guides) if self.guides else 0,
            self.surface.name if self.surface else None)
