import json
import shutil
import subprocess

import numpy as np
import pytest

from opendynamo import Model, ModelError, Reducer, Sim, System, build
from opendynamo import analysis as A
from opendynamo.examples import examples

EX = {m["id"]: m for m in examples()}


def tiny(nodes, links=(), **sim):
    return dict(name="t", sim={"start": 0, "stop": 10, "dt": 0.1, "method": "rk4", **sim}, nodes=nodes, links=list(links))


def decay(method="rk4", dt=0.1):
    return tiny([dict(id="x", kind="stock", name="X", initial="100"),
                 dict(id="f", kind="flow", name="loss", equation="X / tau"),
                 dict(id="p", kind="parameter", name="tau", value=2.0)],
                [dict(id="l", kind="flow", source="x", target="f")], method=method, dt=dt)


def close(a, b, tol=1e-9):
    return np.nanmax(np.abs(a - b) / (1 + np.abs(b))) < tol


def test_rk4_matches_the_analytic_solution_and_beats_euler():
    exact = lambda t: 100 * np.exp(-t / 2)   # noqa: E731
    rk, eu = Model(decay("rk4")).simulate(), Model(decay("euler")).simulate()
    assert np.abs(rk["X"][:, 0] - exact(rk.t)).max() < 1e-4
    assert 0.1 < np.abs(eu["X"][:, 0] - exact(eu.t)).max() < 2
    assert rk.t[0] == 0 and rk.t[-1] == pytest.approx(10) and len(rk.t) == 101


@pytest.mark.parametrize("key", [k for k in EX if k != "lorenz"])
@pytest.mark.parametrize("method", ["euler", "rk4"])
def test_native_and_numpy_backends_agree(key, method):
    m = Model(EX[key])
    sim = m.with_sim(method=method)
    a, b = m.simulate(sim=sim), m.simulate(sim=sim, backend="numpy")
    for n in m.variables:
        assert close(a[n], b[n]), n
        assert np.isfinite(a[n]).all(), n


def test_chaotic_example_agrees_before_rounding_differences_grow():
    m = Model(EX["lorenz"])
    sim = m.with_sim(stop=5)
    a, b = m.simulate(sim=sim), m.simulate(sim=sim, backend="numpy")
    assert close(a["x"], b["x"], 1e-6)


def test_overrides_batches_and_thinned_output():
    m = Model(EX["sir"])
    run = m.simulate({"contact_rate": np.array([0.2, 0.4, 0.8])}, record=["Infected"], sim=m.with_sim(save_every=4))
    peak = run["Infected"].max(0)
    assert run["Infected"].shape == (101, 3) and peak[0] < peak[1] < peak[2]
    total = m.simulate(record=["Total_population"])["Total_population"]
    assert np.allclose(total, 1000)                                  # flows conserve people
    with pytest.raises(KeyError):
        m.simulate({"nope": 1})


def test_reducers_match_numpy():
    m = Model(EX["lv"])
    data = tuple(np.linspace(80, 120, m.sim.steps + 1))
    reds = [Reducer("Rabbits", k) for k in ("last", "first", "min", "max", "mean", "integral", "sum")]
    reds += [Reducer("Foxes", "max", i0=100, i1=900), Reducer("Rabbits", "sse", target=data)]
    ov = {"predation_rate": np.linspace(0.02, 0.04, 7)}
    a = m.compiled.run(m.sim, ov, reducers=reds).metrics
    b = m.compiled.run(m.sim, ov, reducers=reds, backend="numpy").metrics
    assert a.shape == (9, 7) and np.allclose(a, b, rtol=1e-9)
    full = m.simulate(ov, record=["Rabbits"])["Rabbits"]
    assert np.allclose(a[3], full.max(0)) and np.allclose(a[0], full[-1])


def test_any_node_can_link_to_any_node():
    # a stock-to-stock flow with its own rate, a variable feeding a stock directly, and a link to a note
    spec = tiny([dict(id="a", kind="stock", name="A", initial="10"), dict(id="b", kind="stock", name="B", initial="0"),
                 dict(id="v", kind="variable", name="top up", equation="1"), dict(id="n", kind="note", text="hello"),
                 dict(id="k", kind="parameter", name="k", value=0.5)],
                [dict(id="l1", kind="flow", source="a", target="b", rate="k * A"),
                 dict(id="l2", kind="flow", source="v", target="a"),
                 dict(id="l3", kind="relates to", source="n", target="a"),
                 dict(id="l4", kind="influence", source="k", target="b")])
    built = build(spec)
    assert built.ok
    assert [d["link"] for d in built.diagnostics if d["level"] == "warning"] == ["l4"]     # B's maths never uses k
    m = Model(spec)
    run = m.simulate()
    assert np.allclose(run["A"] + run["B"], 10 + run.t[:, None])       # only the top-up adds material
    assert "flow__1" in m.variables                                    # the link's own rate can be plotted


