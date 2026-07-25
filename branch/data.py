"""Fetch and cache the two public datasets branch needs.

* The **walkable street network** from OpenStreetMap (via osmnx / Overpass).
* The **street trees** from the NYC 2015 Street Tree Census (Socrata API).

Both are downloaded once per area and cached under ``data/`` so subsequent runs
are fast and work offline. Everything is returned already projected to a metric
CRS (meters) so downstream geometry math is in real-world units.
"""
from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict

import geopandas as gpd
import networkx as nx
import osmnx as ox
import requests
from shapely.geometry import LineString, Point

from . import config, geoutil, sources
from .config import Area

ox.settings.use_cache = True
ox.settings.log_console = False


# Ad-hoc areas are snapped outward to this grid, in degrees (~1.1 km), before
# they become a cache key. Without it, two clicks a block apart produce two
# different keys and each pays the full OpenStreetMap download again.
CACHE_GRID_DEG = 0.01


def area_from_bbox(bbox: tuple[float, float, float, float],
                   name: str = "Custom area") -> Area:
    """Build an ad-hoc Area from a raw (west, south, east, north) bbox.

    Used for on-demand routing anywhere. The bbox is expanded outward to a fixed
    grid so that nearby requests resolve to the same key and reuse the cached
    download instead of refetching. Expanding (rather than rounding) guarantees
    the snapped area still covers everything the caller asked for.
    demo_from/demo_to are set to the center (unused off the presets).
    """
    w, s, e, n = bbox
    g = CACHE_GRID_DEG
    w = round(math.floor(w / g) * g, 4)
    s = round(math.floor(s / g) * g, 4)
    e = round(math.ceil(e / g) * g, 4)
    n = round(math.ceil(n / g) * g, 4)
    center = ((s + n) / 2.0, (w + e) / 2.0)
    key = "bbox_" + "_".join(f"{v:.4f}".replace("-", "m").replace(".", "p")
                             for v in (w, s, e, n))
    return Area(key=key, name=name, bbox=(w, s, e, n),
                demo_from=center, demo_to=center)


# --- Street network ----------------------------------------------------------
# The disk cache stops us re-downloading, but every request still paid to parse
# the graphml, reproject it, and rebuild edge geometry, which is seconds of CPU
# on a small instance. Keep the finished graph in memory instead. Two entries is
# deliberate: this runs on a 512MB box and a prepared graph is not small.
PREPARED_CACHE_SIZE = 2
_PREPARED: "OrderedDict[tuple, nx.MultiDiGraph]" = OrderedDict()
_PREPARED_LOCK = threading.Lock()


def clear_prepared_cache() -> None:
    """Drop the in-memory graphs (used by tests)."""
    with _PREPARED_LOCK:
        _PREPARED.clear()


def _cached_prepared(key: tuple) -> nx.MultiDiGraph | None:
    with _PREPARED_LOCK:
        G = _PREPARED.get(key)
        if G is not None:
            _PREPARED.move_to_end(key)
        return G


def _store_prepared(key: tuple, G: nx.MultiDiGraph) -> None:
    with _PREPARED_LOCK:
        _PREPARED[key] = G
        _PREPARED.move_to_end(key)
        while len(_PREPARED) > PREPARED_CACHE_SIZE:
            _PREPARED.popitem(last=False)


def get_graph(area: Area, data_dir: str = config.DATA_DIR,
              network_type: str = config.NETWORK_TYPE,
              copy: bool = False) -> nx.MultiDiGraph:
    """Return the projected (metric) walkable graph for ``area``, using cache.

    The returned graph is SHARED. Pass ``copy=True`` if you are going to write
    attributes onto it (shade, weights), or you will hand the next request your
    leftovers.
    """
    key = (area.key, network_type, area.metric_crs)
    cached = _cached_prepared(key)
    if cached is not None:
        geoutil.use_metric_crs(area.metric_crs)
        return cached.copy() if copy else cached

    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{area.key}_{network_type}.graphml")
    if os.path.exists(path):
        G = ox.io.load_graphml(path)
    else:
        covering = _covering_cache(area.bbox, data_dir, network_type)
        if covering:
            # An area we already downloaded contains this one. Reuse it rather
            # than paying another Overpass download for overlapping streets.
            G = ox.io.load_graphml(covering)
        else:
            # osmnx 2.x bbox order is (west, south, east, north).
            G = ox.graph.graph_from_bbox(area.bbox, network_type=network_type,
                                         simplify=True)
            ox.io.save_graphml(G, path)
    geoutil.use_metric_crs(area.metric_crs)
    G = ox.projection.project_graph(G, to_crs=area.metric_crs)
    _ensure_edge_geometry(G)
    _store_prepared(key, G)
    return G.copy() if copy else G


