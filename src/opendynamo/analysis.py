"""Experiments that need many runs: parameter sweeps, Monte Carlo, optimisation and fitting to data.

Each one evaluates its scenarios as a single batch on the native kernel, which is why a
browser on a modest laptop hands these to the server.
"""
from __future__ import annotations

from statistics import NormalDist
from typing import Sequence

import numpy as np

from .kernel import Reducer, Sim
from .model import Model

MAX_VALUES = 8_000_000          # trajectory values held in memory for one experiment


def thin(model: Model, sim: Sim, scenarios: int, n_vars: int, max_points: int = 600) -> Sim:
    """``sim`` with trajectories thinned so the experiment's output stays a sensible size."""
    cap = max(2, min(max_points, MAX_VALUES // max(1, scenarios * max(1, n_vars))))
    every = max(1, -(-sim.steps // (cap - 1)))
    return Sim(sim.start, sim.stop, sim.dt, sim.method, every)


def _lhs(rng: np.random.Generator, n: int, d: int) -> np.ndarray:
    """Latin hypercube sample of the unit cube, (n, d)."""
    u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
    for j in range(d):
        rng.shuffle(u[:, j])
    return u


def sweep(model: Model, param: str, values: Sequence[float], record: Sequence[str], sim: Sim | None = None,
          overrides: dict | None = None) -> dict:
    """One run per value of ``param``; returns each run's trajectories."""
    values = np.asarray(values, dtype=float)
    sim = thin(model, sim or model.sim, len(values), len(record))
    run = model.compiled.run(sim, {**(overrides or {}), param: values}, record=record)
    return {"t": run.t, "param": param, "values": values,
            "series": {n: run[n].T for n in record}}          # each (runs, samples)


def monte_carlo(model: Model, uncertain: Sequence[dict], n: int, record: Sequence[str], sim: Sim | None = None,
                overrides: dict | None = None, seed: int = 1) -> dict:
    """Run ``n`` scenarios with parameters drawn from distributions (and fresh noise in each).

    ``uncertain`` items: ``{"param", "dist": "uniform" | "normal", "a", "b"}`` where (a, b) is
    (low, high) or (mean, standard deviation)."""
    rng = np.random.default_rng(seed)
    u = _lhs(rng, n, max(1, len(uncertain)))
    draws: dict = dict(overrides or {})
    inv = np.vectorize(NormalDist().inv_cdf)
    for j, spec in enumerate(uncertain):
        a, b = float(spec["a"]), float(spec["b"])
        if spec.get("dist", "uniform") == "normal":
            draws[spec["param"]] = a + b * inv(np.clip(u[:, j], 1e-12, 1 - 1e-12))
        else:
            draws[spec["param"]] = a + (b - a) * u[:, j]
    sim = thin(model, sim or model.sim, n, len(record))
    run = model.compiled.run(sim, draws, batch=n, record=record, seed=seed)
    out: dict = {"t": run.t, "n": n, "bands": {}, "final": {}, "samples": {}}
    qs = [5, 25, 50, 75, 95]
    for name in record:
        x = run[name]                                                   # (samples, n)
        with np.errstate(all="ignore"):
            pct = np.nanpercentile(x, qs, axis=1)
            out["bands"][name] = {**{f"p{q}": pct[i] for i, q in enumerate(qs)}, "mean": np.nanmean(x, axis=1)}
        last = x[-1][np.isfinite(x[-1])]
        counts, edges = np.histogram(last, bins=24) if last.size else (np.zeros(0), np.zeros(1))
        out["final"][name] = {"counts": counts, "edges": edges, "mean": last.mean() if last.size else np.nan,
                              "sd": last.std() if last.size else np.nan,
                              "p5": np.percentile(last, 5) if last.size else np.nan,
                              "p95": np.percentile(last, 95) if last.size else np.nan}
        out["samples"][name] = x[:, :min(n, 24)].T                      # a few individual runs to show
    return out


def optimise(model: Model, decisions: Sequence[dict], objective: dict, sim: Sim | None = None,
             overrides: dict | None = None, evaluations: int = 6000, seed: int = 1) -> dict:
    """Search parameter values by differential evolution.

    ``decisions``: ``{"param", "min", "max"}``. ``objective`` is either
    ``{"kind": "fit", "targets": [{"var", "t": [...], "y": [...]}]}`` (match observed data) or
    ``{"kind": "min" | "max", "var", "agg": "last" | "min" | "max" | "mean" | "integral"}``.
    """
    sim = sim or model.sim
    names = [d["param"] for d in decisions]
    lo = np.array([float(d["min"]) for d in decisions])
    hi = np.array([float(d["max"]) for d in decisions])
    if not names or np.any(hi <= lo):
        raise ValueError("each parameter to search needs min < max")
    base = model.compiled.param_matrix(overrides)[0]
    cols = [model.compiled.slots[n] for n in names]

    if objective["kind"] == "fit":
        reducers, weights = [], []
        for tg in objective["targets"]:
            data = model.target(tg["t"], tg["y"], sim)
            count = int(np.isfinite(data).sum())
            if count == 0:
                raise ValueError(f"none of the data for '{tg['var']}' falls inside the simulated time span")
            spread = float(np.nanvar(data)) or float(np.nanmean(data) ** 2) or 1.0
            reducers.append(Reducer(tg["var"], "sse", target=tuple(data)))
            weights.append(1.0 / (count * spread))
        weights = np.array(weights)
        score = lambda m: (m * weights[:, None]).sum(0)          # noqa: E731  normalised mean squared error
    else:
        sign = 1.0 if objective["kind"] == "min" else -1.0
        reducers = [Reducer(objective["var"], objective.get("agg", "last"))]
        score = lambda m: sign * m[0]                             # noqa: E731

    def evaluate(X: np.ndarray) -> np.ndarray:
        P = np.tile(base, (len(X), 1))
        P[:, cols] = X
        s = score(model.compiled.run(sim, reducers=reducers, P=P, seed=seed, seed_mod=1).metrics)
        return np.where(np.isfinite(s), s, np.inf)

    rng = np.random.default_rng(seed)
    d = len(names)
    pop = int(np.clip(12 * d, 24, 96))
    X = lo + (hi - lo) * _lhs(rng, pop, d)
    X[0] = np.clip(base[cols], lo, hi)                        # the current values are a candidate too
    fx = evaluate(X)
    history = [float(fx.min())]
    used = pop
    while used + pop <= max(evaluations, 2 * pop):
        idx = np.array([rng.choice(pop - 1, 3, replace=False) for _ in range(pop)])
        idx += idx >= np.arange(pop)[:, None]                 # three distinct others for each member
        best = X[fx.argmin()]
        f = rng.uniform(0.4, 0.9, (pop, 1))
        mutant = X[idx[:, 0]] + f * (X[idx[:, 1]] - X[idx[:, 2]]) + 0.3 * f * (best - X[idx[:, 0]])
        cross = rng.random((pop, d)) < 0.9
        cross[np.arange(pop), rng.integers(0, d, pop)] = True
        trial = np.clip(np.where(cross, mutant, X), lo, hi)
        ft = evaluate(trial)
        better = ft <= fx
        X[better], fx[better] = trial[better], ft[better]
        used += pop
        history.append(float(fx.min()))
        if np.isfinite(fx).all() and np.ptp(fx) <= 1e-12 * (1.0 + abs(fx.min())):
            break                                             # the population has collapsed onto one answer
    k = int(fx.argmin())
    flip = -1.0 if objective["kind"] == "max" else 1.0        # report a maximised objective the right way up
    out = {"values": {n: float(x) for n, x in zip(names, X[k])}, "history": [flip * h for h in history],
           "evaluations": used, "score": flip * float(fx[k]), "start": flip * history[0]}
    if objective["kind"] == "fit":
        P = np.tile(base, (1, 1))
        P[0, cols] = X[k]
        sse = model.compiled.run(sim, reducers=reducers, P=P, seed=seed, seed_mod=1).metrics[:, 0]
        out["rmse"] = {r.var: float(np.sqrt(s / np.isfinite(np.array(r.target)).sum())) for r, s in zip(reducers, sse)}
    return out
