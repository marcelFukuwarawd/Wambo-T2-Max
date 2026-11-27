"""Groom guides stored natively (no Maya curves) + binding to the surface.

Each guide keeps:
  pts    : world-space points at groom time (root first)
  tri    : triangle index on the growth surface (engine triangulation)
  bary   : barycentric coordinates of the root in that triangle
  frame0 : (root, normal, tangent) of the triangle at groom time

At evaluation the guide is re-expressed in the triangle's CURRENT frame, so
guides follow skinning / deformation / transforms of the mesh.
"""
from .mathutil import (v_sub, v_add, v_scale, v_cross, v_dot, v_norm, v_madd)


def tri_frame(points, tri_indices, bary):
    """(root, normal, tangent) for barycentric `bary` on triangle (a, b, c)."""
    a, b, c = tri_indices
    pa, pb, pc = points[a], points[b], points[c]
    b0, b1, b2 = bary
    root = (pa[0] * b0 + pb[0] * b1 + pc[0] * b2,
            pa[1] * b0 + pb[1] * b1 + pc[1] * b2,
            pa[2] * b0 + pb[2] * b1 + pc[2] * b2)
    e1 = v_sub(pb, pa)
    n = v_norm(v_cross(e1, v_sub(pc, pa)))
    t = v_norm(v_sub(e1, v_scale(n, v_dot(e1, n))), fallback=(1.0, 0.0, 0.0))
    return root, n, t


def _to_local(v, n, t):
    b = v_cross(n, t)
    return (v_dot(v, t), v_dot(v, n), v_dot(v, b))


def _to_world(l, n, t):
    b = v_cross(n, t)
    return (t[0] * l[0] + n[0] * l[1] + b[0] * l[2],
            t[1] * l[0] + n[1] * l[1] + b[1] * l[2],
            t[2] * l[0] + n[2] * l[1] + b[2] * l[2])


def rebind(pts, frame0, frame1):
    """Move a guide from frame0 to frame1 (rigid per guide)."""
    r0, n0, t0 = frame0
    r1, n1, t1 = frame1
    if r0 == r1 and n0 == n1 and t0 == t1:
        return list(pts)
    return [v_add(r1, _to_world(_to_local(v_sub(p, r0), n0, t0), n1, t1)) for p in pts]


def barycentric(p, pa, pb, pc):
    """Barycentric coordinates of p projected on triangle (pa, pb, pc)."""
    v0, v1, v2 = v_sub(pb, pa), v_sub(pc, pa), v_sub(p, pa)
    d00, d01, d11 = v_dot(v0, v0), v_dot(v0, v1), v_dot(v1, v1)
    d20, d21 = v_dot(v2, v0), v_dot(v2, v1)
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-18:
        return (1.0, 0.0, 0.0)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    return (1.0 - v - w, v, w)


def bind_point(surface, p, candidate_tris=None):
    """Find (tri, bary) for a point on/near the surface. candidate_tris
    narrows the search (e.g. the triangles of the face hit by a ray)."""
    tris = candidate_tris if candidate_tris is not None else range(len(surface.triangles))
    best, best_err = None, None
    P = surface.points
    for t in tris:
        a, b, c = surface.triangles[t]
        bc = barycentric(p, P[a], P[b], P[c])
        # clamp to the triangle and measure how far we are
        cl = tuple(max(0.0, x) for x in bc)
        s = sum(cl) or 1.0
        cl = (cl[0] / s, cl[1] / s, cl[2] / s)
        q = tri_frame(P, (a, b, c), cl)[0]
        err = v_dot(v_sub(q, p), v_sub(q, p))
        if best_err is None or err < best_err:
            best, best_err = (t, cl), err
    return best


class Guide(object):
    __slots__ = ("pts", "tri", "bary", "frame0")

    def __init__(self, pts, tri=-1, bary=(1.0, 0.0, 0.0), frame0=None):
        self.pts = pts
        self.tri = tri
        self.bary = bary
        self.frame0 = frame0

    def copy(self):
        return Guide(list(self.pts), self.tri, self.bary, self.frame0)


def bound_guide(surface, pts, tri, bary):
    frame = tri_frame(surface.points, surface.triangles[tri], bary)
    pts = list(pts)
    pts[0] = frame[0]
    return Guide(pts, tri, bary, frame)


def deform_guides(guides, surface):
    """Guides expressed on the current surface (follows deformation)."""
    out = []
    ntri = len(surface.triangles)
    for g in guides:
        if g.frame0 is None or not (0 <= g.tri < ntri):
            out.append(list(g.pts))
            continue
        f1 = tri_frame(surface.points, surface.triangles[g.tri], g.bary)
        out.append(rebind(g.pts, g.frame0, f1))
    return out


def inherit_shape(new_root, new_normal, guides, length=None, k=3):
    """Shape for a new guide copied from its nearest neighbours (rotated so
    their root segment follows the new normal), like painting new guides
    into an existing groom. Falls back to a straight guide."""
    if not guides:
        return None
    near = sorted(guides, key=lambda g: v_dot(v_sub(g.pts[0], new_root), v_sub(g.pts[0], new_root)))[:k]
    n = len(near[0].pts)
    acc = [(0.0, 0.0, 0.0)] * n
    wsum = 0.0
    for g in near:
        if len(g.pts) != n:
            continue
        d2 = v_dot(v_sub(g.pts[0], new_root), v_sub(g.pts[0], new_root))
        w = 1.0 / (d2 + 1e-6)
        if g.frame0 is not None:
            r0, n0, t0 = g.frame0
            # same local offsets, re-expressed around the new normal
            t1 = v_norm(v_sub(t0, v_scale(new_normal, v_dot(t0, new_normal))), fallback=t0)
            offs = [_to_world(_to_local(v_sub(p, g.pts[0]), n0, t0), new_normal, t1) for p in g.pts]
        else:
            offs = [v_sub(p, g.pts[0]) for p in g.pts]
        acc = [v_madd(a, o, w) for a, o in zip(acc, offs)]
        wsum += w
    if wsum == 0.0:
        return None
    pts = [v_add(new_root, v_scale(a, 1.0 / wsum)) for a in acc]
    if length:
        from .mathutil import polyline_length
        L = polyline_length(pts)
        if L > 1e-9:
            pts = [v_madd(new_root, v_sub(p, new_root), length / L) for p in pts]
    return pts
