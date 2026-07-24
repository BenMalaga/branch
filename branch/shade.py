"""Turn a set of trees + a sun position into per-street-segment shade.

Pipeline:
1. ``compute_shadows`` projects every tree crown to a ground shadow polygon.
2. ``annotate_edges`` walks each street segment and measures the fraction of
   its length that falls inside a tree shadow. That ``sun_frac`` (0 = fully
   shaded, 1 = fully exposed) is the quantity the router optimizes against.

An STRtree spatial index keeps the per-edge shade query near O(log n) in the
number of trees, so this scales past a single neighborhood.
"""
from __future__ import annotations

import geopandas as gpd
from shapely import STRtree
from shapely.ops import unary_union

from . import config, solar


def compute_shadows(trees: gpd.GeoDataFrame, altitude_deg: float,
                    azimuth_deg: float,
                    max_len: float = config.MAX_SHADOW_M) -> gpd.GeoDataFrame:
    """Return a GeoDataFrame of ground shadow polygons, one per tree.

    Empty when the sun is below the horizon (nothing casts a shadow).
    """
    if altitude_deg <= 0:
        return gpd.GeoDataFrame({"crown_r": []}, geometry=[], crs=trees.crs)

    polys, crown_rs = [], []
    for geom, dbh in zip(trees.geometry.values, trees["dbh"].values):
        crown_r = solar.crown_radius_m(dbh)
        height = solar.tree_height_m(dbh)
        shadow = solar.tree_shadow_polygon(geom.x, geom.y, crown_r, height,
                                           altitude_deg, azimuth_deg, max_len)
        if shadow is None or shadow.is_empty:
            continue
        polys.append(shadow)
        crown_rs.append(crown_r)
    return gpd.GeoDataFrame({"crown_r": crown_rs}, geometry=polys, crs=trees.crs)


def annotate_edges(G, shadows: gpd.GeoDataFrame) -> None:
    """Attach ``sun_frac`` and ``shade_len`` to every edge of ``G`` in place.

    ``sun_frac`` in [0, 1] is the share of the segment exposed to sun.
    """
    if shadows is None or len(shadows) == 0:
        for _, _, data in G.edges(data=True):
            data["shade_len"] = 0.0
            data["sun_frac"] = 1.0
        return

    geoms = list(shadows.geometry.values)
    tree_index = STRtree(geoms)

    for _, _, data in G.edges(data=True):
        edge = data["geometry"]
        length = data["length"]
        # Candidate shadows whose bounding box meets this edge.
        hits = tree_index.query(edge)
        if len(hits) == 0 or length == 0:
            shaded = 0.0
        else:
            covering = unary_union([geoms[i] for i in hits])
            shaded = edge.intersection(covering).length
        shaded = min(shaded, length)
        data["shade_len"] = shaded
        data["sun_frac"] = 1.0 - (shaded / length if length else 1.0)


def shade_union(shadows: gpd.GeoDataFrame):
    """Dissolve all shadow polygons into one geometry (for drawing a shade layer)."""
    if shadows is None or len(shadows) == 0:
        return None
    return unary_union(list(shadows.geometry.values))