def _bbox_from_key(fname: str, network_type: str) -> tuple | None:
    """Recover the (west, south, east, north) a cached graph file was built for.

    Ad-hoc caches are named ``bbox_<w>_<s>_<e>_<n>_<network>.graphml`` with the
    sign written as ``m`` and the decimal point as ``p``, so the bbox is
    readable straight off the filename and needs no sidecar index.
    """
    suffix = f"_{network_type}.graphml"
    if not fname.startswith("bbox_") or not fname.endswith(suffix):
        return None
    parts = fname[len("bbox_"):-len(suffix)].split("_")
    if len(parts) != 4:
        return None
    try:
        return tuple(float(p.replace("m", "-").replace("p", ".")) for p in parts)
    except ValueError:
        return None


def _covering_cache(bbox: tuple[float, float, float, float], data_dir: str,
                    network_type: str) -> str | None:
    """Path of the smallest cached graph whose bbox fully contains ``bbox``."""
    w, s, e, n = bbox
    best, best_area = None, None
    try:
        names = os.listdir(data_dir)
    except OSError:
        return None
    for fname in names:
        cached = _bbox_from_key(fname, network_type)
        if not cached:
            continue
        cw, cs, ce, cn = cached
        if cw <= w and cs <= s and ce >= e and cn >= n:
            area = (ce - cw) * (cn - cs)
            if best_area is None or area < best_area:
                best, best_area = os.path.join(data_dir, fname), area
    return best


def _ensure_edge_geometry(G: nx.MultiDiGraph) -> None:
    """Guarantee every edge has a metric ``geometry`` LineString and ``length``.

    osmnx only attaches an explicit geometry to curved edges; straight edges are
    implied by their endpoints. We materialize those so shade intersection can
    treat all edges uniformly, and recompute ``length`` from the projected
    geometry so distances are consistent with shade lengths.
    """
    for u, v, data in G.edges(data=True):
        geom = data.get("geometry")
        if geom is None:
            geom = LineString([(G.nodes[u]["x"], G.nodes[u]["y"]),
                               (G.nodes[v]["x"], G.nodes[v]["y"])])
            data["geometry"] = geom
        data["length"] = geom.length


# --- Street trees ------------------------------------------------------------
def get_trees(area: Area, data_dir: str = config.DATA_DIR) -> gpd.GeoDataFrame:
    """Return alive street trees within ``area`` as a metric GeoDataFrame.

    Columns: tree_id, dbh (inches), species, health, geometry (points).
    """
    sources.require("nyc_street_trees", area.bbox)
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{area.key}_trees.geojson")
    if os.path.exists(path):
        gdf = gpd.read_file(path)
    else:
        gdf = _download_trees(area)
        if len(gdf):
            gdf.to_file(path, driver="GeoJSON")
    if gdf.crs is None:
        gdf = gdf.set_crs(config.WGS84)
    geoutil.use_metric_crs(area.metric_crs)
    return gdf.to_crs(area.metric_crs)


def get_named_streets(area: Area, data_dir: str = config.DATA_DIR
                      ) -> list[tuple[object, str]]:
    """Named street centerlines (metric geometry, name) for the area.

    The walkable network is mostly unnamed sidewalks, so planting sites are
    labeled against the *drive* network, whose edges carry real street names.
    """
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{area.key}_drive.graphml")
    if os.path.exists(path):
        G = ox.io.load_graphml(path)
    else:
        G = ox.graph.graph_from_bbox(area.bbox, network_type="drive",
                                     simplify=True)
        ox.io.save_graphml(G, path)
    geoutil.use_metric_crs(area.metric_crs)
    G = ox.projection.project_graph(G, to_crs=area.metric_crs)
    _ensure_edge_geometry(G)

    streets = []
    for _, _, d in G.edges(data=True):
        name = d.get("name")
        if not name:
            continue
        label = ", ".join(name) if isinstance(name, list) else str(name)
        streets.append((d["geometry"], label))
    return streets


def _download_trees(area: Area) -> gpd.GeoDataFrame:
    w, s, e, n = area.bbox
    where = (f"latitude between {s} and {n} and longitude between {w} and {e} "
             f"and status='Alive' and tree_dbh > 0")
    params = {
        "$select": "tree_id,latitude,longitude,tree_dbh,spc_common,health",
        "$where": where,
        "$limit": 200000,
        "$order": "tree_id",
    }
    resp = requests.get(config.TREES_ENDPOINT, params=params, timeout=120)
    resp.raise_for_status()
    rows = resp.json()

    records, geoms = [], []
    for r in rows:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
            dbh = float(r.get("tree_dbh", 0) or 0)
        except (TypeError, ValueError):
            continue
        records.append({
            "tree_id": r.get("tree_id"),
            "dbh": dbh,
            "species": r.get("spc_common") or "unknown",
            "health": r.get("health") or "Unknown",
        })
        geoms.append(Point(lon, lat))
    return gpd.GeoDataFrame(records, geometry=geoms, crs=config.WGS84)