def test_problems_are_reported_on_the_node_that_has_them():
    spec = tiny([dict(id="a", kind="stock", name="A", initial="1"), dict(id="b", kind="stock", name="B", initial="1"),
                 dict(id="v", kind="variable", name="v", equation="w + 1"), dict(id="w", kind="variable", name="w", equation="v * 2"),
                 dict(id="u", kind="variable", name="u", equation="missing + sin(")],
                [dict(id="l", kind="flow", source="a", target="b")])
    d = build(spec).diagnostics
    assert any(x["link"] == "l" and "needs a rate" in x["message"] for x in d)
    assert any(x["node"] == "u" and "syntax" in x["message"] for x in d)
    spec["links"], spec["nodes"][4]["equation"] = [], "missing * 2"
    d = build(spec).diagnostics
    assert any(x["node"] == "u" and "unknown name 'missing'" in x["message"] for x in d)
    spec["nodes"][4]["equation"] = "1"
    d = build(spec).diagnostics
    assert {x["node"] for x in d if "circular" in x["message"]} == {"v", "w"}
    dup = tiny([dict(id="a", kind="variable", name="rate x", equation="1"), dict(id="b", kind="parameter", name="rate_x", value=1),
                dict(id="c", kind="variable", name="time", equation="1"), dict(id="e", kind="variable", name="e", equation="")])
    msgs = {x["node"]: x["message"] for x in build(dup).diagnostics}
    assert "two nodes" in msgs["a"] and "two nodes" in msgs["b"] and "built-in" in msgs["c"] and "no equation" in msgs["e"]
    with pytest.raises(ModelError):
        Model(dup)


def test_expression_language():
    def value(eq, **sim):
        m = Model(tiny([dict(id="v", kind="variable", name="v", equation=eq)], **sim))
        a, b = m.simulate(), m.simulate(backend="numpy")
        assert close(a["v"], b["v"]), eq
        return a["v"][:, 0]

    assert np.allclose(value("2^3 + 10 % 4 + (1 if time >= 5 else 0)")[[0, -1]], [10, 11])
    assert np.allclose(value("step(3, 2) + ramp(2, 4, 6) + pulse(8, 1)")[[0, 20, 50, 60, 85, 100]], [0, 3, 5, 7, 8, 7])
    assert np.allclose(value("pulse(1, 0.5, 2)")[[0, 10, 14, 15, 30, 34, 35]], [0, 1, 1, 0, 1, 1, 0])
    assert np.allclose(value("lookup(time, [0, 5, 10], [0, 10, 0])")[[0, 25, 50, 100]], [0, 5, 10, 0])
    assert np.allclose(value("min(3, 2, 1) + max(1, 2) + clip(time, 2, 3) + abs(-1) + sign(-4) + div(1, 0)")[0], 5)
    assert np.allclose(value("sin(pi / 2) + cos(0) + atan2(1, 1) + sqrt(16) + exp(0) + log(1) + round(2.5) + floor(-0.5)")[0],
                       2 + np.pi / 4 + 4 + 1 + 3 - 1)
    assert np.allclose(value("(time > 2 and time < 4) + (not time > 2) + (time < 1 or time > 9)")[[0, 30, 95]], [2, 1, 1])
    assert np.allclose(value("dt * 10"), 1)
    noise = value("randn()", stop=500)
    assert abs(noise.mean()) < 0.1 and 0.9 < noise.std() < 1.1 and 0 < value("rand()").min() < value("rand()").max() < 1
    for bad in ("__import__('os')", "v.real", "x[0]", "open('f')", "lambda: 1", "'text'", "sin"):
        assert not build(tiny([dict(id="v", kind="variable", name="v", equation=bad)])).ok, bad


def test_delays_and_smoothing():
    spec = tiny([dict(id="i", kind="variable", name="signal", equation="step(1, 2)"),
                 dict(id="d", kind="variable", name="late", equation="delay(signal, 3)"),
                 dict(id="e", kind="variable", name="late2", equation="delay(signal * 2, lag, 7)"),
                 dict(id="s", kind="variable", name="smoothed", equation="smooth(signal, 1.5)"),
                 dict(id="m", kind="variable", name="material", equation="delay3(signal, 2)"),
                 dict(id="p", kind="parameter", name="lag", value=1)], stop=30)
    m = Model(spec)
    a, b = m.simulate(), m.simulate(backend="numpy")
    for n in m.variables:
        assert close(a[n], b[n]), n
    t = a.t
    late = a["late"][:, 0]
    assert late[t < 4.95].max() < 1e-9 and np.allclose(late[t > 5.15], 1)
    assert a["late2"][0, 0] == 7 and np.allclose(a["late2"][t > 3.2, 0], 2)
    assert np.allclose(a["smoothed"][:, 0], np.where(t < 2, 0, 1 - np.exp(-(t - 2) / 1.5)), atol=0.03)   # the step lands mid-RK4-stage
    assert a["material"][-1, 0] == pytest.approx(1, abs=1e-4) and a["material"][t < 2].max() == 0
    assert set(m.variables) == {"signal", "late", "late2", "smoothed", "material"}        # helper stocks stay hidden


