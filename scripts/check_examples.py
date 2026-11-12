"""Quick manual check: every example on both backends, plus build timings. Run: uv run python tests/smoke.py"""
import time

import numpy as np

from opendynamo import Model
from opendynamo.examples import examples

for spec in examples():
    t = time.perf_counter()
    m = Model(spec)
    t_build = time.perf_counter() - t
    warn = [d["message"] for d in m.built.diagnostics]
    t = time.perf_counter()
    a = m.simulate()
    t_native = time.perf_counter() - t
    b = m.simulate(backend="numpy")
    worst = max(np.nanmax(np.abs(a[n] - b[n]) / (1 + np.abs(b[n]))) for n in m.variables)
    t = time.perf_counter()
    wasm = m.compiled.wasm()
    t_wasm = time.perf_counter() - t
    t = time.perf_counter()
    m.simulate()
    t_run = time.perf_counter() - t
    print(f"{spec['id']:9s} eq={m.size:3d} steps={m.sim.steps:5d} build={t_build * 1e3:5.1f}ms native(first)={t_native:5.2f}s "
          f"run={t_run * 1e3:6.2f}ms wasm={t_wasm:5.2f}s/{len(wasm) / 1024:.0f}KB  maxdiff={worst:.1e}  warnings={warn}")
