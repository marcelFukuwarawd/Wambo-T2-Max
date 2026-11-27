import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

from wambofur.core.guides import (bound_guide, deform_guides, bind_point, inherit_shape,  # noqa: E402
                                  tri_frame)
from wambofur.core.data import Surface                                          # noqa: E402
from wambofur.core.mathutil import polyline_length, v_dist                      # noqa: E402
from test_core import grid_plane                                                # noqa: E402


def rotated(surface, ang, offset=(0.0, 0.0, 0.0)):
    c, s = math.cos(ang), math.sin(ang)
    pts = [(x * c - y * s + offset[0], x * s + y * c + offset[1], z + offset[2]) for x, y, z in surface.points]
    return Surface(pts, surface.triangles, tri_uvs=surface.tri_uvs)


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.s = grid_plane()
        t = 37
        root = tri_frame(self.s.points, self.s.triangles[t], (0.2, 0.3, 0.5))[0]
        pts = [(root[0] + 0.1 * k, root[1] + 0.3 * k, root[2]) for k in range(6)]
        self.g = bound_guide(self.s, pts, t, (0.2, 0.3, 0.5))

    def test_identity(self):
        out = deform_guides([self.g], self.s)[0]
        for a, b in zip(out, self.g.pts):
            self.assertLess(v_dist(a, b), 1e-9)

    def test_follows_rigid_motion(self):
        ang = math.radians(90)
        moved = rotated(self.s, ang, (5.0, 1.0, 2.0))
        out = deform_guides([self.g], moved)[0]
        c, s = math.cos(ang), math.sin(ang)
        for p, q in zip(self.g.pts, out):
            expect = (p[0] * c - p[1] * s + 5.0, p[0] * s + p[1] * c + 1.0, p[2] + 2.0)
            self.assertLess(v_dist(q, expect), 1e-6)
        self.assertAlmostEqual(polyline_length(out), polyline_length(self.g.pts), 6)

    def test_bind_point(self):
        p = tri_frame(self.s.points, self.s.triangles[11], (0.1, 0.6, 0.3))[0]
        tri, bary = bind_point(self.s, p)
        q = tri_frame(self.s.points, self.s.triangles[tri], bary)[0]
        self.assertLess(v_dist(p, q), 1e-9)

    def test_inherit(self):
        new_root = (self.g.pts[0][0] + 0.2, 0.0, self.g.pts[0][2])
        pts = inherit_shape(new_root, (0.0, 1.0, 0.0), [self.g])
        self.assertEqual(len(pts), len(self.g.pts))
        self.assertLess(v_dist(pts[0], new_root), 1e-9)
        self.assertAlmostEqual(polyline_length(pts), polyline_length(self.g.pts), 6)


if __name__ == "__main__":
    unittest.main()
