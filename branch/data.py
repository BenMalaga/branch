"""Fetch and cache the two public datasets branch needs.

* The **walkable street network** from OpenStreetMap (via osmnx / Overpass).
* The **street trees** from the NYC 2015 Street Tree Census (Socrata API).

Both are downloaded once per area and cached under ``data/`` so subsequent runs
are fast and work offline. Everything is returned already projected to a metric
CRS (meters) so downstream geometry math is in real-world units.
"""
from __future__ import annotations

import os

import geopandas as gpd
import networkx as nx
import osmnx as ox
import requests
from shapely.geometry import LineString, Point

from . import config
from .config import Area

ox.settings.use_cache = True
ox.settings.log_console = False


def area_from_bbox(bbox: tuple[float, float, float, float],
                   name: str = "Custom area") -> Area:
    """Build an ad-hoc Area from a raw (west, south, east, north) bbox.

    Used for on-demand routing anywhere: the key is derived from the rounded
    bbox so the downloaded graph and trees cache and are reused for nearby
    requests. demo_from/demo_to are set to the center (unused off the presets).
    """
    w, s, e, n = bbox
    center = ((s + n) / 2.0, (w + e) / 2.0)
    key = "bbox_" + "_".join(f"{v:.4f}".replace("-", "m").replace(".", "p")
                             for v in (w, s, e, n))
    return Area(key=key, name=name, bbox=(w, s, e, n),
                demo_from=center, demo_to=center)


# --- Street network ----------------------------------------------------------
def get_graph(area: Area, data_dir: str = config.DATA_DIR,
              network_type: str = config.NETWORK_TYPE) -> nx.MultiDiGraph:
    """Return the projected (metric) walkable graph for ``area``, using cache."""
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{area.key}_{network_type}.graphml")
    if os.path.exists(path):
        G = ox.io.load_graphml(path)
    else:
        # osmnx 2.x bbox order is (west, south, east, north).
        G = ox.graph.graph_from_bbox(area.bbox, network_type=network_type,
                                     simplify=True)
        ox.io.save_graphml(G, path)
    G = ox.projection.project_graph(G, to_crs=config.METRIC_CRS)
    _ensure_edge_geometry(G)
    return G


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
    return gdf.to_crs(config.METRIC_CRS)


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
    G = ox.projection.project_graph(G, to_crs=config.METRIC_CRS)
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
