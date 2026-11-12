"""Continuous-time dynamical system and its compiled backends.

A :class:`System` is a set of named variables of three kinds:

* **params** - constants a run can override (what sliders, sweeps, Monte Carlo and
  the optimiser change);
* **stocks** - state that integrates a net flow: ``d(stock)/dt = flow``;
* **auxes**  - algebraic quantities recomputed at every instant from params, stocks
  and other auxes.

Auxes are sorted by dependency; a cycle that does not pass through a stock or a
``delay()`` is an algebraic loop and is rejected at compile time.

One set of equations runs on three interchangeable backends:

``numpy``   reference implementation - each variable is a vector over scenarios;
``native``  generated C compiled to a shared library and run across all cores
            (the server uses this for sweeps, Monte Carlo and optimisation);
``wasm``    the same C compiled to WebAssembly, which the browser runs itself.

Integration is fixed-step Euler or classical Runge-Kutta (RK4). Trajectories are
stored only for the variables you ask to ``record``; *reducers* collapse a
trajectory to one number per scenario inside the kernel, so a large batch returns
a small array.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import subprocess
import sys
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from . import functions as F
from .expr import CDialect, ExprError, VectorDialect, compile_expr
from .expr import Compiled as CExpr

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
KINDS = {"last": 0, "first": 1, "min": 2, "max": 3, "mean": 4, "integral": 5, "sse": 6, "sum": 7}
CHUNK = 200            # statements per generated C function (keeps compile time linear)
_BUILD_ID = "od1"      # bump to invalidate cached kernels
_CFG = 16              # length of the run-configuration block


@dataclass
class Param:
    name: str
    value: float
    meta: dict = field(default_factory=dict)


@dataclass
class Aux:
    name: str
    expr: str
    meta: dict = field(default_factory=dict)


@dataclass
class Stock:
    name: str
    init: str | float
    flow: str
    lo: float | None = None
    hi: float | None = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Sim:
    """When and how to integrate."""
    start: float = 0.0
    stop: float = 10.0
    dt: float = 0.1
    method: str = "rk4"            # euler | rk4
    save_every: int = 1            # keep every n-th step of recorded trajectories

    @property
    def steps(self) -> int:
        return max(1, int(round((self.stop - self.start) / self.dt)))

    @property
    def n_out(self) -> int:
        return self.steps // max(1, self.save_every) + 1

    def times(self) -> np.ndarray:
        return self.start + np.arange(self.n_out) * (self.dt * max(1, self.save_every))

    def check(self) -> None:
        if not (np.isfinite([self.start, self.stop, self.dt]).all() and self.dt > 0 and self.stop > self.start):
            raise ExprError("the time settings need stop > start and a positive time step")
        if self.method not in ("euler", "rk4"):
            raise ExprError(f"unknown integration method {self.method!r}")


@dataclass(frozen=True)
class Reducer:
    """Collapse one variable's trajectory to a single number per scenario."""
    var: str
    kind: str = "last"             # last | first | min | max | mean | integral | sse | sum
    i0: int = 0                    # first step index
    i1: int = -1                   # last step index, inclusive; -1 = final step
    target: tuple | None = None    # sse: one value per step (steps + 1), NaN where there is no data


class System:
    def __init__(self):
        self.params: dict[str, Param] = {}
        self.auxes: dict[str, Aux] = {}
        self.stocks: dict[str, Stock] = {}

    def _check(self, name: str) -> None:
        if not NAME_RE.match(name):
            raise ExprError(f"invalid variable name {name!r}")
        if name in self:
            raise ExprError(f"variable {name!r} is defined twice")

    def __contains__(self, name: str) -> bool:
        return name in self.params or name in self.auxes or name in self.stocks

    def param(self, name: str, value: float, **meta) -> str:
        self._check(name)
        self.params[name] = Param(name, float(value), meta)
        return name

    def aux(self, name: str, expr: str | float, **meta) -> str:
        self._check(name)
        self.auxes[name] = Aux(name, str(expr), meta)
        return name

    def stock(self, name: str, init: str | float, flow: str | float, lo: float | None = None,
              hi: float | None = None, **meta) -> str:
        self._check(name)
        self.stocks[name] = Stock(name, init, str(flow), lo, hi, meta)
        return name

    def compile(self) -> "CompiledSystem":
        return CompiledSystem(self)


@dataclass
class Run:
    """Result of simulating a batch of B scenarios."""
    t: np.ndarray                        # (n_out,)
    B: int
    series: dict[str, np.ndarray]        # recorded variables, each (n_out, B)
    metrics: np.ndarray                  # (number of reducers, B)
    backend: str = "native"

    def __getitem__(self, name: str) -> np.ndarray:
        return self.series[name]


