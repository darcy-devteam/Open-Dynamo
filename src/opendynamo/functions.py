"""numpy versions of the functions available in model equations (reference backend).

Every function is vectorised over scenarios: arguments are scalars or arrays of
shape (B,). The generated C in :mod:`opendynamo.kernel` implements the same
functions; the test-suite checks the two agree.
"""
from __future__ import annotations

from functools import reduce

import numpy as np

_M = (1 << 64) - 1
_C1, _C2, _C3, _C4 = 0x9E3779B97F4A7C15, 0xBF58476D1CE4E5B9, 0x94D049BB133111EB, 0x2545F4914F6CDD1D


def _late(t):
    """Time nudged forward by a rounding error, so events at t0 fire on the step that lands on t0."""
    return t + 1e-9 * (np.abs(t) + 1.0)


# -- inputs shaped in time ------------------------------------------------------
def step(t, height, t0):
    """0 before t0, ``height`` from t0 onward."""
    return height * (_late(t) >= t0)


def ramp(t, slope, t0, t1=np.inf):
    """0 until t0, then rising at ``slope`` per unit time (flat after t1)."""
    return slope * np.maximum(np.minimum(t, t1) - t0, 0.0)


def pulse(t, t0, width, period=0.0):
    """1 for ``width`` time units starting at t0, repeating every ``period`` if given."""
    rel = _late(t) - t0
    per = np.asarray(period, dtype=float)
    rep = np.fmod(rel, np.where(per > 0, per, 1.0))
    rel2 = np.where(per > 0, rep, rel)
    return ((rel >= 0) & (rel2 < width)) * 1.0


# -- history ---------------------------------------------------------------------
def delay(hist, valid, pos, init):
    """Value of a recorded variable at fractional step index ``pos`` (``init`` before the start).

    ``hist`` is (steps, B); rows 0..valid-1 have been written."""
    width = hist.shape[1]
    pos = np.broadcast_to(np.asarray(pos, dtype=float), (width,))
    init = np.broadcast_to(np.asarray(init, dtype=float), (width,))
    if valid <= 0:
        return init + 0.0
    ok = pos >= 0
    p = np.where(ok, np.minimum(pos, valid - 1.0), 0.0)
    k = np.minimum(np.floor(p).astype(np.int64), max(valid - 2, 0))
    cols = np.arange(width)
    lo, hi = hist[k, cols], hist[np.minimum(k + 1, valid - 1), cols]
    return np.where(ok, lo + (hi - lo) * (p - k), init)


# -- random numbers (counter based: the same seed, step and call site always give the same draw) --
def rand(seed, step, site):
    z = np.asarray(seed, dtype=np.uint64) * np.uint64(_C1) + np.uint64((step * _C2 + site * _C3 + _C4) & _M)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(_C2)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(_C3)
    z = z ^ (z >> np.uint64(31))
    return (z >> np.uint64(11)).astype(np.float64) * (1.0 / 9007199254740992.0)


def randn(seed, step, site):
    u1 = np.maximum(rand(seed, step, site), 1e-300)
    return np.sqrt(-2.0 * np.log(u1)) * np.cos(6.283185307179586 * rand(seed, step, site + 1))


# -- maths -------------------------------------------------------------------------
def vmin(*xs):
    return reduce(np.minimum, xs)


def vmax(*xs):
    return reduce(np.maximum, xs)


def where(cond, a, b):
    return np.where(np.asarray(cond) > 0.5, a, b)


def div(a, b):
    """Division that returns 0 where the denominator is 0."""
    b_arr = np.asarray(b, dtype=float)
    return np.where(b_arr == 0.0, 0.0, a / np.where(b_arr == 0.0, 1.0, b_arr))


def lookup(x, xs, ys):
    """Piecewise-linear table lookup, flat outside the table."""
    return np.interp(x, xs, ys)


def and_(*xs):
    return reduce(np.multiply, [np.asarray(x) > 0.5 for x in xs]) * 1.0


def or_(*xs):
    return reduce(np.maximum, [(np.asarray(x) > 0.5) * 1.0 for x in xs])


def not_(x):
    return 1.0 - (np.asarray(x) > 0.5) * 1.0


def rnd(x):
    return np.floor(np.asarray(x) + 0.5)


def power(a, b):
    return np.power(np.asarray(a, dtype=float), b)


def mod(a, b):
    return np.mod(np.asarray(a, dtype=float), b)


def atan2(a, b):
    return np.arctan2(a, b)


clip, vabs, sign = np.clip, np.abs, np.sign
exp, log, log10, sqrt = np.exp, np.log, np.log10, np.sqrt
sin, cos, tan, asin, acos, atan = np.sin, np.cos, np.tan, np.arcsin, np.arccos, np.arctan
sinh, cosh, tanh, floor, ceil = np.sinh, np.cosh, np.tanh, np.floor, np.ceil
