import base64

import pytest
from fastapi.testclient import TestClient

from opendynamo.server import create_app


@pytest.fixture(scope="module")
def api():
    return TestClient(create_app(warm_up=False))


def test_examples_compile_to_wasm(api):
    listing = api.get("/api/examples").json()
    assert len(listing) >= 8 and {"id", "name", "description", "level"} <= set(listing[0])
    for item in listing:
        model = api.get(f"/api/examples/{item['id']}").json()
        out = api.post("/api/compile", json=model).json()
        assert out["ok"], (item["id"], out["diagnostics"])
        assert not [d for d in out["diagnostics"] if d["level"] == "error"]
        assert base64.b64decode(out["wasm"])[:4] == b"\0asm"
        assert {v["name"] for v in out["variables"]} and all("slot" in p for p in out["params"])
    assert api.get("/api/examples/nope").status_code == 404
    assert "smooth" in api.get("/api/info").json()["functions"]


def test_compile_reports_problems_without_failing(api):
    model = api.get("/api/examples/coffee").json()
    model["nodes"][1]["equation"] = "Temperature / nothing"
    out = api.post("/api/compile", json=model).json()
    assert not out["ok"] and out["wasm"] is None
    assert out["diagnostics"][0]["node"] == "cool" and "nothing" in out["diagnostics"][0]["message"]
    assert api.post("/api/simulate", json={"model": model}).status_code == 422


def test_simulate_sweep_montecarlo_optimise(api):
    model = api.get("/api/examples/coffee").json()
    run = api.post("/api/simulate", json={"model": model, "params": {"time_constant": 4}, "maxPoints": 50}).json()
    assert len(run["t"]) <= 50 and run["series"]["Temperature"][0] == 90 and run["series"]["Temperature"][-1] < 21
    sw = api.post("/api/sweep", json={"model": model, "param": "time_constant", "values": [2, 8, 20],
                                      "record": ["Temperature"]}).json()
    ends = [row[-1] for row in sw["series"]["Temperature"]]
    assert ends[0] < ends[1] < ends[2]
    mc = api.post("/api/montecarlo", json={"model": model, "n": 200, "record": ["Temperature"], "uncertain": [
        {"param": "room_temperature", "dist": "uniform", "a": 15, "b": 25}]}).json()
    assert 15 < mc["final"]["Temperature"]["mean"] < 26 and len(mc["bands"]["Temperature"]["p50"]) == len(mc["t"])
    data = api.post("/api/simulate", json={"model": model, "params": {"time_constant": 12.5}, "maxPoints": 30}).json()
    fit = api.post("/api/optimise", json={"model": model, "decisions": [{"param": "time_constant", "min": 1, "max": 30}],
                                          "objective": {"kind": "fit", "targets": [
                                              {"var": "Temperature", "t": data["t"], "y": data["series"]["Temperature"]}]},
                                          "evaluations": 1500}).json()
    assert fit["values"]["time_constant"] == pytest.approx(12.5, abs=0.05)


def test_limits_and_bad_requests_are_explained(api):
    model = api.get("/api/examples/coffee").json()
    big = {**model, "sim": {**model["sim"], "dt": 1e-7}}
    r = api.post("/api/simulate", json={"model": big})
    assert r.status_code == 422 and "limit" in r.json()["detail"]["message"]
    r = api.post("/api/sweep", json={"model": model, "param": "nope", "values": [1], "record": ["Temperature"]})
    assert r.status_code == 422 and "nope" in r.json()["detail"]["message"]
    r = api.post("/api/optimise", json={"model": model, "decisions": [{"param": "time_constant", "min": 5, "max": 1}],
                                        "objective": {"kind": "min", "var": "Temperature"}})
    assert r.status_code == 422 and "min < max" in r.json()["detail"]["message"]