def reduce_series(x: np.ndarray, r: Reducer, sim: Sim) -> np.ndarray:
    """numpy implementation of a reducer on a (steps + 1, B) array (reference backend)."""
    n = sim.steps
    i1 = n if r.i1 < 0 else min(r.i1, n)
    w = x[r.i0:i1 + 1]
    if r.kind == "last":
        return x[i1]
    if r.kind == "first":
        return x[r.i0]
    if r.kind == "min":
        return w.min(0)
    if r.kind == "max":
        return w.max(0)
    if r.kind == "mean":
        return w.mean(0)
    if r.kind == "sum":
        return w.sum(0)
    if r.kind == "integral":
        wt = np.full(len(w), sim.dt)
        wt[0] *= 0.5
        wt[-1] *= 0.5
        return (w * wt[:, None]).sum(0) if len(w) > 1 else w[0] * 0.0
    if r.kind == "sse":
        tgt = np.asarray(r.target, dtype=float)[r.i0:i1 + 1]
        d = np.where(np.isnan(tgt)[:, None], 0.0, w - np.nan_to_num(tgt)[:, None])
        return (d * d).sum(0)
    raise ValueError(f"unknown reducer kind {r.kind!r}")


class CompiledSystem:
    def __init__(self, system: System):
        s = self.system = system
        self.params = list(s.params)
        self.NP, self.NA, self.NS = len(s.params), len(s.auxes), len(s.stocks)
        self.F0 = self.NP + self.NA
        self.S0 = self.F0 + self.NS
        self.NV = self.S0 + self.NS
        self.slots: dict[str, int] = {n: i for i, n in enumerate(list(s.params) + list(s.auxes))}
        for i, n in enumerate(s.stocks):
            self.slots[n] = self.S0 + i
        self.hist_of: dict[str, int] = {}         # variable -> history row
        self.tables: list[list[float]] = []
        self.site0: dict[str, int] = {}           # first random-draw site of each expression

        sites = 0
        self.aux_c, self.flow_c, self.init_c = {}, {}, {}
        for group, items in ((self.aux_c, [(n, a.expr) for n, a in s.auxes.items()]),
                             (self.flow_c, [(n, st.flow) for n, st in s.stocks.items()]),
                             (self.init_c, [(n, st.init) for n, st in s.stocks.items()])):
            for n, text in items:
                key = f"{n}/{'aux' if group is self.aux_c else 'flow' if group is self.flow_c else 'init'}"
                self.site0[key] = sites
                group[n] = self._compile(key, text)
                sites += group[n].sites
        self.NH = len(self.hist_of)
        self.order = self._toposort()
        self.init_plan = self._init_plan()
        self.defaults = np.array([p.value for p in s.params.values()], dtype=float)
        self._vec_fns: dict[frozenset, Any] = {}
        self._source: str | None = None

    # -- expression handling ---------------------------------------------------
    def slot(self, name: str) -> int:
        return self.slots[name]

    def hist(self, name: str) -> int:
        if name not in self.system.auxes and name not in self.system.stocks:
            raise ExprError(f"delay() needs a stock, flow or variable, and '{name}' is a parameter")
        return self.hist_of.setdefault(name, len(self.hist_of))

    def table(self, values: list[float]) -> str:
        self.tables.append(values)
        return f"TB{len(self.tables) - 1}"

    def table_len(self, name: str) -> int:
        return len(self.tables[int(name[2:])])

    def _compile(self, key: str, text, dialect=None) -> CExpr:
        return compile_expr(text, dialect or CDialect(self), self.system.__contains__, self.site0[key])

    # -- dependency analysis ---------------------------------------------------
    def _toposort(self) -> list[str]:
        auxes = self.system.auxes
        deps = {n: [d for d in sorted(c.deps) if d in auxes] for n, c in self.aux_c.items()}
        order: list[str] = []
        state: dict[str, int] = {}
        for root in auxes:
            if root in state:
                continue
            stack = [(root, iter(deps[root]))]
            state[root] = 1
            while stack:
                n, it = stack[-1]
                for d in it:
                    if state.get(d) == 1:
                        path = [x for x, _ in stack]
                        path = path[path.index(d):] + [d]
                        raise ExprError("circular definition with no stock in the loop: " + " -> ".join(path))
                    if d not in state:
                        state[d] = 1
                        stack.append((d, iter(deps[d])))
                        break
                else:
                    state[n] = 2
                    order.append(n)
                    stack.pop()
        return order

    def _init_plan(self) -> list[tuple[str, str]]:
        """Order in which to evaluate initial values: stock initials may use auxes, which may use other stocks."""
        s = self.system
        plan: list[tuple[str, str]] = []
        state: dict[tuple[str, str], int] = {}

        def needs(node):
            kind, n = node
            for d in sorted((self.init_c if kind == "init" else self.aux_c)[n].deps):
                if d in s.stocks:
                    yield ("init", d)
                elif d in s.auxes:
                    yield ("aux", d)

        def visit(node, trail):
            if state.get(node) == 2:
                return
            if state.get(node) == 1:
                loop = [n for _, n in trail[trail.index(node):]] + [node[1]]
                raise ExprError("initial values depend on each other in a circle: " + " -> ".join(loop))
            state[node] = 1
            for d in needs(node):
                visit(d, trail + [node])
            state[node] = 2
            plan.append(node)

        for n in s.stocks:
            visit(("init", n), [])
        return plan

    def dependencies(self, names: Iterable[str]) -> set[str]:
        """Everything the given variables depend on (transitively, through stocks' flows)."""
        seen: set[str] = set()
        todo = list(names)
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            if n in self.aux_c:
                todo.extend(self.aux_c[n].deps)
            elif n in self.flow_c:
                todo.extend(self.flow_c[n].deps | self.init_c[n].deps)
        return seen

    # -- inputs ------------------------------------------------------------------
    def param_matrix(self, overrides: dict[str, Any] | None, batch: int | None = None) -> np.ndarray:
        """(B, NP) parameter values: defaults with ``overrides`` (scalars or arrays of length B) applied."""
        overrides = overrides or {}
        unknown = [k for k in overrides if k not in self.system.params]
        if unknown:
            raise KeyError(f"cannot override unknown parameter(s): {unknown[:5]}")
        B = batch or max([np.shape(v)[0] for v in overrides.values() if np.ndim(v)], default=1)
        P = np.tile(self.defaults, (B, 1))
        for k, v in overrides.items():
            P[:, self.slots[k]] = v
        return P

    # -- public entry point ----------------------------------------------------
    def run(self, sim: Sim, overrides: dict[str, Any] | None = None, batch: int | None = None,
            record: Iterable[str] | str = (), reducers: Sequence[Reducer] = (), backend: str = "native",
            seed: int = 0, seed_mod: int = 0, P: np.ndarray | None = None) -> Run:
        """Simulate a batch of scenarios.

        ``overrides`` maps param names to a scalar (all scenarios) or an array of shape (B,);
        alternatively pass the full (B, NP) matrix as ``P``. ``record`` lists variables whose
        trajectory to return ("all" for every aux and stock). Scenario b draws its random
        numbers from ``seed + b`` (``seed + b % seed_mod`` when ``seed_mod`` is set, which
        gives different candidates the same noise).
        """
        sim.check()
        s = self.system
        if P is None:
            P = self.param_matrix(overrides, batch)
        P = np.ascontiguousarray(P, dtype=np.float64)
        rec = (list(s.auxes) + list(s.stocks)) if isinstance(record, str) and record == "all" else list(dict.fromkeys(record))
        reducers = list(reducers)
        for n in rec + [r.var for r in reducers]:
            if n not in self.slots:
                raise KeyError(f"{n!r} is not a variable of this model")
        for r in reducers:
            if r.kind not in KINDS:
                raise ValueError(f"unknown reducer kind {r.kind!r}")
            if r.kind == "sse" and (r.target is None or len(r.target) != sim.steps + 1):
                raise ValueError("an sse reducer needs a target with one value per step")
        if backend == "numpy":
            return self._run_numpy(sim, P, rec, reducers, seed, seed_mod)
        if backend == "native":
            return self._run_native(sim, P, rec, reducers, seed, seed_mod)
        raise ValueError(f"unknown backend {backend!r}")

    # ======================= numpy reference backend =======================
    def _vec_build(self, want: frozenset):
        s = self.system
        loc = {n: f"x{i}" for n, i in self.slots.items()}
        ctx = _VecCtx(loc)
        dia = VectorDialect(ctx)
        comp = lambda key, text: self._compile(key, text, dia).src   # noqa: E731
        keep = [n for n in self.slots if n in want or n in self.hist_of]
        stocks = list(s.stocks)
        L = ["def _run(N, B, P, F, np, T0, DT, RK4, STRIDE, SEED, LO, HI):",
             "    NOUT = N // STRIDE + 1; tm = T0; step = 0; hv = 0"]
        for i, n in enumerate(self.params):
            L.append(f"    {loc[n]} = P[:, {i}]")
        for n in self.hist_of:
            L.append(f"    h_{loc[n]} = np.zeros((N + 1, B))")
        for n in want:
            L.append(f"    o_{loc[n]} = np.zeros((N + 1, B))")
        for kind, n in self.init_plan:
            text = s.stocks[n].init if kind == "init" else s.auxes[n].expr
            L.append(f"    {loc[n]} = ({comp(f'{n}/{kind}', text)}) + np.zeros(B)")
        L.append("    def ev(S, tm, step, hv):")
        if stocks:
            L.append("        " + ", ".join(loc[n] for n in stocks) + ", = S")
        for n in self.order:
            L.append(f"        {loc[n]} = {comp(n + '/aux', s.auxes[n].expr)}")
        flows = ", ".join(f"({comp(n + '/flow', s.stocks[n].flow)}) + np.zeros(B)" for n in stocks)
        L.append(f"        return [{flows}], [" + ", ".join(f"{loc[n]} + np.zeros(B)" for n in keep) + "]")
        L.append("    S = [" + ", ".join(loc[n] for n in stocks) + "]")
        L.append("    for i in range(N + 1):")
        L.append("        tm = T0 + i * DT")
        L.append("        k1, vals = ev(S, tm, i, i)")
        for j, n in enumerate(keep):
            if n in self.hist_of:
                L.append(f"        h_{loc[n]}[i] = vals[{j}]")
            if n in want:
                L.append(f"        o_{loc[n]}[i] = vals[{j}]")
        L += ["        if i == N: break",
              "        if RK4:",
              "            k2, _ = ev([s + 0.5 * DT * k for s, k in zip(S, k1)], tm + 0.5 * DT, i, i + 1)",
              "            k3, _ = ev([s + 0.5 * DT * k for s, k in zip(S, k2)], tm + 0.5 * DT, i, i + 1)",
              "            k4, _ = ev([s + DT * k for s, k in zip(S, k3)], tm + DT, i, i + 1)",
              "            S = [s + DT / 6.0 * (a + 2.0 * b + 2.0 * c + d) for s, a, b, c, d in zip(S, k1, k2, k3, k4)]",
              "        else:",
              "            S = [s + DT * k for s, k in zip(S, k1)]",
              "        S = [np.minimum(np.maximum(s, lo), hi) for s, lo, hi in zip(S, LO, HI)]"]
        L.append("    return {" + ", ".join(f"{n!r}: o_{loc[n]}" for n in want) + "}")
        ns: dict[str, Any] = {}
        exec(compile("\n".join(L), "<opendynamo-vector>", "exec"), ns)
        return ns["_run"]

    def _bounds(self):
        st = self.system.stocks.values()
        return ([-np.inf if x.lo is None else float(x.lo) for x in st], [np.inf if x.hi is None else float(x.hi) for x in st])

    def _run_numpy(self, sim, P, rec, reducers, seed, seed_mod) -> Run:
        want = frozenset(rec) | frozenset(r.var for r in reducers)
        fn = self._vec_fns.get(want)
        if fn is None:
            fn = self._vec_fns[want] = self._vec_build(want)
        B, N = P.shape[0], sim.steps
        b = np.arange(B, dtype=np.int64)
        seeds = (seed + (b % seed_mod if seed_mod > 0 else b)).astype(np.uint64)
        lo, hi = self._bounds()
        with np.errstate(all="ignore"):
            data = fn(N, B, P, F, np, sim.start, sim.dt, sim.method == "rk4", 1, seeds, lo, hi)
        metrics = np.empty((len(reducers), B))
        for m, r in enumerate(reducers):
            metrics[m] = reduce_series(data[r.var], r, sim)
        k = max(1, sim.save_every)
        return Run(sim.times(), B, {n: data[n][::k] for n in rec}, metrics, "numpy")

    # ==================== generated C: native library and WebAssembly ====================
    def source(self) -> str:
        """C source of the kernel. The same text is compiled natively and to WebAssembly."""
        if self._source is not None:
            return self._source
        s = self.system
        saved, self.tables = self.tables, []      # lookup tables are re-collected for this emission
        dia = CDialect(self)
        comp = lambda key, text: self._compile(key, text, dia).src   # noqa: E731
        sig = "REAL *v, REAL tm, I64 step, I64 hv, I64 seed, const REAL *K, const REAL *hb"
        args = "v, tm, step, hv, seed, K, hb"
        fns: list[str] = []

        def chunked(prefix: str, stmts: list[str]) -> str:
            calls = []
            for k in range(0, max(len(stmts), 1), CHUNK):
                name = f"{prefix}_{k // CHUNK}"
                body = "\n".join("    " + x for x in stmts[k:k + CHUNK])
                fns.append(f"static NOINLINE void {name}({sig}) {{\n    (void)tm; (void)step; (void)hv; (void)seed; (void)K; (void)hb;\n{body}\n}}")
                calls.append(f"    {name}({args});")
            return f"static void {prefix}({sig}) {{\n" + "\n".join(calls) + "\n}"

        init = []
        for kind, n in self.init_plan:
            text = s.stocks[n].init if kind == "init" else s.auxes[n].expr
            init.append(f"v[{self.slots[n]}] = {comp(f'{n}/{kind}', text)};")
        ev = [f"v[{self.slots[n]}] = {comp(n + '/aux', s.auxes[n].expr)};" for n in self.order]
        ev += [f"v[{self.F0 + i}] = {comp(n + '/flow', st.flow)};" for i, (n, st) in enumerate(s.stocks.items())]
        init_fn, ev_fn = chunked("init", init), chunked("ev", ev)
        tables, self.tables = self.tables, saved

        lo, hi = self._bounds()
        arr = lambda xs, pad: ", ".join(xs) if xs else pad   # noqa: E731
        L = [_C_HEAD]
        for k, val in dict(NP=self.NP, NS=self.NS, NH=self.NH, F0=self.F0, S0=self.S0, NV=self.NV).items():
            L.append(f"#define {k} {val}")
        for k, tb in enumerate(tables):
            L.append(f"static const REAL TB{k}[] = {{" + ", ".join(dia.const(x) for x in tb) + "};")
        L.append("static const int HSRC[] = {" + arr([str(self.slots[n]) for n in self.hist_of], "0") + "};")
        L.append("static const REAL SLO[] = {" + arr([dia.const(x) for x in lo], "0") + "};")
        L.append("static const REAL SHI[] = {" + arr([dia.const(x) for x in hi], "0") + "};")
        L.append(_C_HELPERS)
        L += fns + [init_fn, ev_fn, _C_RUN]
        self._source = "\n".join(L)
        return self._source

    @property
    def digest(self) -> str:
        return hashlib.sha1((self.source() + _BUILD_ID).encode()).hexdigest()[:20]

    def wasm(self) -> bytes:
        """The kernel as a WebAssembly module (exports ``run``, ``malloc``, ``free`` and its memory)."""
        return build_wasm(self.source(), self.digest)

    def _pack(self, sim: Sim, rec, reducers, seed, seed_mod):
        n = sim.steps
        cfg = np.zeros(_CFG)
        cfg[:9] = [sim.start, sim.dt, n, 1 if sim.method == "rk4" else 0, max(1, sim.save_every),
                   len(rec), len(reducers), seed, seed_mod]
        targets = [r for r in reducers if r.kind == "sse"]
        tgt = np.zeros((max(len(targets), 1), n + 1))
        ix = [self.slots[x] for x in rec]
        row = 0
        for r in reducers:
            ix += [self.slots[r.var], KINDS[r.kind], r.i0, n if r.i1 < 0 else min(r.i1, n), row]
            if r.kind == "sse":
                tgt[row] = np.asarray(r.target, dtype=float)
                row += 1
        return cfg, np.array(ix + [0], dtype=np.int32), tgt

    def _run_native(self, sim, P, rec, reducers, seed, seed_mod) -> Run:
        B, R, M = P.shape[0], len(rec), len(reducers)
        cfg, ix, tgt = self._pack(sim, rec, reducers, seed, seed_mod)
        out = np.empty((B, max(R, 1), sim.n_out))
        met = np.empty((B, max(M, 1)))
        with native(self.source(), self.digest) as call:
            call(B, cfg, P, ix, tgt, out, met)
        return Run(sim.times(), B, {n: out[:, i, :].T for i, n in enumerate(rec)}, met[:, :M].T.copy(), "native")


