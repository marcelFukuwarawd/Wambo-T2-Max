"""Uniform hash grid for nearest-neighbour queries on 3D points."""
import math
from .mathutil import v_dist2


class HashGrid(object):
    def __init__(self, points, cell=None):
        self.points = points
        if cell is None:
            cell = self.estimate_cell(points)
        self.cell = max(cell, 1e-6)
        self.cells = {}
        inv = 1.0 / self.cell
        for i, p in enumerate(points):
            key = (int(math.floor(p[0] * inv)), int(math.floor(p[1] * inv)), int(math.floor(p[2] * inv)))
            self.cells.setdefault(key, []).append(i)

    @staticmethod
    def estimate_cell(points):
        if not points:
            return 1.0
        mn = [min(p[k] for p in points) for k in range(3)]
        mx = [max(p[k] for p in points) for k in range(3)]
        ext = [mx[k] - mn[k] for k in range(3)]
        ext = sorted(ext, reverse=True)
        # points usually lie on a surface: use the 2 largest extents
        area = max(ext[0] * max(ext[1], ext[0] * 0.01), 1e-8)
        return math.sqrt(area / max(len(points), 1)) * 2.0

    def _key(self, p):
        inv = 1.0 / self.cell
        return (int(math.floor(p[0] * inv)), int(math.floor(p[1] * inv)), int(math.floor(p[2] * inv)))

    def within(self, p, radius):
        """List of (index, dist2) for points within radius of p."""
        r2 = radius * radius
        rings = int(math.ceil(radius / self.cell))
        kx, ky, kz = self._key(p)
        out = []
        cells = self.cells
        pts = self.points
        for dx in range(-rings, rings + 1):
            for dy in range(-rings, rings + 1):
                for dz in range(-rings, rings + 1):
                    bucket = cells.get((kx + dx, ky + dy, kz + dz))
                    if not bucket:
                        continue
                    for i in bucket:
                        d2 = v_dist2(p, pts[i])
                        if d2 <= r2:
                            out.append((i, d2))
        return out

    def nearest(self, p, k=1, max_rings=4):
        """k nearest neighbours as [(index, dist2)], closest first."""
        kx, ky, kz = self._key(p)
        cells = self.cells
        pts = self.points
        found = []
        for ring in range(0, max_rings + 1):
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for dz in range(-ring, ring + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        bucket = cells.get((kx + dx, ky + dy, kz + dz))
                        if bucket:
                            for i in bucket:
                                found.append((i, v_dist2(p, pts[i])))
            # once we have k candidates and searched one ring further, done
            if len(found) >= k and ring >= 1:
                found.sort(key=lambda x: x[1])
                # guarantee correctness: every candidate closer than ring*cell
                if found[k - 1][1] <= (ring * self.cell) ** 2:
                    return found[:k]
        if len(found) < k:
            # brute force fallback (sparse data)
            found = [(i, v_dist2(p, q)) for i, q in enumerate(pts)]
        found.sort(key=lambda x: x[1])
        return found[:k]