def test_initial_values_may_depend_on_other_variables():
    spec = tiny([dict(id="a", kind="stock", name="A", initial="total - B"), dict(id="b", kind="stock", name="B", initial="25"),
                 dict(id="t", kind="variable", name="total", equation="scale * 100"),
                 dict(id="s", kind="parameter", name="scale", value=2)])
    run = Model(spec).simulate({"B__init": 40})
    assert run["A"][0, 0] == 160
    spec["nodes"][1]["initial"] = "A / 2"
    assert any("circle" in d["message"] for d in build(spec).diagnostics)


def test_non_negative_stock_and_a_plain_system():
    s = System()
    s.param("k", 3.0)
    s.stock("x", 1.0, "-k", lo=0.0)
    run = s.compile().run(Sim(0, 2, 0.1, "euler"), record=["x"])
    assert run["x"].min() == 0 and run["x"][0, 0] == 1


def test_wasm_kernel_matches_native():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    import tempfile
    from pathlib import Path
    m = Model(EX["tank"])
    c = m.compiled
    rec = ["Level", "valve", "Integral_of_error"]
    cfg, ix, _ = c._pack(m.sim, rec, [], 0, 0)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "k.wasm").write_bytes(c.wasm())
        (Path(d) / "job.json").write_text(json.dumps({"cfg": cfg.tolist(), "params": c.defaults.tolist(), "rec": ix[:3].tolist()}))
        res = subprocess.run([node, str(Path(__file__).parent / "run_wasm.mjs"), str(Path(d) / "k.wasm"), str(Path(d) / "job.json")],
                             capture_output=True, text=True, check=True)
    got = json.loads(res.stdout)
    ref = m.simulate(record=rec)
    assert got["code"] == 0
    for i, n in enumerate(rec):
        assert close(np.array(got["out"][i]), ref[n][:, 0], 1e-9), n


def test_sweep_and_monte_carlo():
    m = Model(EX["sir"])
    sw = A.sweep(m, "contact_rate", np.linspace(0.2, 1.0, 5), ["Infected"])
    assert sw["series"]["Infected"].shape == (5, len(sw["t"])) and np.all(np.diff(sw["series"]["Infected"].max(1)) > 0)
    mc = A.monte_carlo(m, [dict(param="contact_rate", dist="uniform", a=0.3, b=0.5),
                           dict(param="recovery_time", dist="normal", a=5, b=0.5)], 400, ["Infected"])
    band = mc["bands"]["Infected"]
    assert np.all(band["p5"] <= band["p50"] + 1e-9) and np.all(band["p50"] <= band["p95"] + 1e-9)
    assert band["p95"].max() > band["p5"].max() and mc["final"]["Infected"]["counts"].sum() == 400


def test_fitting_recovers_parameters_and_optimiser_finds_a_maximum():
    m = Model(EX["sir"])
    truth = {"contact_rate": 0.62, "recovery_time": 7.5}
    obs = m.simulate(truth, record=["Infected"], sim=m.with_sim(save_every=8))
    fit = A.optimise(m, [dict(param="contact_rate", min=0.1, max=1.5), dict(param="recovery_time", min=2, max=15)],
                     dict(kind="fit", targets=[dict(var="Infected", t=obs.t.tolist(), y=obs["Infected"][:, 0].tolist())]),
                     evaluations=4000)
    assert fit["values"]["contact_rate"] == pytest.approx(0.62, abs=0.01)
    assert fit["values"]["recovery_time"] == pytest.approx(7.5, abs=0.1)
    assert fit["history"][-1] < fit["history"][0] and fit["rmse"]["Infected"] < 1
    # the damping that makes a mass-spring settle fastest: minimise the integral of squared error
    spec = json.loads(json.dumps(EX["msd"]))
    spec["nodes"].append(dict(id="ise", kind="stock", name="ISE", initial="0"))
    spec["nodes"].append(dict(id="se", kind="flow", name="sq error", equation="(Position - 0.5)^2 * step(1, 1)"))
    spec["links"].append(dict(id="x1", kind="flow", source="se", target="ise"))
    best = A.optimise(Model(spec), [dict(param="damping", min=0, max=10)], dict(kind="min", var="ISE", agg="last"), evaluations=1500)
    assert 4 < best["values"]["damping"] < 9 and best["score"] < best["start"]
