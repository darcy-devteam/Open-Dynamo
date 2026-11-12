"""HTTP server: compiles diagrams, runs the experiments a browser should not, and serves the web app.

The server keeps no user data. Models live in the student's browser (and in files they
export); every request carries the model it is about.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, analysis
from .examples import examples
from .expr import FUNCTIONS
from .kernel import Sim, n_threads, warm
from .model import Built, Model, ModelError, ModelSpec, build

STATIC = Path(os.environ.get("OPENDYNAMO_STATIC", Path(__file__).parent / "static"))
MAX_WORK = float(os.environ.get("OPENDYNAMO_MAX_WORK", 5e9))         # equations x steps x scenarios per request
MAX_STEPS = int(os.environ.get("OPENDYNAMO_MAX_STEPS", 2_000_000))
_jobs = threading.Semaphore(int(os.environ.get("OPENDYNAMO_JOBS", "2")))    # heavy experiments at once
_cache: "OrderedDict[str, Model]" = OrderedDict()
_cache_lock = threading.Lock()


def clean(x):
    """numpy -> JSON-safe Python (NaN and infinity become null)."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.ndarray):
        if x.dtype.kind == "f":
            return np.where(np.isfinite(x), x, None).tolist() if not np.isfinite(x).all() else x.tolist()
        return x.tolist()
    if isinstance(x, (np.floating, float)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    return x


class Request(BaseModel):
    model: ModelSpec
    params: dict[str, float] = Field(default_factory=dict)


class SimulateRequest(Request):
    record: list[str] | None = None
    maxPoints: int = 2000


class SweepRequest(Request):
    param: str
    values: list[float] = Field(min_length=1, max_length=200)
    record: list[str] = Field(min_length=1, max_length=12)


class MonteCarloRequest(Request):
    uncertain: list[dict] = Field(default_factory=list, max_length=50)
    n: int = Field(500, ge=2, le=20000)
    record: list[str] = Field(min_length=1, max_length=12)
    seed: int = 1


class OptimiseRequest(Request):
    decisions: list[dict] = Field(min_length=1, max_length=40)
    objective: dict
    evaluations: int = Field(6000, ge=50, le=200000)
    seed: int = 1


def load(spec: ModelSpec) -> Model:
    """Compile (or fetch from cache) the model in a request; a broken model is a 422 with diagnostics."""
    math = [[n.id, n.kind, n.name, n.equation, str(n.initial), n.value, n.input, n.points, n.nonNegative]
            for n in spec.nodes] + [[l.kind, l.source, l.target, l.rate] for l in spec.links]
    key = hashlib.sha1(json.dumps([math, spec.sim.model_dump()], default=str).encode()).hexdigest()
    with _cache_lock:
        model = _cache.get(key)
        if model is not None:
            _cache.move_to_end(key)
            return model
    try:
        model = Model(spec)
    except ModelError as e:
        raise HTTPException(422, detail={"message": str(e), "diagnostics": e.diagnostics}) from None
    with _cache_lock:
        _cache[key] = model
        while len(_cache) > 256:
            _cache.popitem(last=False)
    return model


def budget(model: Model, sim: Sim, scenarios: int) -> None:
    sim.check()
    if sim.steps > MAX_STEPS:
        raise HTTPException(422, detail={"message": f"{sim.steps:,} time steps is over this server's limit of "
                                                    f"{MAX_STEPS:,}; use a larger time step or a shorter run"})
    work = float(model.size) * sim.steps * scenarios
    if work > MAX_WORK:
        raise HTTPException(422, detail={"message": f"this experiment is about {work / MAX_WORK:.0f}x larger than "
                                                    "this server allows; use fewer runs, a shorter run or a larger time step"})


def checked(model: Model, names: list[str], params: dict) -> None:
    bad = [n for n in names if n not in model.compiled.slots] + [p for p in params if p not in model.parameters]
    if bad:
        raise HTTPException(422, detail={"message": f"unknown variable or parameter: {', '.join(bad[:5])}"})


def create_app(warm_up: bool = True) -> FastAPI:
    app = FastAPI(title="Open-Dynamo", version=__version__)
    if warm_up:                                           # so the first student does not wait for the compiler
        threading.Thread(target=warm, daemon=True).start()

    @app.exception_handler(ValueError)
    async def _value_error(_, exc):                       # bad experiment settings -> a readable 422
        return JSONResponse({"detail": {"message": str(exc)}}, status_code=422)

    @app.get("/api/info")
    def info():
        return {"version": __version__, "threads": n_threads(), "maxSteps": MAX_STEPS, "maxWork": MAX_WORK,
                "functions": FUNCTIONS + ["smooth", "delay1", "delay3"]}

    @app.get("/api/examples")
    def list_examples():
        return [{k: m[k] for k in ("id", "name", "description", "level")} for m in examples()]

    @app.get("/api/examples/{key}")
    def get_example(key: str):
        for m in examples():
            if m["id"] == key:
                return m
        raise HTTPException(404, "no such example")

    @app.post("/api/compile")
    def compile_model(spec: ModelSpec):
        """Check a diagram and return its kernel as WebAssembly for the browser to run."""
        built: Built = build(spec)
        out = {"ok": built.ok, "diagnostics": built.diagnostics, "names": built.names, "uses": built.uses,
               "params": built.params, "variables": built.variables, "wasm": None, "digest": None, "size": 0}
        if built.ok:
            c = built.compiled
            out.update(wasm=base64.b64encode(c.wasm()).decode(), digest=c.digest, size=c.NA + 2 * c.NS, np=c.NP)
        return clean(out)

    @app.post("/api/simulate")
    def simulate(req: SimulateRequest):
        """Run once on the server (the browser normally does this itself with the WebAssembly kernel)."""
        model = load(req.model)
        record = model.variables if req.record is None else req.record
        checked(model, record, req.params)
        sim = model.with_sim(save_every=max(1, -(-model.sim.steps // max(2, req.maxPoints - 1))))
        budget(model, sim, 1)
        run = model.simulate(req.params, record=record, sim=sim)
        return clean({"t": run.t, "series": {n: run[n][:, 0] for n in record}})

    @app.post("/api/sweep")
    def sweep(req: SweepRequest):
        model = load(req.model)
        checked(model, req.record, {**req.params, req.param: 0})
        budget(model, model.sim, len(req.values))
        with _jobs:
            return clean(analysis.sweep(model, req.param, req.values, req.record, overrides=req.params))

    @app.post("/api/montecarlo")
    def montecarlo(req: MonteCarloRequest):
        model = load(req.model)
        checked(model, req.record, {**req.params, **{u.get("param", "?"): 0 for u in req.uncertain}})
        budget(model, model.sim, req.n)
        with _jobs:
            return clean(analysis.monte_carlo(model, req.uncertain, req.n, req.record, overrides=req.params, seed=req.seed))

    @app.post("/api/optimise")
    def optimise(req: OptimiseRequest):
        model = load(req.model)
        obj = req.objective
        names = [t.get("var", "?") for t in obj.get("targets", [])] if obj.get("kind") == "fit" else [obj.get("var", "?")]
        if obj.get("kind") not in ("fit", "min", "max") or not names:
            raise HTTPException(422, detail={"message": "the objective must be a fit, a minimum or a maximum"})
        checked(model, names, {**req.params, **{d.get("param", "?"): 0 for d in req.decisions}})
        budget(model, model.sim, req.evaluations)
        with _jobs:
            return clean(analysis.optimise(model, req.decisions, obj, overrides=req.params,
                                           evaluations=req.evaluations, seed=req.seed))

    if (STATIC / "index.html").exists():
        if (STATIC / "assets").exists():
            app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def web(path: str):
            file = (STATIC / path).resolve()
            if path and file.is_file() and STATIC.resolve() in file.parents:
                return FileResponse(file)
            return FileResponse(STATIC / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_web():
            return JSONResponse({"message": "The web app has not been built. Run `npm install && npm run build` in web/, "
                                            "or use the API under /api (see /docs)."})
    return app
