"""Bake an area into a compact JSON the browser routes on (no backend needed).

The interactive web app (``web/index.html``) runs Dijkstra client-side, so it
needs the walk graph plus, for a handful of times of day, the sun fraction of
every edge. This precomputes all of that once and writes a single JSON file the
page fetches. Because routing happens in the browser, the app deploys as a
static site (GitHub Pages, Vercel, S3) with zero server cost.
"""
from __future__ import annotations

import json
import os

from . import config, data, geoutil, shade, solar
from .config import Area


def export_web(area: Area, out_path: str,
               date: str = "2026-07-15",
               hours: tuple[int, ...] = (8, 10, 12, 14, 16, 18),
               default_hour: int = 16,
               alpha_default: float = config.DEFAULT_ALPHA,
               tz: str = config.TIMEZONE,
               data_dir: str = config.DATA_DIR,
               max_trees: int = 4000) -> str:
    """Write the web bundle for ``area`` and return the path."""
    G = data.get_graph(area, data_dir)
    trees = data.get_trees(area, data_dir)
    lat, lon = area.center

    # Per-edge sun fraction at each modeled hour.
    edge_sun: dict[tuple, list[float]] = {}
    for h in hours:
        when = solar.to_utc(f"{date} {h:02d}:00", tz)
        alt, az = solar.sun_position(lat, lon, when)
        shadows = shade.compute_shadows(trees, alt, az)
        shade.annotate_edges(G, shadows)
        for u, v, k, d in G.edges(keys=True, data=True):
            edge_sun.setdefault((u, v, k), []).append(round(d["sun_frac"], 3))

    # Compact node table (remap osmid -> 0..N-1).
    node_ids = list(G.nodes())
    idx = {nid: i for i, nid in enumerate(node_ids)}
    nodes = []
    for nid in node_ids:
        la, lo = geoutil.xy_to_latlon(G.nodes[nid]["x"], G.nodes[nid]["y"])
        nodes.append([round(la, 6), round(lo, 6)])

    edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        wgs = geoutil.geom_to_wgs(d["geometry"])
        coords = [[round(la, 6), round(lo, 6)] for lo, la in wgs.coords]
        edges.append({
            "u": idx[u], "v": idx[v],
            "len": round(d["length"], 1),
            "c": coords,
            "s": edge_sun[(u, v, k)],
        })

    # Trees for display (subsampled to keep the payload small).
    pts = list(trees.geometry.values)
    step = max(1, len(pts) // max_trees)
    tree_pts = []
    for p in pts[::step]:
        la, lo = geoutil.xy_to_latlon(p.x, p.y)
        tree_pts.append([round(la, 6), round(lo, 6)])

    bundle = {
        "area": {"key": area.key, "name": area.name,
                 "bbox": list(area.bbox), "center": [lat, lon]},
        "date": date,
        "hours": list(hours),
        "default_hour": default_hour if default_hour in hours else hours[len(hours) // 2],
        "default_alpha": alpha_default,
        "nodes": nodes,
        "edges": edges,
        "trees": tree_pts,
        "demo": {"from": list(area.demo_from), "to": list(area.demo_to)},
    }

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(bundle, fh, separators=(",", ":"))
    return out_path
