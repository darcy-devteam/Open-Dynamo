"""Command line: ``opendynamo serve`` runs the web app; ``opendynamo run model.json`` simulates a saved model."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="opendynamo", description="Open-Dynamo: dynamical systems modelling")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app and API")
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to accept connections from other machines")
    s.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("run", help="simulate a saved model and print where each variable ends up")
    r.add_argument("model", type=Path)
    r.add_argument("--set", action="append", default=[], metavar="PARAM=VALUE")
    r.add_argument("--csv", type=Path, help="write the full trajectories to this file")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("opendynamo.server:create_app", factory=True, host=args.host, port=args.port)
        return 0

    from .model import Model, ModelError
    try:
        model = Model(json.loads(args.model.read_text(encoding="utf-8")))
    except ModelError as e:
        for d in e.diagnostics:
            print(f"{d['level']}: {d['message']}", file=sys.stderr)
        return 1
    overrides = {k: float(v) for k, v in (item.split("=", 1) for item in args.set)}
    run = model.simulate(overrides)
    width = max(len(n) for n in model.variables)
    print(f"{model.spec.name}: t = {run.t[0]:g} to {run.t[-1]:g}, {len(run.t)} points")
    for n in model.variables:
        x = run[n][:, 0]
        print(f"  {n:<{width}}  start {x[0]:>12.6g}  end {x[-1]:>12.6g}  min {x.min():>12.6g}  max {x.max():>12.6g}")
    if args.csv:
        import numpy as np
        np.savetxt(args.csv, np.column_stack([run.t] + [run[n][:, 0] for n in model.variables]), delimiter=",",
                   header=",".join(["time"] + model.variables), comments="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
