"""Shade-annotation tests on a hand-built graph (no network)."""
import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, Point

from branch import config, shade


def _tiny_graph():
    """A single 100 m east-west edge from node 0 to node 1."""
    G = nx.MultiDiGraph()
    G.add_node(0, x=0.0, y=0.0)
    G.add_node(1, x=100.0, y=0.0)
    geom = LineString([(0, 0), (100, 0)])
    G.add_edge(0, 1, 0, geometry=geom, length=geom.length)
    return G


def _trees(points, dbh=30.0):
    return gpd.GeoDataFrame({"dbh": [dbh] * len(points)},
                            geometry=[Point(*p) for p in points],
                            crs=config.METRIC_CRS)


def test_no_trees_means_full_sun():
    G = _tiny_graph()
    empty = gpd.GeoDataFrame({"crown_r": []}, geometry=[], crs=config.METRIC_CRS)
    shade.annotate_edges(G, empty)
    assert G.edges[0, 1, 0]["sun_frac"] == 1.0


def test_tree_on_segment_creates_shade():
    G = _tiny_graph()
    shadows = shade.compute_shadows(_trees([(50, 0)]), altitude_deg=45.0, azimuth_deg=180.0)
    assert len(shadows) == 1
    shade.annotate_edges(G, shadows)
    d = G.edges[0, 1, 0]
    assert 0.0 <= d["sun_frac"] < 1.0
    assert d["shade_len"] > 0.0


def test_sun_fraction_always_in_unit_interval():
    G = _tiny_graph()
    shadows = shade.compute_shadows(_trees([(20, 0), (50, 0), (80, 0)]),
                                    altitude_deg=30.0, azimuth_deg=210.0)
    shade.annotate_edges(G, shadows)
    assert 0.0 <= G.edges[0, 1, 0]["sun_frac"] <= 1.0


def test_no_shadows_at_night():
    shadows = shade.compute_shadows(_trees([(50, 0)]), altitude_deg=-5.0, azimuth_deg=180.0)
    assert len(shadows) == 0
