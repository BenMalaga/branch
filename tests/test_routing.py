"""Routing tests on a hand-built two-path graph (no network).

Node 0 -> node 3 either by a short SUNNY direct edge, or a slightly longer
SHADED detour through node 2. The router should take the direct edge when shade
is ignored, and detour for shade when shade aversion is high.
"""
import networkx as nx
from shapely.geometry import LineString

from branch import routing


def _graph():
    G = nx.MultiDiGraph()
    for n, (x, y) in {0: (0, 0), 2: (50, -15), 3: (100, 0)}.items():
        G.add_node(n, x=x, y=y)

    def add(u, v, coords, sun):
        g = LineString(coords)
        G.add_edge(u, v, 0, geometry=g, length=g.length, sun_frac=sun)
        rg = LineString(coords[::-1])
        G.add_edge(v, u, 0, geometry=rg, length=rg.length, sun_frac=sun)

    add(0, 3, [(0, 0), (100, 0)], 1.0)                 # short, full sun
    add(0, 2, [(0, 0), (50, -15)], 0.0)                # detour, fully shaded
    add(2, 3, [(50, -15), (100, 0)], 0.0)
    return G


def test_alpha_zero_takes_the_shortest_path():
    G = _graph()
    routing.set_weights(G, alpha=0.0)
    r = routing.route(G, 0, 3, "w_cool")  # alpha 0 -> cool cost == length
    assert abs(r["length_m"] - 100.0) < 1e-6
    assert r["sun_frac"] == 1.0


def test_high_alpha_detours_for_shade():
    G = _graph()
    routing.set_weights(G, alpha=5.0)
    fast = routing.route(G, 0, 3, "w_fast")
    cool = routing.route(G, 0, 3, "w_cool")
    assert fast["sun_m"] > cool["sun_m"]
    assert cool["length_m"] > fast["length_m"]
    assert cool["sun_frac"] < fast["sun_frac"]


def test_route_reports_consistent_distance():
    G = _graph()
    routing.set_weights(G, alpha=2.0)
    r = routing.route(G, 0, 3, "w_fast")
    assert r["sun_m"] <= r["length_m"] + 1e-6
    assert r["shade_m"] >= 0.0
