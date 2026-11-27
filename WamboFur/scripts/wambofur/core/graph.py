"""Procedural node graph: storage, connections, evaluation, JSON I/O."""
import json
import time
import copy

from .nodes import NODE_TYPES
from .data import EvalError

GRAPH_VERSION = 1


class GraphNode(object):
    def __init__(self, nid, type_name, name, params=None, pos=(0.0, 0.0), bypass=False):
        if type_name not in NODE_TYPES:
            raise ValueError("unknown node type: %s" % type_name)
        self.id = nid
        self.type_name = type_name
        self.name = name
        op = NODE_TYPES[type_name]
        self.params = op.defaults()
        if params:
            for k, v in params.items():
                if k in self.params:
                    self.params[k] = tuple(v) if isinstance(v, list) else v
        self.pos = tuple(pos)
        self.bypass = bypass

    @property
    def op(self):
        return NODE_TYPES[self.type_name]

    def to_dict(self):
        return {"id": self.id, "type": self.type_name, "name": self.name,
                "params": dict((k, list(v) if isinstance(v, tuple) else v) for k, v in self.params.items()),
                "pos": list(self.pos), "bypass": self.bypass}


class EvalContext(object):
    """Evaluation environment.

    density   : multiplier applied to Scatter counts (preview vs render)
    resolver  : object providing get_surface(name), get_guides(name, samples)
                and sample_map(texture, uvs) -> [float]
    """

    def __init__(self, density=1.0, resolver=None):
        self.density = density
        self.resolver = resolver
        self.stats = {}     # node id -> (seconds, strand count)
        self.errors = {}    # node id -> message

    def get_surface(self, name):
        if self.resolver is None:
            raise EvalError("no scene resolver")
        return self.resolver.get_surface(name)

    def get_guides(self, name, samples):
        if self.resolver is None:
            raise EvalError("no scene resolver")
        return self.resolver.get_guides(name, samples)

    def sample_map(self, texture, uvs):
        if self.resolver is None or not hasattr(self.resolver, "sample_map"):
            return [1.0] * len(uvs)
        return self.resolver.sample_map(texture, uvs)