class _VecCtx:
    def __init__(self, loc: dict[str, str]):
        self.loc = loc

    def local(self, name):
        return self.loc[name]

    def hist(self, name):
        return "h_" + self.loc[name]


# ---------------------------------------------------------------------------------
# Building and loading kernels
# ---------------------------------------------------------------------------------
def cache_dir() -> Path:
    root = Path(os.environ.get("OPENDYNAMO_CACHE", Path.home() / ".cache" / "opendynamo")) / "kernels"
    root.mkdir(parents=True, exist_ok=True)
    return root


def n_threads() -> int:
    return max(1, int(os.environ.get("OPENDYNAMO_THREADS", os.cpu_count() or 1)))


def _zig_cmd() -> list[str]:
    """The zig compiler that ships in the ``ziglang`` wheel (calling the binary directly saves a Python start-up)."""
    try:
        import ziglang
        exe = Path(ziglang.__file__).parent / ("zig.exe" if sys.platform == "win32" else "zig")
        if exe.exists():
            return [str(exe)]
    except ImportError:
        pass
    return [sys.executable, "-m", "ziglang"]


def _zig(args: list[str], out: Path, src: str, digest: str) -> None:
    """Compile ``src`` to ``out`` with the bundled zig C compiler (atomic: safe when several workers race)."""
    c_path = cache_dir() / f"k_{digest}.c"
    c_path.write_text(src, encoding="utf-8")
    tmp = out.with_name(f"{out.stem}_{os.getpid()}_{threading.get_ident()}{out.suffix}")
    res = subprocess.run([*_zig_cmd(), "cc", *args, "-o", str(tmp), str(c_path)], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("kernel compilation failed:\n" + res.stderr[-4000:])
    try:
        os.replace(tmp, out)
    except OSError:                  # another worker won the race; use its build
        tmp.unlink(missing_ok=True)
    for junk in (tmp.with_suffix(".pdb"), tmp.with_suffix(".lib")):
        junk.unlink(missing_ok=True)
    _prune()


def _prune(keep: int = int(os.environ.get("OPENDYNAMO_CACHE_FILES", "3000"))) -> None:
    files = sorted(cache_dir().iterdir(), key=lambda p: p.stat().st_mtime)
    for p in files[:max(0, len(files) - keep)]:
        try:
            p.unlink()
        except OSError:
            pass


# -O1 for WebAssembly: within about 20% of -O2 at run time, and it compiles large models in a second instead of a minute
_WASM_ARGS = ["-target", "wasm32-wasi", "-O1", "-s", "-fno-math-errno", "-mexec-model=reactor",
              "-Wl,--export=run", "-Wl,--export=malloc", "-Wl,--export=free"]
_NATIVE_ARGS = ["-O2", "-shared", "-s", "-fno-math-errno"] + ([] if sys.platform == "win32" else ["-fPIC"])
_LIB_EXT = {"win32": ".dll", "darwin": ".dylib"}.get(sys.platform, ".so")


def build_wasm(src: str, digest: str) -> bytes:
    path = cache_dir() / f"k_{digest}.wasm"
    if not path.exists():
        _zig(_WASM_ARGS, path, src, digest)
    return path.read_bytes()


def warm() -> None:
    """Run the compiler once for each target, so its caches are loaded before the first real request.

    A server calls this in the background at start-up: the first compile in a fresh process takes seconds,
    later ones a fraction of a second."""
    s = System()
    s.param("k", 1.0)
    s.stock("x", 1.0, "-k * x")
    src, tag = s.compile().source(), f"warm{os.getpid()}"
    for args, ext in ((_WASM_ARGS, ".wasm"), (_NATIVE_ARGS, _LIB_EXT)):
        out = cache_dir() / f"k_{tag}{ext}"
        try:
            _zig(args, out, src, tag)
        except Exception:                                  # a real request will report the problem properly
            pass
        out.unlink(missing_ok=True)
    (cache_dir() / f"k_{tag}.c").unlink(missing_ok=True)


_pool: ThreadPoolExecutor | None = None
_libs: "OrderedDict[str, _Lib]" = OrderedDict()
_libs_lock = threading.Lock()
_MAX_LIBS = int(os.environ.get("OPENDYNAMO_LOADED_KERNELS", "64"))


class _Lib:
    def __init__(self, path: Path):
        self.lib = ctypes.CDLL(str(path))
        self.lib.run.restype = ctypes.c_int
        self.lib.run.argtypes = [ctypes.c_int64, ctypes.c_int64] + [ctypes.c_void_p] * 6
        self.users = 0

    def unload(self) -> None:
        handle, self.lib = self.lib._handle, None
        try:
            if sys.platform == "win32":
                ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(handle))
            else:
                import _ctypes
                _ctypes.dlclose(handle)
        except Exception:                                  # leaving it mapped is harmless
            pass


