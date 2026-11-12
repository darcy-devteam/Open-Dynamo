"""Worked example models that ship with Open-Dynamo.

Each is written with a few helpers so the list stays readable; :func:`examples` returns
them in the same JSON form the editor saves.
"""
from __future__ import annotations


def stock(key, name, x, y, initial, **kw):
    return dict(id=key, kind="stock", name=name, x=x, y=y, initial=str(initial), **kw)


def flow(key, name, x, y, equation, **kw):
    return dict(id=key, kind="flow", name=name, x=x, y=y, equation=equation, **kw)


def var(key, name, x, y, equation, **kw):
    return dict(id=key, kind="variable", name=name, x=x, y=y, equation=equation, **kw)


def par(key, name, x, y, value, lo, hi, **kw):
    return dict(id=key, kind="parameter", name=name, x=x, y=y, value=value, min=lo, max=hi, **kw)


def cloud(key, x, y):
    return dict(id=key, kind="cloud", name="", x=x, y=y)


def note(key, x, y, text, w=230, h=120, color="yellow"):
    return dict(id=key, kind="note", name="", x=x, y=y, w=w, h=h, text=text, color=color)


def model(key, name, description, sim, nodes, pipes, infos, charts, level="Starter"):
    links = [dict(id=f"f{i}", kind="flow", source=a, target=b) for i, (a, b) in enumerate(pipes)]
    for i, spec in enumerate(infos):
        a, b, *sign = spec
        links.append(dict(id=f"i{i}", kind="influence", source=a, target=b, polarity=sign[0] if sign else ""))
    charts = [dict(id=f"c{i}", **c) for i, c in enumerate(charts)]
    return dict(format="open-dynamo/1", id=key, name=name, description=description, level=level, sim=sim,
                nodes=nodes, links=links, charts=charts)


def time(*names):
    return dict(mode="time", vars=list(names))


def phase(x, y):
    return dict(mode="phase", x=x, y=y, vars=[])


