"""Deterministic 3D value noise + fractal noise (no numpy needed)."""
import math


def _hash3(ix, iy, iz, seed):
    h = (ix * 374761393 + iy * 668265263 + iz * 1440662683 + seed * 1013904223) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFFFF) / 8388607.5 - 1.0  # [-1, 1]


def _fade(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def value_noise(x, y, z, seed=0):
    fx0, fy0, fz0 = math.floor(x), math.floor(y), math.floor(z)
    fx, fy, fz = x - fx0, y - fy0, z - fz0
    ix, iy, iz = int(fx0), int(fy0), int(fz0)
    u = fx * fx * fx * (fx * (fx * 6.0 - 15.0) + 10.0)
    v = fy * fy * fy * (fy * (fy * 6.0 - 15.0) + 10.0)
    w = fz * fz * fz * (fz * (fz * 6.0 - 15.0) + 10.0)
    sx0 = ix * 374761393
    sx1 = sx0 + 374761393
    sy0 = iy * 668265263
    sy1 = sy0 + 668265263
    sz0 = iz * 1440662683 + seed * 1013904223
    sz1 = sz0 + 1440662683
    M = 0xFFFFFFFF
    vals = []
    for hz in (sz0, sz1):
        for hy in (sy0, sy1):
            for hx in (sx0, sx1):
                h = (hx + hy + hz) & M
                h = ((h ^ (h >> 13)) * 1274126177) & M
                h ^= h >> 16
                vals.append((h & 0xFFFFFF) / 8388607.5 - 1.0)
    a000, a100, a010, a110, a001, a101, a011, a111 = vals
    x00 = a000 + (a100 - a000) * u
    x10 = a010 + (a110 - a010) * u
    x01 = a001 + (a101 - a001) * u
    x11 = a011 + (a111 - a011) * u
    y0 = x00 + (x10 - x00) * v
    y1 = x01 + (x11 - x01) * v
    return y0 + (y1 - y0) * w


def fbm(x, y, z, seed=0, octaves=1, lacunarity=2.0, gain=0.5):
    total, amp, norm = 0.0, 1.0, 0.0
    for o in range(max(1, int(octaves))):
        total += value_noise(x, y, z, seed + o * 17) * amp
        norm += amp
        amp *= gain
        x, y, z = x * lacunarity, y * lacunarity, z * lacunarity
    return total / norm


def vector_noise(x, y, z, seed=0, octaves=1):
    return (fbm(x, y, z, seed, octaves),
            fbm(x + 31.416, y + 47.853, z + 12.793, seed + 101, octaves),
            fbm(x - 19.191, y + 73.137, z - 51.977, seed + 211, octaves))


# ---------------------------------------------------------------- numpy path
try:
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

HAVE_NUMPY = _np is not None


def _value_noise_np(P, seed):
    """Vectorised value_noise for an (N, 3) float array. Same results as
    value_noise()."""
    np = _np
    F = np.floor(P)
    f = P - F
    I = F.astype(np.int64)
    fade = f * f * f * (f * (f * 6.0 - 15.0) + 10.0)
    M = np.int64(0xFFFFFFFF)
    sx = I[:, 0] * 374761393
    sy = I[:, 1] * 668265263
    sz = I[:, 2] * 1440662683 + seed * 1013904223
    vals = {}
    for dz in (0, 1):
        for dy in (0, 1):
            for dx in (0, 1):
                h = (sx + dx * 374761393 + sy + dy * 668265263 + sz + dz * 1440662683) & M
                h = ((h ^ (h >> 13)) * 1274126177) & M
                h ^= h >> 16
                vals[(dx, dy, dz)] = (h & 0xFFFFFF) / 8388607.5 - 1.0
    u, v, w = fade[:, 0], fade[:, 1], fade[:, 2]
    x00 = vals[0, 0, 0] + (vals[1, 0, 0] - vals[0, 0, 0]) * u
    x10 = vals[0, 1, 0] + (vals[1, 1, 0] - vals[0, 1, 0]) * u
    x01 = vals[0, 0, 1] + (vals[1, 0, 1] - vals[0, 0, 1]) * u
    x11 = vals[0, 1, 1] + (vals[1, 1, 1] - vals[0, 1, 1]) * u
    y0 = x00 + (x10 - x00) * v
    y1 = x01 + (x11 - x01) * v
    return y0 + (y1 - y0) * w


def _fbm_np(P, seed, octaves):
    total = 0.0
    amp, norm = 1.0, 0.0
    for o in range(max(1, int(octaves))):
        total = total + _value_noise_np(P, seed + o * 17) * amp
        norm += amp
        amp *= 0.5
        P = P * 2.0
    return total / norm


def vector_noise_array(points, seed=0, octaves=1):
    """points: list of (x,y,z) -> list of noise vectors. Uses numpy when
    available, otherwise the pure Python implementation."""
    if not points:
        return []
    if _np is None:
        return [vector_noise(p[0], p[1], p[2], seed, octaves) for p in points]
    P = _np.asarray(points, dtype=_np.float64)
    nx = _fbm_np(P, seed, octaves)
    ny = _fbm_np(P + _np.array([31.416, 47.853, 12.793]), seed + 101, octaves)
    nz = _fbm_np(P + _np.array([-19.191, 73.137, -51.977]), seed + 211, octaves)
    return _np.stack([nx, ny, nz], axis=1).tolist()
