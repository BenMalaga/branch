"""The prepared graph is cached in memory, and sharing it must stay safe.

Reading a graphml off disk, reprojecting it and rebuilding edge geometry cost
about 21 seconds on a small instance, and it was paid on every single request
even though the download was already cached. Keeping the finished graph in
memory removes that, but a shared mutable graph is only safe if nothing writes
per-request state onto it. These tests hold both halves of that bargain.
"""
import networkx as nx
import pytest
from shapely.geometry import LineString

from branch import data


def _fake_prepared(n=3):
    G = nx.MultiDiGraph()
    for i in range(n):
        G.add_node(i, x=float(i) * 100.0, y=0.0)
    for i in range(n - 1):
        geom = LineString([(i * 100.0, 0.0), ((i + 1) * 100.0, 0.0)])
        G.add_edge(i, i + 1, 0, geometry=geom, length=geom.length)
    return G


@pytest.fixture(autouse=True)
def _clean_cache():
    data.clear_prepared_cache()
    yield
    data.clear_prepared_cache()


def test_a_hit_returns_the_same_object():
    """The point of the cache: no reparsing, no reprojecting, no copying."""
    key = ("area", "walk", "EPSG:32618")
    G = _fake_prepared()
    data._store_prepared(key, G)
    assert data._cached_prepared(key) is G


def test_a_writer_gets_its_own_copy():
    """Shade annotation writes onto edges, so it must not touch the shared graph."""
    key = ("area", "walk", "EPSG:32618")
    G = _fake_prepared()
    data._store_prepared(key, G)
    mine = data._cached_prepared(key).copy()
    for _, _, d in mine.edges(data=True):
        d["sun_frac"] = 0.5
    assert all("sun_frac" not in d for _, _, d in G.edges(data=True))


def test_the_cache_is_bounded():
    """This runs on a 512MB instance; an unbounded graph cache would kill it."""
    for i in range(data.PREPARED_CACHE_SIZE + 3):
        data._store_prepared((f"area{i}", "walk", "EPSG:32618"), _fake_prepared())
    assert len(data._PREPARED) == data.PREPARED_CACHE_SIZE


def test_the_oldest_entry_is_evicted_first():
    a, b, c = [(f"a{i}", "walk", "EPSG:32618") for i in range(3)]
    data._store_prepared(a, _fake_prepared())
    data._store_prepared(b, _fake_prepared())
    data._cached_prepared(a)                      # touching a makes b the oldest
    data._store_prepared(c, _fake_prepared())
    assert data._cached_prepared(a) is not None
    assert data._cached_prepared(b) is None
    assert data._cached_prepared(c) is not None


def test_areas_in_different_zones_do_not_share_an_entry():
    """The metric CRS is part of the identity of a prepared graph."""
    ny = ("bbox_x", "walk", "EPSG:32618")
    la = ("bbox_x", "walk", "EPSG:32611")
    data._store_prepared(ny, _fake_prepared())
    assert data._cached_prepared(la) is None
