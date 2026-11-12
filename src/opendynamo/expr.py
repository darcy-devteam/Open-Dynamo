"""Safe expression compiler.

Model equations are small formulas such as::

    beta * Susceptible * Infected / N
    step(5, 2) + 0.1 * sin(2 * pi * time)

They are parsed with :mod:`ast`, checked against a whitelist (names, numbers,
arithmetic, comparisons and the functions listed below; no attribute access, no
subscripts, no arbitrary calls) and re-emitted by a *dialect*:

* :class:`VectorDialect` emits numpy code in which every variable is an array with
  one value per scenario (the reference backend);
* :class:`CDialect` emits C for a single scenario. The same C is compiled to a
  native library for the server and to WebAssembly for the browser.

Nothing a user types is ever executed directly: only the re-emitted text is.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field


class ExprError(ValueError):
    """An expression is malformed or refers to something that does not exist."""


BUILTINS = {"time", "dt", "pi"}
MATH1 = {"abs", "exp", "log", "log10", "sqrt", "sin", "cos", "tan", "asin", "acos", "atan",
         "sinh", "cosh", "tanh", "floor", "ceil", "round", "sign"}
PURE = MATH1 | {"min", "max", "clip", "where", "div", "mod", "pow", "atan2", "lookup"}
TIMED = {"step", "ramp", "pulse"}
RANDOM = {"rand", "randn"}
ARITY = {**{f: (1, 1) for f in MATH1}, "min": (2, 99), "max": (2, 99), "clip": (3, 3), "where": (3, 3),
         "div": (2, 2), "mod": (2, 2), "pow": (2, 2), "atan2": (2, 2), "lookup": (3, 3),
         "step": (2, 2), "ramp": (2, 3), "pulse": (2, 3), "rand": (0, 0), "randn": (0, 0), "delay": (2, 3)}
FUNCTIONS = sorted(PURE | TIMED | RANDOM | {"delay"})
RESERVED = BUILTINS | set(FUNCTIONS) | {"smooth", "delay1", "delay3", "and", "or", "not", "if", "else", "True", "False"}

_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "**", ast.Mod: "%"}
_CHAIN = {ast.Add: "sum", ast.Sub: "sum", ast.Mult: "product", ast.Div: "product"}   # same-precedence families
_CMP = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}


@dataclass
class Compiled:
    src: str = ""
    deps: set[str] = field(default_factory=set)   # variables read at the same instant
    hist: set[str] = field(default_factory=set)   # variables read through delay()
    sites: int = 0                                # random draws used


class VectorDialect:
    """numpy source; ``ctx`` supplies local names."""

    def __init__(self, ctx):
        self.ctx, self.site = ctx, 0

    def const(self, x):
        return repr(float(x))

    def var(self, name):
        return self.ctx.local(name)

    def builtin(self, name):
        return {"time": "tm", "dt": "DT", "pi": repr(3.141592653589793)}[name]

    def binop(self, op, l, r):
        return f"F.mod({l}, {r})" if op == "%" else f"({l} {op} {r})"

    def cmp(self, l, op, r):
        return f"(({l} {op} {r}) * 1.0)"

    def not_(self, x):
        return f"F.not_({x})"

    def boolop(self, kind, xs):
        return f"F.{kind}_({', '.join(xs)})"

    def where(self, c, a, b):
        return f"F.where({c}, {a}, {b})"

    def pure(self, fn, args):
        name = {"min": "vmin", "max": "vmax", "abs": "vabs", "round": "rnd", "pow": "power"}.get(fn, fn)
        return f"F.{name}({', '.join(args)})"

    def timed(self, fn, args):
        return f"F.{fn}(tm, {', '.join(args)})"

    def rand(self, fn, site):
        return f"F.{fn}(SEED, step, {site})"

    def delay(self, name, tau, init):
        return f"F.delay({self.ctx.hist(name)}, hv, (tm - ({tau}) - T0) / DT, {init})"

    def table(self, values):
        return "[" + ", ".join(values) + "]"


class CDialect:
    """C source for one scenario; state lives in the array ``v``."""

    def __init__(self, ctx):
        self.ctx, self.site = ctx, 0

    def const(self, x):
        x = float(x)
        if x != x:
            return "((REAL)NAN)"
        if x in (float("inf"), float("-inf")):
            return "((REAL)INFINITY)" if x > 0 else "(-(REAL)INFINITY)"
        return repr(x)

    def var(self, name):
        return f"v[{self.ctx.slot(name)}]"

    def builtin(self, name):
        return {"time": "tm", "dt": "K[1]", "pi": "3.141592653589793"}[name]

    def binop(self, op, l, r):
        if op == "**":
            return f"pow({l}, {r})"
        if op == "%":
            return f"f_mod({l}, {r})"
        return f"({l} {op} {r})"

    def cmp(self, l, op, r):
        return f"((REAL)({l} {op} {r}))"

    def not_(self, x):
        return f"((REAL)!({x} > 0.5))"

    def boolop(self, kind, xs):
        op = " && " if kind == "and" else " || "
        return "((REAL)(" + op.join(f"({x} > 0.5)" for x in xs) + "))"

    def where(self, c, a, b):
        return f"(({c} > 0.5) ? ({a}) : ({b}))"

    def pure(self, fn, args):
        if fn in ("min", "max"):
            out = args[0]
            for a in args[1:]:
                out = f"{fn}2({out}, {a})"
            return out
        if fn == "abs":
            return f"fabs({args[0]})"
        if fn == "round":
            return f"floor({args[0]} + 0.5)"
        if fn == "clip":
            return f"min2(max2({args[0]}, {args[1]}), {args[2]})"
        if fn == "where":
            return self.where(*args)
        if fn == "lookup":
            return f"f_lookup({args[0]}, {args[1]}, {args[2]}, {self.ctx.table_len(args[1])})"
        if fn in ("div", "mod", "sign"):
            return f"f_{fn}({', '.join(args)})"
        return f"{fn}({', '.join(args)})"               # straight from <math.h>

    def timed(self, fn, args):
        if fn == "ramp" and len(args) == 2:
            args = args + ["((REAL)INFINITY)"]
        if fn == "pulse" and len(args) == 2:
            args = args + ["0.0"]
        return f"f_{fn}(tm, {', '.join(args)})"

    def rand(self, fn, site):
        return f"f_{fn}(seed, step, {site})"

    def delay(self, name, tau, init):
        return f"f_delay(hb + {self.ctx.hist(name)} * HN, hv, (tm - ({tau}) - K[0]) / K[1], {init})"

    def table(self, values):
        return self.ctx.table([float(x) for x in values])


def parse(text) -> ast.AST:
    src = str(text).strip().replace("^", "**")           # engineers write x^2
    if not src:
        raise ExprError("the equation is empty")
    try:
        return ast.parse(src, mode="eval").body
    except SyntaxError as e:
        raise ExprError(f"syntax error: {e.msg}") from None


def names_in(text) -> set[str]:
    """Identifiers an expression mentions (variables and functions alike); empty if it does not parse."""
    try:
        return {n.id for n in ast.walk(parse(text)) if isinstance(n, ast.Name)}
    except ExprError:
        return set()


def compile_expr(text, dialect, known, site0: int = 0) -> Compiled:
    """Compile ``text`` with ``dialect``. ``known(name)`` says whether a variable exists."""
    out = Compiled()
    if isinstance(text, (int, float)):
        out.src = dialect.const(text)
        return out
    tree = parse(text)

    def var(node: ast.AST) -> str:
        if not isinstance(node, ast.Name):
            raise ExprError("delay() needs a variable name as its first argument")
        if not known(node.id):
            raise ExprError(f"unknown name '{node.id}'")
        return node.id

    def emit(n: ast.AST) -> str:
        if isinstance(n, ast.Constant):
            if isinstance(n.value, (bool, int, float)):
                return dialect.const(n.value)
            raise ExprError(f"unsupported value {n.value!r}")
        if isinstance(n, ast.Name):
            if known(n.id):                       # a model variable may shadow a built-in name
                out.deps.add(n.id)
                return dialect.var(n.id)
            if n.id in BUILTINS:
                return dialect.builtin(n.id)
            if n.id in ARITY:
                raise ExprError(f"'{n.id}' is a function: write {n.id}(...)")
            raise ExprError(f"unknown name '{n.id}'")
        if isinstance(n, ast.BinOp) and type(n.op) in _CHAIN:
            # a + b - c + ... is left-associative: emit it flat so a long sum is not hundreds of brackets deep
            family, parts, node = _CHAIN[type(n.op)], [], n
            while isinstance(node, ast.BinOp) and _CHAIN.get(type(node.op)) == family:
                parts.append((_BIN[type(node.op)], node.right))
                node = node.left
            src = emit(node)
            for op, right in reversed(parts):
                src += f" {op} {emit(right)}"
            return f"({src})"
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return dialect.binop(_BIN[type(n.op)], emit(n.left), emit(n.right))
        if isinstance(n, ast.UnaryOp):
            if isinstance(n.op, ast.USub):
                return f"(-{emit(n.operand)})"
            if isinstance(n.op, ast.UAdd):
                return emit(n.operand)
            if isinstance(n.op, ast.Not):
                return dialect.not_(emit(n.operand))
        if isinstance(n, ast.Compare):
            parts, left = [], emit(n.left)
            for op, comp in zip(n.ops, n.comparators):
                if type(op) not in _CMP:
                    raise ExprError("unsupported comparison")
                right = emit(comp)
                parts.append(dialect.cmp(left, _CMP[type(op)], right))
                left = right
            return parts[0] if len(parts) == 1 else "(" + " * ".join(parts) + ")"
        if isinstance(n, ast.BoolOp):
            return dialect.boolop("and" if isinstance(n.op, ast.And) else "or", [emit(v) for v in n.values])
        if isinstance(n, ast.IfExp):
            return dialect.where(emit(n.test), emit(n.body), emit(n.orelse))
        if isinstance(n, ast.List):
            vals = []
            for e in n.elts:
                try:
                    vals.append(repr(float(ast.literal_eval(e))))
                except (ValueError, TypeError, SyntaxError):
                    raise ExprError("a list may only hold numbers") from None
            if len(vals) < 2:
                raise ExprError("a lookup table needs at least two points")
            return dialect.table(vals)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            fn, args = n.func.id, n.args
            if fn not in ARITY:
                raise ExprError(f"unknown function '{fn}()'")
            lo, hi = ARITY[fn]
            if not lo <= len(args) <= hi:
                want = str(lo) if lo == hi else f"{lo} to {hi}"
                raise ExprError(f"{fn}() takes {want} argument(s), got {len(args)}")
            if fn == "lookup":
                if not all(isinstance(a, ast.List) for a in args[1:]) or len(args[1].elts) != len(args[2].elts):
                    raise ExprError("lookup(x, [x1, x2, ...], [y1, y2, ...]) needs two lists of the same length")
            if fn in PURE:
                return dialect.pure(fn, [emit(a) for a in args])
            if fn in TIMED:
                return dialect.timed(fn, [emit(a) for a in args])
            if fn in RANDOM:
                site = site0 + out.sites
                out.sites += 2
                return dialect.rand(fn, site)
            name = var(args[0])                   # delay(x, time[, initial])
            out.hist.add(name)
            return dialect.delay(name, emit(args[1]), emit(args[2]) if len(args) == 3 else dialect.const(0.0))
        raise ExprError(f"unsupported syntax ({type(n).__name__})")

    out.src = emit(tree)
    return out