class native:
    """Context manager giving a callable that runs a batch on the native kernel across all cores.

    Libraries are compiled once (cached on disk by content hash) and at most ``_MAX_LIBS`` stay
    loaded, so a long-running server does not accumulate one library per model ever opened."""

    def __init__(self, src: str, digest: str):
        self.src, self.digest = src, digest

    def __enter__(self):
        with _libs_lock:
            entry = _libs.get(self.digest)
            if entry is not None:
                _libs.move_to_end(self.digest)
                entry.users += 1
        if entry is None:
            path = cache_dir() / f"k_{self.digest}{_LIB_EXT}"
            if not path.exists():
                _zig(_NATIVE_ARGS, path, self.src, self.digest)
            with _libs_lock:
                entry = _libs.get(self.digest)
                if entry is None:
                    entry = _libs[self.digest] = _Lib(path)
                entry.users += 1
                for key in [k for k, e in _libs.items() if e.users == 0][:max(0, len(_libs) - _MAX_LIBS)]:
                    _libs.pop(key).unload()
        self.entry = entry
        return self._call

    def __exit__(self, *exc):
        with _libs_lock:
            self.entry.users -= 1
        return False

    def _call(self, B: int, cfg, P, ix, tgt, out, met) -> None:
        global _pool
        run = self.entry.lib.run
        ptrs = [a.ctypes.data for a in (cfg, P, ix, tgt, out, met)]
        n = n_threads()
        if B < 4 * n or n == 1:
            codes = [run(0, B, *ptrs)]
        else:
            if _pool is None:
                _pool = ThreadPoolExecutor(max_workers=n)
            size = max(1, B // (n * 4))                  # small slices keep the cores evenly loaded
            codes = [j.result() for j in [_pool.submit(run, lo, min(B, lo + size), *ptrs) for lo in range(0, B, size)]]
        if any(codes):
            raise MemoryError("the kernel could not allocate its working memory")


_C_HEAD = """/* Generated by Open-Dynamo - do not edit. */
#include <math.h>
#include <stdlib.h>
typedef double REAL;
typedef long long I64;
#define NOINLINE __attribute__((noinline))
#define HN ((I64)K[2] + 1)
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif"""

_C_HELPERS = """
static REAL min2(REAL a, REAL b) { return a < b ? a : b; }
static REAL max2(REAL a, REAL b) { return a > b ? a : b; }
static REAL f_late(REAL t) { return t + 1e-9 * (fabs(t) + 1.0); }
static REAL f_step(REAL t, REAL h, REAL t0) { return f_late(t) >= t0 ? h : 0.0; }
static REAL f_ramp(REAL t, REAL slope, REAL t0, REAL t1) {
    const REAL x = (t < t1 ? t : t1) - t0;
    return x > 0 ? slope * x : 0.0;
}
static REAL f_pulse(REAL t, REAL t0, REAL width, REAL period) {
    REAL rel = f_late(t) - t0;
    if (!(rel >= 0)) return 0.0;
    if (period > 0) rel = fmod(rel, period);
    return rel < width ? 1.0 : 0.0;
}
static REAL f_div(REAL a, REAL b) { return b == 0 ? 0.0 : a / b; }
static REAL f_sign(REAL a) { return (REAL)((a > 0) - (a < 0)); }
static REAL f_mod(REAL a, REAL b) {
    const REAL r = fmod(a, b);
    return (r != 0 && ((r < 0) != (b < 0))) ? r + b : r;
}
static REAL f_lookup(REAL x, const REAL *xs, const REAL *ys, int n) {
    int i = 1;
    if (!(x > xs[0])) return ys[0];
    if (x >= xs[n - 1]) return ys[n - 1];
    while (xs[i] < x) i++;
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
}
static REAL f_delay(const REAL *h, I64 valid, REAL pos, REAL init) {
    if (!(pos >= 0) || valid <= 0) return init;
    if (pos >= (REAL)(valid - 1)) return h[valid - 1];
    const I64 k = (I64)pos;
    return h[k] + (h[k + 1] - h[k]) * (pos - (REAL)k);
}
static REAL f_rand(I64 seed, I64 step, I64 site) {
    unsigned long long z = (unsigned long long)seed * 0x9E3779B97F4A7C15ULL
        + (unsigned long long)step * 0xBF58476D1CE4E5B9ULL
        + (unsigned long long)site * 0x94D049BB133111EBULL + 0x2545F4914F6CDD1DULL;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    z = z ^ (z >> 31);
    return (REAL)(z >> 11) * (1.0 / 9007199254740992.0);
}
static REAL f_randn(I64 seed, I64 step, I64 site) {
    REAL u1 = f_rand(seed, step, site);
    if (u1 < 1e-300) u1 = 1e-300;
    return sqrt(-2.0 * log(u1)) * cos(6.283185307179586 * f_rand(seed, step, site + 1));
}
"""

# K (run configuration): t0, dt, steps, rk4, stride, n recorded, n reducers, seed, seed modulus.
# IX: recorded slots, then five ints per reducer (slot, kind, first step, last step, target row).
# OUT is (scenario, recorded variable, sample); MET is (scenario, reducer).
_C_RUN = """
EXPORT int run(I64 b0, I64 b1, const REAL *K, const REAL *P, const int *IX, const REAL *TGT, REAL *OUT, REAL *MET) {
    const REAL T0 = K[0], DT = K[1];
    const I64 N = (I64)K[2], STRIDE = (I64)K[4] < 1 ? 1 : (I64)K[4];
    const int RK4 = (int)K[3], NR = (int)K[5], NM = (int)K[6];
    const I64 SEED = (I64)K[7], SMOD = (I64)K[8], NOUT = N / STRIDE + 1;
    const int *rec = IX, *red = IX + NR;
    REAL *v = (REAL *)malloc(sizeof(REAL) * (NV + 4 * NS + 1));
    REAL *hb = (REAL *)malloc(sizeof(REAL) * (NH * (N + 1) + 1));
    REAL *s0 = v + NV, *k1 = s0 + NS, *k2 = k1 + NS, *k3 = k2 + NS;
    I64 b, i;
    int j, m;
    if (!v || !hb) { free(v); free(hb); return 1; }
    for (b = b0; b < b1; b++) {
        const REAL *p = P + b * NP;
        const I64 seed = SEED + (SMOD > 0 ? b % SMOD : b);
        REAL *out = OUT + b * (NR > 0 ? NR : 1) * NOUT, *met = MET + b * (NM > 0 ? NM : 1);
        for (j = 0; j < NP; j++) v[j] = p[j];
        for (j = NP; j < NV; j++) v[j] = 0.0;
        init(v, T0, 0, 0, seed, K, hb);
        for (m = 0; m < NM; m++) {
            const int kind = red[m * 5 + 1];
            met[m] = kind == 2 ? (REAL)INFINITY : (kind == 3 ? -(REAL)INFINITY : 0.0);
        }
        for (i = 0; i <= N; i++) {
            const REAL tm = T0 + (REAL)i * DT;
            ev(v, tm, i, i, seed, K, hb);
            for (j = 0; j < NH; j++) hb[j * (N + 1) + i] = v[HSRC[j]];
            if (i % STRIDE == 0)
                for (j = 0; j < NR; j++) out[j * NOUT + i / STRIDE] = v[rec[j]];
            for (m = 0; m < NM; m++) {
                const int *r = red + m * 5;
                const REAL x = v[r[0]];
                if (i < r[2] || i > r[3]) continue;
                switch (r[1]) {
                    case 0: met[m] = x; break;
                    case 1: if (i == r[2]) met[m] = x; break;
                    case 2: if (x < met[m]) met[m] = x; break;
                    case 3: if (x > met[m]) met[m] = x; break;
                    case 4: met[m] += x / (REAL)(r[3] - r[2] + 1); break;
                    case 5: met[m] += (r[3] == r[2]) ? 0.0 : x * DT * ((i == r[2] || i == r[3]) ? 0.5 : 1.0); break;
                    case 6: { const REAL y = TGT[(I64)r[4] * (N + 1) + i]; if (y == y) met[m] += (x - y) * (x - y); } break;
                    default: met[m] += x;
                }
            }
            if (i == N) break;
            if (!RK4) {
                for (j = 0; j < NS; j++) v[S0 + j] += DT * v[F0 + j];
            } else {
                for (j = 0; j < NS; j++) { s0[j] = v[S0 + j]; k1[j] = v[F0 + j]; v[S0 + j] = s0[j] + 0.5 * DT * k1[j]; }
                ev(v, tm + 0.5 * DT, i, i + 1, seed, K, hb);
                for (j = 0; j < NS; j++) { k2[j] = v[F0 + j]; v[S0 + j] = s0[j] + 0.5 * DT * k2[j]; }
                ev(v, tm + 0.5 * DT, i, i + 1, seed, K, hb);
                for (j = 0; j < NS; j++) { k3[j] = v[F0 + j]; v[S0 + j] = s0[j] + DT * k3[j]; }
                ev(v, tm + DT, i, i + 1, seed, K, hb);
                for (j = 0; j < NS; j++) v[S0 + j] = s0[j] + DT / 6.0 * (k1[j] + 2.0 * k2[j] + 2.0 * k3[j] + v[F0 + j]);
            }
            for (j = 0; j < NS; j++) {
                if (v[S0 + j] < SLO[j]) v[S0 + j] = SLO[j];
                if (v[S0 + j] > SHI[j]) v[S0 + j] = SHI[j];
            }
        }
    }
    free(v);
    free(hb);
    return 0;
}
"""
