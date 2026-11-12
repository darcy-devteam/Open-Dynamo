"""The diagram a person draws, and how it becomes equations.

A model is a graph. **Nodes** carry the maths:

``stock``      accumulates what flows in and out; has an ``initial`` value
``flow``       a rate, drawn as a valve; has an ``equation``
``variable``   any other quantity; has an ``equation``
``parameter``  a constant with a ``value`` (what sliders and experiments change)
``table``      a piecewise-linear curve through ``points``, read at ``input`` (default: time)

Anything else (notes, frames, clouds, ...) is drawing only.

**Links** may join any node to any node:

``flow``       moves quantity out of its source and into its target, where those are stocks.
               The rate is the link's own ``rate`` formula if it has one, otherwise the value
               of whichever end is not a stock (normally a flow node).
``influence``  says "the source is an input of the target". Equations refer to other nodes
               by name, so influence links document the structure rather than define it.

Any other link kind is annotation.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .expr import RESERVED, CDialect, ExprError, compile_expr, names_in, parse
from .kernel import CompiledSystem, Reducer, Run, Sim, System

MATH_KINDS = ("stock", "flow", "variable", "parameter", "table")
MAX_NODES = 5000


class SimSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: float = 0.0
    stop: float = 100.0
    dt: float = 0.25
    method: str = "rk4"
    timeUnit: str = ""

    def to_sim(self, save_every: int = 1) -> Sim:
        return Sim(self.start, self.stop, self.dt, self.method, save_every)


class Node(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    kind: str
    name: str = ""
    equation: str = ""
    initial: str | float = "0"
    value: float = 0.0
    min: float | None = None
    max: float | None = None
    input: str = "time"
    points: list[tuple[float, float]] = Field(default_factory=list)
    nonNegative: bool = False
    units: str = ""


class Link(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    kind: str = "influence"
    source: str
    target: str
    rate: str = ""


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = "Untitled model"
    description: str = ""
    sim: SimSpec = Field(default_factory=SimSpec)
    nodes: list[Node] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)


class ModelError(ValueError):
    """The model cannot be simulated; ``diagnostics`` says why, node by node."""

    def __init__(self, diagnostics: list[dict]):
        self.diagnostics = diagnostics
        errors = [d["message"] for d in diagnostics if d["level"] == "error"]
        super().__init__("; ".join(errors[:5]) or "invalid model")


def ident(name: str) -> str:
    """The identifier equations use for a node called ``name`` ("Birth rate" -> "Birth_rate")."""
    out = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip()).strip("_")
    return ("_" + out) if out[:1].isdigit() else out


def _number(x) -> float | None:
    try:
        v = float(str(x).strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


class _Probe:
    """Stand-in compile context used to check one expression on its own."""

    def __init__(self, stateful):
        self.stateful = stateful

    def slot(self, name):
        return 0

    def hist(self, name):
        if name not in self.stateful:
            raise ExprError(f"delay() needs a stock, flow or variable, and '{name}' is a parameter")
        return 0

    def table(self, values):
        self.n = len(values)
        return "TB0"

    def table_len(self, name):
        return self.n


class _Expander(ast.NodeTransformer):
    """Rewrites smooth(), delay1(), delay3() and delay(<expression>, ...) into hidden stocks and variables."""

    def __init__(self, owner: str, taken: set[str]):
        self.owner, self.taken = owner, taken
        self.stocks: list[tuple[str, str, str]] = []      # name, initial, net flow
        self.auxes: list[tuple[str, str]] = []

    def _new(self, tag: str) -> str:
        k = 1
        while f"{self.owner}__{tag}{k}" in self.taken:
            k += 1
        name = f"{self.owner}__{tag}{k}"
        self.taken.add(name)
        return name

    def visit_Call(self, node: ast.Call):
        self.generic_visit(node)
        fn = node.func.id if isinstance(node.func, ast.Name) else None
        if fn == "delay" and node.args and not isinstance(node.args[0], ast.Name):
            name = self._new("in")
            self.auxes.append((name, ast.unparse(node.args[0])))
            node.args[0] = ast.Name(name, ast.Load())
            return node
        if fn not in ("smooth", "delay1", "delay3"):
            return node
        if node.keywords or not 2 <= len(node.args) <= 3:
            raise ExprError(f"{fn}(input, time[, initial]) takes 2 or 3 arguments")
        x, tau = ast.unparse(node.args[0]), ast.unparse(node.args[1])
        start = ast.unparse(node.args[2]) if len(node.args) == 3 else x
        if fn == "smooth":
            name = self._new("smooth")
            self.stocks.append((name, start, f"(({x}) - {name}) / ({tau})"))
            return ast.Name(name, ast.Load())
        stages = 3 if fn == "delay3" else 1
        inflow = f"({x})"
        for _ in range(stages):
            name = self._new("delay")
            out = f"({name} / (({tau}) / {stages}))"
            self.stocks.append((name, f"({start}) * ({tau}) / {stages}", f"{inflow} - {out}"))
            inflow = out
        return parse(inflow)


@dataclass
class Built:
    spec: ModelSpec
    diagnostics: list[dict]
    compiled: CompiledSystem | None = None
    names: dict[str, str] = field(default_factory=dict)        # node id -> identifier
    params: list[dict] = field(default_factory=list)
    variables: list[dict] = field(default_factory=list)
    uses: dict[str, list[str]] = field(default_factory=dict)   # node id -> ids of the nodes its formulas mention

    @property
    def ok(self) -> bool:
        return self.compiled is not None


def build(spec: ModelSpec | dict) -> Built:
    """Turn a diagram into a compiled system, collecting every problem rather than stopping at the first."""
    if not isinstance(spec, ModelSpec):
        spec = ModelSpec.model_validate(spec)
    out = Built(spec, [])
    diag = out.diagnostics

    def err(message, node=None, link=None, level="error"):
        diag.append({"level": level, "message": message, "node": node, "link": link})

    nodes = [n for n in spec.nodes if n.kind in MATH_KINDS]
    if len(nodes) > MAX_NODES:
        err(f"the model has {len(nodes)} nodes; the limit is {MAX_NODES}")
        return out
    by_id = {n.id: n for n in spec.nodes}
    owner_of: dict[str, str] = {}                         # identifier -> node id
    for n in nodes:
        name = ident(n.name)
        if not name:
            err("give this node a name", n.id)
        elif name in RESERVED:
            err(f"'{name}' is a built-in name; choose another", n.id)
        elif name in owner_of:
            err(f"two nodes are called '{name}'", n.id)
            err(f"two nodes are called '{name}'", owner_of[name])
        else:
            owner_of[name] = n.id
            out.names[n.id] = name
    taken = set(owner_of)
    stateful = {out.names[n.id] for n in nodes if n.kind != "parameter" and n.id in out.names}
    sys = System()
    exprs: list[tuple[str, str, str | None, str | None]] = []     # (label, text, node id, link id) to validate
    inflow: dict[str, list[str]] = {out.names[n.id]: [] for n in nodes if n.kind == "stock" and n.id in out.names}
    hidden_stocks: list[tuple[str, str, str]] = []
    hidden_auxes: list[tuple[str, str]] = []

    def expand(text: str, owner: str, node=None, link=None) -> str:
        """Macro-expand one formula; returns text the kernel understands ('0' if it is broken)."""
        try:
            ex = _Expander(owner, taken)
            new = ast.unparse(ex.visit(parse(text)))
        except ExprError as e:
            err(str(e), node, link)
            return "0"
        hidden_stocks.extend(ex.stocks)
        hidden_auxes.extend(ex.auxes)
        stateful.update(n for n, *_ in ex.stocks)
        stateful.update(n for n, _ in ex.auxes)
        for label, t in [(n, t) for n, _, f in ex.stocks for t in (_, f)] + list(ex.auxes):
            exprs.append((label, t, node, link))
        exprs.append((owner, new, node, link))
        return new

    auxes: list[tuple[str, str]] = []
    stock_init: dict[str, str] = {}
    for n in nodes:
        name = out.names.get(n.id)
        if name is None:
            continue
        label = n.name.strip()
        if n.kind == "parameter":
            sys.param(name, n.value)
            out.params.append(dict(name=name, node=n.id, role="parameter", label=label, default=n.value, min=n.min, max=n.max))
        elif n.kind == "stock":
            value = _number(n.initial)
            if value is not None:
                sys.param(f"{name}__init", value)
                taken.add(f"{name}__init")
                out.params.append(dict(name=f"{name}__init", node=n.id, role="initial", label=f"{label} (initial)",
                                       default=value, min=None, max=None))
                stock_init[name] = f"{name}__init"
            else:
                stock_init[name] = expand(str(n.initial), name, n.id)
        elif n.kind == "table":
            pts = sorted(n.points)
            xs = [p[0] for p in pts]
            if len(pts) < 2 or any(b <= a for a, b in zip(xs, xs[1:])):
                err("a table needs at least two points with different x values", n.id)
                auxes.append((name, "0"))
            else:
                inp = expand(n.input or "time", name, n.id)
                auxes.append((name, f"lookup({inp}, {xs!r}, {[p[1] for p in pts]!r})"))
        else:
            if not str(n.equation).strip():
                err("this node has no equation yet", n.id)
                auxes.append((name, "0"))
            else:
                auxes.append((name, expand(n.equation, name, n.id)))

    # flow links: quantity leaves the source and enters the target, where those are stocks
    touched: set[str] = set()
    for k, l in enumerate(spec.links):
        if l.kind != "flow":
            continue
        a, b = by_id.get(l.source), by_id.get(l.target)
        if a is None or b is None:
            continue
        sa = out.names.get(a.id) if a.kind == "stock" else None
        sb = out.names.get(b.id) if b.kind == "stock" else None
        touched.update((a.id, b.id))
        if l.rate.strip():
            rate = f"flow__{k + 1}"
            taken.add(rate)
            auxes.append((rate, expand(l.rate, rate, None, l.id)))
            out.variables.append(dict(name=rate, node=None, link=l.id, kind="flow", label=f"{a.name} → {b.name}"))
        elif sa and sb:
            err("a flow drawn straight from one stock to another needs a rate: type one on the link, "
                "or put a flow node between them", link=l.id)
            continue
        elif sa or sb:
            other = b if sa else a
            rate = out.names.get(other.id) if other.kind in MATH_KINDS else None
        else:
            rate = None
            if a.kind in MATH_KINDS and b.kind in MATH_KINDS:
                err("this flow does not touch a stock, so it has no effect", link=l.id, level="warning")
        if rate is None:
            continue                                   # e.g. cloud -> flow node: the open end of a pipe
        if sa:
            inflow[sa].append(f"- {rate}")
        if sb:
            inflow[sb].append(f"+ {rate}")
    for n in nodes:
        name = out.names.get(n.id)
        if n.kind == "stock" and name and not inflow[name]:
            err("nothing flows into or out of this stock, so it never changes", n.id, level="warning")
        if n.kind == "flow" and n.id not in touched:
            err("this flow is not connected to a stock", n.id, level="warning")

    for name, text in auxes + hidden_auxes:
        sys.aux(name, text)
    for n in nodes:
        name = out.names.get(n.id)
        if n.kind == "stock" and name:
            sys.stock(name, stock_init[name], "0 " + " ".join(inflow[name]), lo=0.0 if n.nonNegative else None)
    for name, init, flow in hidden_stocks:
        sys.stock(name, init, flow)

    # check every formula on its own so each problem lands on the node that has it
    seen: set[tuple] = set()
    for label, text, node, link in exprs:
        try:
            compile_expr(text, CDialect(_Probe(stateful)), sys.__contains__)
        except ExprError as e:
            if (str(e), node, link) not in seen:
                seen.add((str(e), node, link))
                err(str(e), node, link)
    known = set(owner_of)
    for n in nodes:
        texts = [str(n.equation), str(n.initial), n.input if n.kind == "table" else ""]
        used = set().union(*(names_in(t) for t in texts if t and _number(t) is None)) & known
        out.uses[n.id] = sorted(owner_of[u] for u in used if owner_of[u] != n.id)
    for l in spec.links:
        if l.kind == "influence" and l.target in out.uses and l.source in out.names and l.source not in out.uses[l.target]:
            err(f"'{out.names[l.source]}' is linked to '{out.names.get(l.target, '?')}' but its equation does not use it",
                link=l.id, level="warning")
    if any(d["level"] == "error" for d in diag):
        return out
    try:
        out.compiled = sys.compile()
    except ExprError as e:
        involved = [owner_of[x] for x in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(e)) if x in owner_of]
        for node in dict.fromkeys(involved) or [None]:
            err(str(e), node)
        return out
    hidden = {n for n, *_ in hidden_stocks} | {n for n, _ in hidden_auxes}
    for n in nodes:
        if n.kind != "parameter":
            out.variables.append(dict(name=out.names[n.id], node=n.id, link=None, kind=n.kind, label=n.name.strip(),
                                      units=n.units))
    for v in out.variables:
        v["slot"] = out.compiled.slots[v["name"]]
    for p in out.params:
        p["slot"] = out.compiled.slots[p["name"]]
    out.variables = [v for v in out.variables if v["name"] not in hidden]
    return out


class Model:
    """A compiled model: simulate it, or hand it to :mod:`opendynamo.analysis`."""

    def __init__(self, spec: ModelSpec | dict):
        self.built = build(spec)
        if not self.built.ok:
            raise ModelError(self.built.diagnostics)
        self.spec, self.compiled = self.built.spec, self.built.compiled
        self.sim = self.spec.sim.to_sim()
        self.variables = [v["name"] for v in self.built.variables]
        self.parameters = {p["name"]: p for p in self.built.params}

    @property
    def size(self) -> int:
        """Number of equations (what a step costs)."""
        return self.compiled.NA + 2 * self.compiled.NS

    def with_sim(self, **changes) -> Sim:
        s = self.sim
        return Sim(**{**dict(start=s.start, stop=s.stop, dt=s.dt, method=s.method, save_every=s.save_every), **changes})

    def simulate(self, overrides: dict[str, Any] | None = None, record=None, sim: Sim | None = None,
                 backend: str = "native", **kw) -> Run:
        return self.compiled.run(sim or self.sim, overrides, record=self.variables if record is None else record,
                                 backend=backend, **kw)

    def target(self, times, values, sim: Sim | None = None) -> np.ndarray:
        """Observed data resampled onto the step grid for an ``sse`` reducer (NaN where there is none)."""
        sim = sim or self.sim
        out = np.full(sim.steps + 1, np.nan)
        idx = np.rint((np.asarray(times, dtype=float) - sim.start) / sim.dt).astype(int)
        ok = (idx >= 0) & (idx <= sim.steps) & np.isfinite(np.asarray(values, dtype=float))
        out[idx[ok]] = np.asarray(values, dtype=float)[ok]
        return out


__all__ = ["Model", "ModelSpec", "ModelError", "Reducer", "Sim", "build", "ident"]
