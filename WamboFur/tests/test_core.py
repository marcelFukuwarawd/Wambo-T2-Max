"""Run with:  python3 -m unittest discover -s tests   (no Maya needed)"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from wambofur.core.data import Surface, EvalError          # noqa: E402
from wambofur.core.graph import Graph, EvalContext, default_graph  # noqa: E402
from wambofur.core.nodes import NODE_TYPES                  # noqa: E402
from wambofur.core.export import write_ass                  # noqa: E402
from wambofur.core.mathutil import polyline_length  # noqa: E402


def grid_plane(n=10, size=10.0):
    pts, tris, uvs = [], [], []
    for j in range(n + 1):
        for i in range(n + 1):
            pts.append((i * size / n - size / 2, 0.0, j * size / n - size / 2))
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b, c, d = a + 1, a + n + 1, a + n + 2
            uv = lambda k: ((k % (n + 1)) / float(n), (k // (n + 1)) / float(n))
            tris.append((a, c, b)); uvs.append((uv(a), uv(c), uv(b)))
            tris.append((b, c, d)); uvs.append((uv(b), uv(c), uv(d)))
    return Surface(pts, tris, tri_uvs=uvs, name="plane")


class Resolver(object):
    def __init__(self):
        self.surface = grid_plane()

    def get_surface(self, name):
        if name != "plane":
            raise EvalError("no mesh %s" % name)
        return self.surface

    def get_guides(self, name, samples):
        # two bent guides
        return [[(-3 + 0.1 * k * k, k * 0.3, 0) for k in range(samples)],
                [(3, k * 0.5, 0.05 * k * k) for k in range(samples)]]

    def sample_map(self, tex, uvs):
        return [1.0 if u > 0.5 else 0.0 for u, v in uvs]


class CoreTests(unittest.TestCase):
    def test_normals_up(self):
        s = grid_plane()
        self.assertAlmostEqual(s.normals[0][1], 1.0, 5)
        self.assertAlmostEqual(s.total_area, 100.0, 5)

    def test_default_graph(self):
        g = default_graph("plane", "guides")
        ctx = EvalContext(density=0.05, resolver=Resolver())
        res = g.evaluate(g.display_id, ctx)
        self.assertEqual(len(res.strands), 1000)
        for s in res.strands[:50]:
            self.assertEqual(len(s.points), 9)
            self.assertAlmostEqual(s.points[0][1], 0.0, 6)   # roots on the plane
        self.assertFalse(ctx.errors)

    def test_every_node_runs(self):
        r = Resolver()
        for t, op in NODE_TYPES.items():
            if t in ("import_geo", "import_guides", "scatter", "grow", "guides", "output", "merge"):
                continue
            g = Graph()
            a = g.add_node("import_geo", params={"geometry": "plane"})
            b = g.add_node("scatter", params={"count": 200})
            c = g.add_node("grow")
            d = g.add_node(t)
            g.connect(a.id, b.id, 0); g.connect(b.id, c.id, 0); g.connect(c.id, d.id, 0)
            res = g.evaluate(d.id, EvalContext(1.0, r))
            self.assertTrue(len(res.strands) > 0, t)

    def test_preview_is_prefix_of_render(self):
        g = default_graph("plane")
        lo = g.evaluate(g.output_id(), EvalContext(0.01, Resolver()))
        hi = g.evaluate(g.output_id(), EvalContext(0.02, Resolver()))
        self.assertEqual(lo.strands[0].points[0], hi.strands[0].points[0])

    def test_grow_length_and_gravity_preserves_length(self):
        g = Graph()
        a = g.add_node("import_geo", params={"geometry": "plane"})
        b = g.add_node("scatter", params={"count": 50})
        c = g.add_node("grow", params={"length": 2.0, "length_random": 0.0})
        d = g.add_node("gravity", params={"amount": 1.0, "direction": (1, 0, 0)})
        g.connect(a.id, b.id, 0); g.connect(b.id, c.id, 0); g.connect(c.id, d.id, 0)
        res = g.evaluate(d.id, EvalContext(1.0, Resolver()))
        for s in res.strands:
            self.assertAlmostEqual(polyline_length(s.points), 2.0, 4)
            self.assertGreater(s.points[-1][0] - s.points[0][0], 0.1)

    def test_guides_follow(self):
        g = default_graph("plane", "guides")
        res = g.evaluate(g.find("Guides").id, EvalContext(0.01, Resolver()))
        self.assertTrue(res.strands)

    def test_density_map(self):
        g = Graph()
        a = g.add_node("import_geo", params={"geometry": "plane"})
        b = g.add_node("scatter", params={"count": 1000, "density_map": "file1"})
        g.connect(a.id, b.id, 0)
        res = g.evaluate(b.id, EvalContext(1.0, Resolver()))
        self.assertTrue(all(s.uv[0] > 0.5 for s in res.strands))
        self.assertTrue(300 < len(res.strands) < 700)

    def test_roots_stable_under_deformation(self):
        rest = grid_plane()
        # stretch one side of the plane: areas change, rest pose does not
        bent = Surface([(x * (2.0 if x > 0 else 1.0), y + 0.3 * x * x, z) for x, y, z in rest.points],
                       rest.triangles, tri_uvs=rest.tri_uvs, rest_points=rest.points)
        g = Graph()
        a = g.add_node("import_geo", params={"geometry": "plane"})
        b = g.add_node("scatter", params={"count": 300})
        g.connect(a.id, b.id, 0)
        r1, r2 = Resolver(), Resolver()
        r2.surface = bent
        s1 = g.evaluate(b.id, EvalContext(1.0, r1)).strands
        s2 = g.evaluate(b.id, EvalContext(1.0, r2)).strands
        self.assertEqual([s.tri for s in s1], [s.tri for s in s2])
        self.assertEqual([s.bary for s in s1], [s.bary for s in s2])

    def test_empty_guides_pass_through(self):
        class Empty(Resolver):
            def get_guides(self, name, samples):
                return []
        g = default_graph("plane", "groom")
        res = g.evaluate(g.find("Guides").id, EvalContext(0.01, Empty()))
        self.assertEqual(len(res.strands), 200)

    def test_cycle_rejected(self):
        g = Graph()
        a = g.add_node("noise"); b = g.add_node("curl")
        g.connect(a.id, b.id, 0)
        with self.assertRaises(ValueError):
            g.connect(b.id, a.id, 0)

    def test_json_roundtrip(self):
        g = default_graph("plane", "guides")
        g2 = Graph.from_json(g.to_json())
        self.assertEqual(g.to_dict(), g2.to_dict())

    def test_ass_export(self):
        g = default_graph("plane")
        res = g.evaluate(g.output_id(), EvalContext(0.01, Resolver()))
        path = os.path.join(tempfile.mkdtemp(), "fur.ass")
        n = write_ass(path, res.strands, subdiv=1)
        self.assertEqual(n, len(res.strands))
        with open(path) as fh:
            text = fh.read()
        self.assertIn('basis "linear"', text)
        # radius count == point count
        npts = sum(int(x) for x in text.split("num_points")[1].split("points")[0].split()[3:])
        nrad = int(text.split(" radius ")[1].split()[0])
        self.assertEqual(npts, nrad)


if __name__ == "__main__":
    unittest.main()
