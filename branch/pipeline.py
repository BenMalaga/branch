"""End-to-end orchestration: area + time -> shade -> routes.

One call, ``analyze``, runs the whole pipeline and returns every artifact the
CLI and visualizers need. Keeping it here (not in the CLI) means the pipeline is
importable from a notebook or another program.
"""
from __future__ import annotations

from . import config, data, routing, shade, solar
from .config import Area


def analyze(area: Area, when_local: str, alpha: float,
            from_latlon: tuple[float, float], to_latlon: tuple[float, float],
            tz: str = config.TIMEZONE, data_dir: str = config.DATA_DIR) -> dict:
    """Run branch for one area, time, and origin/destination pair."""
    when_utc = solar.to_utc(when_local, tz)
    lat, lon = area.center
    altitude, azimuth = solar.sun_position(lat, lon, when_utc)

    G = data.get_graph(area, data_dir)
    trees = data.get_trees(area, data_dir)

    shadows = shade.compute_shadows(trees, altitude, azimuth)
    shade.annotate_edges(G, shadows)
    shade_geom = shade.shade_union(shadows)

    result = routing.compare(G, from_latlon, to_latlon, alpha)

    return {
        "area": area,
        "graph": G,
        "trees": trees,
        "shadows": shadows,
        "shade_geom": shade_geom,
        "result": result,
        "altitude": altitude,
        "azimuth": azimuth,
        "when_local": when_local,
        "when_utc": when_utc,
    }