def examples() -> list[dict]:
    return [
        model("coffee", "Cooling coffee", "One stock, one flow: Newton's law of cooling. The best place to start.",
              dict(start=0, stop=60, dt=0.25, method="rk4", timeUnit="min"),
              [stock("T", "Temperature", 220, 160, 90, units="°C"),
               flow("cool", "cooling", 470, 172, "(Temperature - room_temperature) / time_constant"),
               cloud("sink", 600, 174),
               par("room", "room temperature", 330, 320, 20, 0, 40, units="°C"),
               par("tau", "time constant", 520, 320, 8, 1, 30, units="min"),
               note("n", 40, 20, "A stock holds a quantity. A flow changes it.\n\nDrag the sliders in Controls and watch "
                                 "the curve move. Double-click a node to rename it; select it to edit its equation.", 300, 110)],
              [("T", "cool"), ("cool", "sink")],
              [("T", "cool", "+"), ("room", "cool", "-"), ("tau", "cool", "-")],
              [time("Temperature")]),
        model("logistic", "Logistic growth", "Growth that slows as a population approaches its carrying capacity.",
              dict(start=0, stop=40, dt=0.1, method="rk4", timeUnit="year"),
              [cloud("src", 90, 174), flow("grow", "net growth", 220, 172,
                                           "growth_rate * Population * (1 - Population / carrying_capacity)"),
               stock("P", "Population", 340, 160, 10),
               par("r", "growth rate", 150, 320, 0.4, 0, 1.5), par("K", "carrying capacity", 330, 320, 1000, 100, 3000)],
              [("src", "grow"), ("grow", "P")],
              [("P", "grow", "+"), ("r", "grow", "+"), ("K", "grow", "+")],
              [time("Population"), time("net_growth")]),
        model("sir", "SIR epidemic", "Susceptible, infected and recovered populations: the classic compartment model.",
              dict(start=0, stop=100, dt=0.25, method="rk4", timeUnit="day"),
              [stock("S", "Susceptible", 80, 160, 990, units="people"),
               flow("inf", "infection", 310, 172, "contact_rate * Susceptible * Infected / Total_population"),
               stock("I", "Infected", 420, 160, 10, units="people"),
               flow("rec", "recovery", 650, 172, "Infected / recovery_time"),
               stock("R", "Recovered", 760, 160, 0, units="people"),
               var("N", "Total population", 400, 40, "Susceptible + Infected + Recovered"),
               par("beta", "contact rate", 230, 320, 0.4, 0, 2, units="1/day"),
               par("tr", "recovery time", 600, 320, 5, 1, 20, units="day"),
               var("R0", "R0", 430, 400, "contact_rate * recovery_time")],
              [("S", "inf"), ("inf", "I"), ("I", "rec"), ("rec", "R")],
              [("S", "inf", "+"), ("I", "inf", "+"), ("N", "inf", "-"), ("beta", "inf", "+"), ("I", "rec", "+"),
               ("tr", "rec", "-"), ("S", "N"), ("I", "N"), ("R", "N"), ("beta", "R0", "+"), ("tr", "R0", "+")],
              [time("Susceptible", "Infected", "Recovered"), time("infection", "recovery")]),
        model("bass", "Bass diffusion", "How a new product spreads: a few innovators, then word of mouth.",
              dict(start=0, stop=20, dt=0.05, method="rk4", timeUnit="year"),
              [stock("P", "Potential adopters", 100, 160, 10000),
               flow("ad", "adoption", 360, 172,
                    "Potential_adopters * (innovation + imitation * Adopters / (Potential_adopters + Adopters))"),
               stock("A", "Adopters", 480, 160, 0),
               par("p", "innovation", 250, 320, 0.03, 0, 0.2), par("q", "imitation", 430, 320, 0.38, 0, 1)],
              [("P", "ad"), ("ad", "A")],
              [("P", "ad", "+"), ("A", "ad", "+"), ("p", "ad", "+"), ("q", "ad", "+")],
              [time("Potential_adopters", "Adopters"), time("adoption")]),
        model("lv", "Predator and prey", "Lotka-Volterra: two populations that drive each other in cycles.",
              dict(start=0, stop=40, dt=0.02, method="rk4", timeUnit="year"),
              [cloud("c1", 60, 114), flow("rb", "rabbit births", 180, 112, "rabbit_birth_rate * Rabbits"),
               stock("Rb", "Rabbits", 300, 100, 100),
               flow("pr", "predation", 540, 112, "predation_rate * Rabbits * Foxes"), cloud("c2", 670, 114),
               cloud("c3", 60, 374), flow("fb", "fox births", 180, 372, "conversion * Rabbits * Foxes"),
               stock("Fx", "Foxes", 300, 360, 20),
               flow("fd", "fox deaths", 540, 372, "fox_death_rate * Foxes"), cloud("c4", 670, 374),
               par("a", "rabbit birth rate", 60, 220, 1.0, 0, 3), par("b", "predation rate", 560, 220, 0.03, 0, 0.1),
               par("d", "conversion", 60, 480, 0.01, 0, 0.05), par("c", "fox death rate", 560, 480, 0.8, 0, 3)],
              [("c1", "rb"), ("rb", "Rb"), ("Rb", "pr"), ("pr", "c2"), ("c3", "fb"), ("fb", "Fx"), ("Fx", "fd"), ("fd", "c4")],
              [("Rb", "rb", "+"), ("a", "rb", "+"), ("Rb", "pr", "+"), ("Fx", "pr", "+"), ("b", "pr", "+"),
               ("Rb", "fb", "+"), ("Fx", "fb", "+"), ("d", "fb", "+"), ("Fx", "fd", "+"), ("c", "fd", "+")],
              [time("Rabbits", "Foxes"), phase("Rabbits", "Foxes")], "Intermediate"),
        model("msd", "Mass, spring and damper", "A second-order mechanical system and its step response.",
              dict(start=0, stop=20, dt=0.01, method="rk4", timeUnit="s"),
              [cloud("c1", 70, 134), flow("acc", "acceleration", 190, 132,
                                          "(Force - damping * Velocity - stiffness * Position) / mass", units="m/s²"),
               stock("V", "Velocity", 310, 120, 0, units="m/s"),
               cloud("c2", 70, 374), flow("dx", "speed", 190, 372, "Velocity", units="m/s"),
               stock("X", "Position", 310, 360, 0, units="m"),
               var("F", "Force", 150, 20, "step(10, 1)", units="N"),
               par("m", "mass", 20, 240, 1, 0.1, 10, units="kg"), par("cc", "damping", 150, 240, 0.5, 0, 10, units="N·s/m"),
               par("k", "stiffness", 530, 240, 20, 1, 100, units="N/m")],
              [("c1", "acc"), ("acc", "V"), ("c2", "dx"), ("dx", "X")],
              [("F", "acc", "+"), ("V", "acc", "-"), ("X", "acc", "-"), ("m", "acc", "-"), ("cc", "acc", "-"),
               ("k", "acc", "-"), ("V", "dx", "+")],
              [time("Position"), phase("Position", "Velocity")], "Intermediate"),
        model("tank", "Tank level control", "A PI controller holds the level of a draining tank at a moving setpoint.",
              dict(start=0, stop=60, dt=0.05, method="rk4", timeUnit="s"),
              [cloud("c1", 180, 214), flow("in", "inflow", 300, 212, "max_inflow * clip(valve, 0, 1)"),
               stock("L", "Level", 420, 200, 0.5, nonNegative=True, units="m"),
               flow("out", "outflow", 660, 212, "outlet * sqrt(max(Level, 0))"), cloud("c2", 790, 214),
               dict(id="sp", kind="table", name="setpoint", x=60, y=40, input="time",
                    points=[[0, 1], [19.9, 1], [20, 1.5], [39.9, 1.5], [40, 0.8], [60, 0.8]], units="m"),
               var("e", "error", 260, 40, "setpoint - Level"),
               cloud("c3", 450, 54), flow("de", "error rate", 560, 52, "error"),
               stock("Ie", "Integral of error", 680, 40, 0),
               var("u", "valve", 210, 330, "Kp * error + Ki * Integral_of_error"),
               par("kp", "Kp", 60, 440, 2, 0, 10), par("ki", "Ki", 200, 440, 0.5, 0, 5),
               par("qmax", "max inflow", 360, 440, 1.5, 0.2, 5), par("ko", "outlet", 660, 330, 0.5, 0.1, 2)],
              [("c1", "in"), ("in", "L"), ("L", "out"), ("out", "c2"), ("c3", "de"), ("de", "Ie")],
              [("sp", "e", "+"), ("L", "e", "-"), ("e", "de", "+"), ("e", "u", "+"), ("Ie", "u", "+"), ("kp", "u", "+"),
               ("ki", "u", "+"), ("u", "in", "+"), ("qmax", "in", "+"), ("L", "out", "+"), ("ko", "out", "+")],
              [time("Level", "setpoint"), time("valve")], "Intermediate"),
        model("supply", "Inventory oscillation", "Delays in a supply line make a stock overshoot when demand steps up.",
              dict(start=0, stop=80, dt=0.125, method="rk4", timeUnit="week"),
              [cloud("c1", 120, 214), flow("prod", "production", 250, 212, "delay3(orders, lead_time)"),
               stock("Inv", "Inventory", 380, 200, 200),
               flow("ship", "shipments", 620, 212, "demand"), cloud("c2", 750, 214),
               var("dem", "demand", 600, 60, "100 + step(20, 5)"),
               var("exp", "expected demand", 380, 60, "smooth(demand, 4)"),
               var("ord", "orders", 140, 60, "max(0, expected_demand + (cover * expected_demand - Inventory) / adjust_time)"),
               par("lt", "lead time", 60, 340, 6, 1, 16), par("at", "adjust time", 220, 340, 3, 1, 12),
               par("cov", "cover", 380, 340, 2, 0.5, 6)],
              [("c1", "prod"), ("prod", "Inv"), ("Inv", "ship"), ("ship", "c2")],
              [("dem", "ship", "+"), ("dem", "exp", "+"), ("exp", "ord", "+"), ("Inv", "ord", "-"), ("ord", "prod", "+"),
               ("lt", "prod", "-"), ("at", "ord", "-"), ("cov", "ord", "+")],
              [time("Inventory"), time("demand", "production", "orders")], "Advanced"),
        model("lorenz", "Lorenz attractor", "Three coupled equations and deterministic chaos. Try the phase plot.",
              dict(start=0, stop=40, dt=0.005, method="rk4", timeUnit=""),
              [cloud("c1", 70, 74), flow("dx", "dx", 190, 72, "sigma * (y - x)"), stock("x", "x", 310, 60, 1),
               cloud("c2", 70, 234), flow("dy", "dy", 190, 232, "x * (rho - z) - y"), stock("y", "y", 310, 220, 1),
               cloud("c3", 70, 394), flow("dz", "dz", 190, 392, "x * y - beta * z"), stock("z", "z", 310, 380, 1),
               par("s", "sigma", 560, 70, 10, 0, 30), par("r", "rho", 560, 230, 28, 0, 60), par("b", "beta", 560, 390, 2.667, 0, 8)],
              [("c1", "dx"), ("dx", "x"), ("c2", "dy"), ("dy", "y"), ("c3", "dz"), ("dz", "z")],
              [("x", "dx"), ("y", "dx"), ("s", "dx"), ("x", "dy"), ("y", "dy"), ("z", "dy"), ("r", "dy"), ("x", "dz"),
               ("y", "dz"), ("z", "dz"), ("b", "dz")],
              [phase("x", "z"), time("x")], "Advanced"),
    ]