class Graph(object):
    def __init__(self):
        self.nodes = {}           # id -> GraphNode
        self.connections = []     # [(src_id, dst_id, port_index)]
        self.display_id = None    # node shown in the viewport
        self._next = 1

    # ------------------------------------------------------------ editing
    def unique_name(self, base):
        names = set(n.name for n in self.nodes.values())
        if base not in names:
            return base
        i = 2
        while "%s%d" % (base, i) in names:
            i += 1
        return "%s%d" % (base, i)

    def add_node(self, type_name, name=None, pos=(0.0, 0.0), params=None):
        nid = "n%d" % self._next
        self._next += 1
        if not name:
            name = "".join(ch for ch in NODE_TYPES[type_name].label.title() if ch.isalnum())
        name = self.unique_name(name)
        node = GraphNode(nid, type_name, name, params, pos)
        self.nodes[nid] = node
        return node

    def remove_node(self, nid):
        self.nodes.pop(nid, None)
        self.connections = [c for c in self.connections if c[0] != nid and c[1] != nid]
        if self.display_id == nid:
            self.display_id = self.output_id()

    def rename(self, nid, name):
        if self.nodes[nid].name != name:
            self.nodes[nid].name = self.unique_name(name)
        return self.nodes[nid].name

    def upstream(self, nid, seen=None):
        seen = seen if seen is not None else set()
        for s, d, _ in self.connections:
            if d == nid and s not in seen:
                seen.add(s)
                self.upstream(s, seen)
        return seen

    def connect(self, src, dst, port):
        if src == dst or dst in self.upstream(src):
            raise ValueError("connection would create a cycle")
        n_ports = len(self.nodes[dst].op.inputs)
        if port < 0 or port >= n_ports:
            raise ValueError("invalid port")
        self.disconnect(dst, port)
        self.connections.append((src, dst, port))

    def downstream(self, nid):
        out = set()
        stack = [nid]
        while stack:
            cur = stack.pop()
            for s, d, _ in self.connections:
                if s == cur and d not in out:
                    out.add(d)
                    stack.append(d)
        return out

    def disconnect(self, dst, port):
        self.connections = [c for c in self.connections if not (c[1] == dst and c[2] == port)]

    def input_of(self, dst, port):
        for s, d, p in self.connections:
            if d == dst and p == port:
                return s
        return None

    def output_id(self):
        for nid, n in self.nodes.items():
            if n.type_name == "output":
                return nid
        return None

    def find(self, name):
        for n in self.nodes.values():
            if n.name == name:
                return n
        return None

    # ---------------------------------------------------------- evaluation
    def evaluate(self, nid, ctx, _memo=None):
        """Evaluate node nid. Errors are recorded in ctx.errors and raised."""
        memo = {} if _memo is None else _memo
        if nid in memo:
            return memo[nid]
        node = self.nodes.get(nid)
        if node is None:
            raise EvalError("node %s does not exist" % nid)
        inputs = []
        for port in range(len(node.op.inputs)):
            src = self.input_of(nid, port)
            inputs.append(self.evaluate(src, ctx, memo) if src else None)
        t0 = time.time()
        try:
            if node.bypass and node.op.inputs:
                result = inputs[0]
                if result is None:
                    raise EvalError("bypassed node has no input")
            else:
                result = node.op.compute(node.params, inputs, ctx)
        except EvalError as e:
            ctx.errors[nid] = str(e)
            raise EvalError("%s: %s" % (node.name, e))
        ctx.stats[nid] = (time.time() - t0, len(result.strands) if result else 0)
        memo[nid] = result
        return result

    # ------------------------------------------------------------- I/O
    def to_dict(self):
        return {"version": GRAPH_VERSION,
                "nodes": [n.to_dict() for n in self.nodes.values()],
                "connections": [list(c) for c in self.connections],
                "display": self.display_id,
                "next": self._next}

    def to_json(self):
        return json.dumps(self.to_dict(), indent=1)

    @classmethod
    def from_dict(cls, d):
        g = cls()
        for nd in d.get("nodes", []):
            if nd["type"] not in NODE_TYPES:
                continue
            n = GraphNode(nd["id"], nd["type"], nd["name"], nd.get("params"),
                          nd.get("pos", (0, 0)), nd.get("bypass", False))
            g.nodes[n.id] = n
        for s, dst, p in d.get("connections", []):
            if s in g.nodes and dst in g.nodes:
                g.connections.append((s, dst, int(p)))
        g.display_id = d.get("display") if d.get("display") in g.nodes else g.output_id()
        nums = [int(k[1:]) for k in g.nodes if k[1:].isdigit()]
        g._next = max([d.get("next", 1)] + [x + 1 for x in nums])
        return g

    @classmethod
    def from_json(cls, text):
        if not text:
            return cls()
        return cls.from_dict(json.loads(text))

    def clone(self):
        return Graph.from_dict(copy.deepcopy(self.to_dict()))


def default_graph(mesh="", guides=""):
    """Starter groom: Import -> Scatter -> Grow -> [Guides] -> Clump -> Noise -> Width -> Output."""
    g = Graph()
    x, dx = 0.0, 200.0
    imp = g.add_node("import_geo", None, (x, 0), {"geometry": mesh})
    sc = g.add_node("scatter", None, (x + dx, 0))
    gr = g.add_node("grow", None, (x + 2 * dx, 0))
    g.connect(imp.id, sc.id, 0)
    g.connect(sc.id, gr.id, 0)
    last = gr
    col = 3
    if guides:
        ig = g.add_node("import_guides", None, (x + 2 * dx, 140), {"curves": guides})
        gd = g.add_node("guides", None, (x + col * dx, 0))
        g.connect(last.id, gd.id, 0)
        g.connect(ig.id, gd.id, 1)
        last = gd
        col += 1
    for t in ("clump", "noise", "width", "output"):
        n = g.add_node(t, None, (x + col * dx, 0))
        g.connect(last.id, n.id, 0)
        last = n
        col += 1
    g.display_id = last.id
    return g
