"""Small, dependency-free vector helpers (tuples of 3 floats)."""
import math

EPS = 1e-12


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_madd(a, b, s):
    """a + b * s"""
    return (a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def v_len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def v_dist(a, b):
    dx, dy, dz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def v_dist2(a, b):
    dx, dy, dz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return dx * dx + dy * dy + dz * dz


def v_norm(a, fallback=(0.0, 1.0, 0.0)):
    l = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    if l < EPS:
        return fallback
    return (a[0] / l, a[1] / l, a[2] / l)


def v_lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def lerp(a, b, t):
    return a + (b - a) * t


def profile(t, root, tip, bias=1.0):
    """Root->tip falloff curve. bias < 1 pushes the effect toward the root,
    bias > 1 toward the tip."""
    if bias <= 0.0:
        bias = 1e-3
    return root + (tip - root) * (t ** bias)


def perpendicular(n):
    """Any unit vector perpendicular to n."""
    ax = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    return v_norm(v_cross(n, ax))


def hash01(i, salt=0):
    """Deterministic per-id random number in [0, 1). Stable regardless of
    evaluation order or preview density."""
    h = (int(i) * 0x9E3779B1 + int(salt) * 0x85EBCA77 + 0x165667B1) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    h = (h * 0x297A2D39) & 0xFFFFFFFF
    h ^= h >> 15
    return h / 4294967296.0


def seg_lengths(pts):
    return [v_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def polyline_length(pts):
    total = 0.0
    for i in range(len(pts) - 1):
        total += v_dist(pts[i], pts[i + 1])
    return total


def _cumulative(pts):
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + v_dist(pts[i], pts[i + 1]))
    return cum


def sample_polyline(pts, cum, s):
    """Point at arc length s along pts (cum = cumulative lengths)."""
    if s <= 0.0:
        return pts[0]
    total = cum[-1]
    if s >= total:
        return pts[-1]
    # binary search
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    seg = cum[hi] - cum[lo]
    t = 0.0 if seg < EPS else (s - cum[lo]) / seg
    return v_lerp(pts[lo], pts[hi], t)


def resample(pts, n, fraction=1.0):
    """Resample a polyline into n points evenly spaced in arc length.
    fraction < 1 keeps only the first part of the curve (used for cutting)."""
    if n < 2:
        return [pts[0]]
    if len(pts) < 2:
        return [pts[0]] * n
    cum = _cumulative(pts)
    total = cum[-1] * fraction
    return [sample_polyline(pts, cum, total * k / (n - 1)) for k in range(n)]


def preserve_lengths(pts, lengths):
    """Rebuild a polyline from its root so every segment keeps the given
    length while following the new directions (length-preserving deform)."""
    out = [pts[0]]
    prev = pts[0]
    for i in range(1, len(pts)):
        d = v_norm(v_sub(pts[i], prev), fallback=v_norm(v_sub(pts[i], pts[i - 1])))
        prev = v_madd(prev, d, lengths[i - 1])
        out.append(prev)
    return out


def catmull_rom_subdivide(pts, subdiv):
    """Smoothly insert `subdiv` points between each pair (uniform Catmull-Rom,
    passes through all original points). Used to make linear render curves smooth."""
    if subdiv <= 0 or len(pts) < 3:
        return list(pts)
    n = len(pts)
    out = []
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else v_sub(v_scale(pts[0], 2.0), pts[1])
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else v_sub(v_scale(pts[-1], 2.0), pts[-2])
        out.append(p1)
        for k in range(1, subdiv + 1):
            t = k / float(subdiv + 1)
            t2, t3 = t * t, t * t * t
            c0 = -0.5 * t3 + t2 - 0.5 * t
            c1 = 1.5 * t3 - 2.5 * t2 + 1.0
            c2 = -1.5 * t3 + 2.0 * t2 + 0.5 * t
            c3 = 0.5 * t3 - 0.5 * t2
            out.append((p0[0] * c0 + p1[0] * c1 + p2[0] * c2 + p3[0] * c3,
                        p0[1] * c0 + p1[1] * c1 + p2[1] * c2 + p3[1] * c3,
                        p0[2] * c0 + p1[2] * c1 + p2[2] * c2 + p3[2] * c3))
    out.append(pts[-1])
    return out
