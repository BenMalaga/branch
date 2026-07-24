"""Shade-aware routing and tree-planting prioritization.

The router runs plain Dijkstra twice on the same graph with two edge-cost
functions:

* ``w_fast`` = segment length (the ordinary shortest path).
* ``w_cool`` = length * (1 + alpha * sun_fraction). Sunny segments cost more, so
  the optimizer will accept a detour when it buys enough shade. ``alpha`` (shade
  aversion) is the same sun-avoidance knob formalized by Wolf, Vierø & Szell,
  "CoolWalks" (Scientific Reports, 2025), applied here to tree shade.

``plant_gaps`` inverts the idea for urban forestry: it scores each street
segment by pedestrian traffic (edge betweenness centrality) x sun exposure x
length, surfacing where a newly planted tree would shade the most walking.
"""
from __future__ import annotations

import networkx as nx
import osmnx as ox
from shapely import STRtree
from shapely.geometry import LineString, box

from . import geoutil

# Report only planting sites at least this far inside the study-area boundary.
# Betweenness centrality on a clipped network inflates segments near the edges
# (they act as artificial funnels), so we exclude the boundary ring.
BOUNDARY_BUFFER_M = 120.0


# --- Edge weighting ----------------------------------------------------------
def set_weights(G, alpha: float) -> None:
    """Set ``w_fast`` and ``w_cool`` on every edge (requires ``sun_frac`` set)."""
    for _, _, data in G.edges(data=True):
        length = data["length"]
        sun = data.get("sun_frac", 1.0)
        data["w_fast"] = length
        data["w_cool"] = length * (1.0 + alpha * sun)


def nearest_node(G, lat: float, lon: float):
    """Nearest graph node to a (lat, lon) point."""
    x, y = geoutil.latlon_to_xy(lat, lon)
    return ox.distance.nearest_nodes(G, X=x, Y=y)


# --- Routing -----------------------------------------------------------------
def route(G, orig, dest, weight_key: str) -> dict:
    """Shortest path under ``weight_key`` with distance/sun statistics.

    Returns a dict with the node path, merged metric geometry, total length,
    sunlit meters, shaded meters, and overall sun fraction.
    """
    path = nx.shortest_path(G, orig, dest, weight=weight_key)
    coords: list[tuple[float, float]] = []
    total_len = sun_m = shade_m = 0.0

    for u, v in zip(path, path[1:]):
        edict = G.get_edge_data(u, v)
        # Among parallel edges, take the one Dijkstra would (min weight).
        k = min(edict, key=lambda kk: edict[kk].get(weight_key, edict[kk]["length"]))
        data = edict[k]
        length = data["length"]
        total_len += length
        sun_m += length * data.get("sun_frac", 1.0)
        shade_m += data.get("shade_len", length * (1.0 - data.get("sun_frac", 1.0)))

        xy = list(data["geometry"].coords)
        ux, uy = G.nodes[u]["x"], G.nodes[u]["y"]
        # Orient the segment geometry so it runs u -> v.
        if _d2(xy[0], (ux, uy)) > _d2(xy[-1], (ux, uy)):
            xy = xy[::-1]
        if coords and coords[-1] == xy[0]:
            coords.extend(xy[1:])
        else:
            coords.extend(xy)

    return {
        "nodes": path,
        "geometry": LineString(coords),
        "length_m": total_len,
        "sun_m": sun_m,
        "shade_m": shade_m,
        "sun_frac": sun_m / total_len if total_len else 0.0,
    }


def compare(G, from_latlon: tuple[float, float], to_latlon: tuple[float, float],
            alpha: float) -> dict:
    """Compute the fastest and coolest routes between two (lat, lon) points."""
    set_weights(G, alpha)
    orig = nearest_node(G, *from_latlon)
    dest = nearest_node(G, *to_latlon)
    if orig == dest:
        raise ValueError("Origin and destination snap to the same node; "
                         "pick points further apart or a larger area.")
    return {
        "fast": route(G, orig, dest, "w_fast"),
        "cool": route(G, orig, dest, "w_cool"),
        "orig": orig,
        "dest": dest,
        "from_latlon": from_latlon,
        "to_latlon": to_latlon,
        "alpha": alpha,
    }


# --- Planting prioritization -------------------------------------------------
def plant_gaps(G, bbox, named_streets, top_n: int = 15) -> list[dict]:
    """Rank street segments where a new tree would shade the most walking.

    score = betweenness_centrality x sun_fraction x length. High score = a busy,
    sunny, long segment: the best marginal place to plant. Boundary segments are
    dropped (edge effects) and the result is deduped to distinct streets, each
    labeled by its nearest named street centerline.

    Args:
        G: the annotated walk graph.
        bbox: (west, south, east, north) of the study area.
        named_streets: list of (metric_geometry, name) for naming sites.
        top_n: number of distinct streets to return.
    """
    H = _simple_undirected(G)
    centrality = nx.edge_betweenness_centrality(H, weight="length", normalized=True)

    name_tree = STRtree([g for g, _ in named_streets]) if named_streets else None
    names = [n for _, n in named_streets]
    interior = _interior_box(bbox)

    scored = []
    for (u, v), c in centrality.items():
        data = H[u][v]
        if "step" in data["highway"].lower():
            continue  # can't plant a tree on a staircase
        mid = data["geometry"].interpolate(0.5, normalized=True)
        if not interior.contains(mid):
            continue  # boundary ring: centrality is unreliable here
        sun = data.get("sun_frac", 1.0)
        length = data["length"]
        lat, lon = geoutil.xy_to_latlon(mid.x, mid.y)
        scored.append({
            "street": _nearest_name(mid, name_tree, names),
            "length_m": round(length, 1),
            "sun_frac": round(sun, 3),
            "centrality": round(c, 5),
            "score": round(c * sun * length, 3),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
        })
    scored.sort(key=lambda d: d["score"], reverse=True)
    return _dedupe_by_street(scored, top_n)


def _interior_box(bbox):
    """Study-area box shrunk by the boundary buffer, in the metric CRS."""
    w, s, e, n = bbox
    x0, y0 = geoutil.latlon_to_xy(s, w)
    x1, y1 = geoutil.latlon_to_xy(n, e)
    b = BOUNDARY_BUFFER_M
    return box(min(x0, x1) + b, min(y0, y1) + b, max(x0, x1) - b, max(y0, y1) - b)


def _dedupe_by_street(scored: list[dict], top_n: int) -> list[dict]:
    """Keep the highest-scoring segment per street name."""
    seen, out = set(), []
    for row in scored:
        if row["street"] in seen:
            continue
        seen.add(row["street"])
        out.append(row)
        if len(out) >= top_n:
            break
    return out


def _simple_undirected(G) -> nx.Graph:
    """Collapse the MultiDiGraph to a simple undirected graph, keeping the
    shortest parallel edge and its shade attributes."""
    H = nx.Graph()
    for u, v, data in G.edges(data=True):
        length = data["length"]
        if H.has_edge(u, v) and H[u][v]["length"] <= length:
            continue
        H.add_edge(u, v, length=length, sun_frac=data.get("sun_frac", 1.0),
                   geometry=data["geometry"], highway=str(data.get("highway", "")))
    return H


def _nearest_name(point, name_tree, names) -> str:
    if name_tree is None:
        return "unnamed"
    return names[name_tree.nearest(point)]


def _d2(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
