"""Heat-vulnerability overlay and H3 aggregation for shade equity analysis.

Shade-aware routing tells one pedestrian how to stay cool. This module zooms
out to the public-health question underneath it: *where* does missing shade
matter most? It layers three things onto the same street network:

1. ``vulnerability_inventory`` pulls heat-vulnerable facilities (schools,
   senior/social facilities, hospitals, transit stops) from OpenStreetMap.
2. ``facility_heat_risk`` scores each facility by the mean sun exposure of the
   walk edges around it, so the sunniest surroundings float to the top.
3. ``h3_shade`` bins trees and edge sun fraction into Uber H3 hexagons, a
   ready-to-map equity grid that is join-friendly across datasets.

Facility and hexagon geometry is returned in WGS84 so it drops straight onto a
web map or into a GIS. All distance math runs in the metric CRS.
"""
from __future__ import annotations

import math

import geopandas as gpd
import h3
import numpy as np
import osmnx as ox
from shapely import STRtree
from shapely.geometry import Polygon

from . import config, data, geoutil, pipeline
from .config import Area

# Radius around a facility whose walk edges define its heat exposure. About two
# short blocks: close enough that a resident actually walks it in the sun.
FACILITY_RADIUS_M = 150.0

# OSM tags that identify each heat-vulnerable category. Passed as one combined
# query (Overpass OR-matches every key/value) and classified afterward so we
# hit the network once instead of four times.
VULNERABLE_TAGS: dict[str, bool | str | list[str]] = {
    "amenity": ["school", "kindergarten", "hospital", "social_facility"],
    "social_facility": ["assisted_living", "nursing_home"],
    "public_transport": ["platform", "station"],
    "highway": "bus_stop",
}


# --- Facility inventory ------------------------------------------------------
def vulnerability_inventory(area: Area) -> gpd.GeoDataFrame:
    """Pull heat-vulnerable facilities for ``area`` from OSM, tagged by category.

    Returns a WGS84 GeoDataFrame with columns ``category``, ``name``, and
    ``geometry``. Categories: ``school``, ``senior_social``, ``hospital``,
    ``transit``. Prints a count per category. Hits Overpass over the network.
    """
    try:
        raw = ox.features.features_from_bbox(area.bbox, VULNERABLE_TAGS)
    except Exception as exc:  # osmnx raises when Overpass returns nothing
        print(f"vulnerability_inventory: no OSM features returned ({exc})")
        return gpd.GeoDataFrame(
            {"category": [], "name": []}, geometry=[], crs=config.WGS84)

    records, geoms = [], []
    for _, row in raw.iterrows():
        category = _classify(row)
        if category is None:
            continue
        records.append({"category": category, "name": _text(row.get("name"))})
        # Facilities are a mix of points (stops) and building polygons; a
        # representative point keeps every category on the same footing.
        geoms.append(row.geometry.representative_point())

    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs=config.WGS84)
    _print_category_counts(gdf)
    return gdf


def _classify(row) -> str | None:
    """Map one OSM feature to a heat-vulnerable category, or None to drop it."""
    amenity = _text(row.get("amenity"))
    social = _text(row.get("social_facility"))
    transport = _text(row.get("public_transport"))
    highway = _text(row.get("highway"))

    if amenity == "hospital":
        return "hospital"
    if amenity in ("school", "kindergarten"):
        return "school"
    if amenity == "social_facility" or social in ("assisted_living", "nursing_home"):
        return "senior_social"
    if transport in ("platform", "station") or highway == "bus_stop":
        return "transit"
    return None


# --- Facility heat risk ------------------------------------------------------
def facility_heat_risk(area: Area) -> gpd.GeoDataFrame:
    """Score facilities by the mean sun fraction of nearby walk edges.

    Runs the shade pipeline to annotate edges with ``sun_frac`` at the default
    modeled time, then for each facility averages the sun fraction of every walk
    edge within ``FACILITY_RADIUS_M``. Higher score = hotter surroundings.
    Returns a WGS84 GeoDataFrame sorted by ``heat_risk`` (descending) and prints
    the top few.
    """
    facilities = vulnerability_inventory(area)
    if len(facilities) == 0:
        print("facility_heat_risk: no facilities to score")
        return facilities

    out = pipeline.analyze(
        area, config.DEFAULT_DATETIME_LOCAL, config.DEFAULT_ALPHA,
        from_latlon=area.demo_from, to_latlon=area.demo_to)
    edge_geoms, edge_sun = _edge_sun_arrays(out["graph"])
    edge_index = STRtree(edge_geoms)

    # Work in the metric CRS so the 150 m buffer is real meters.
    metric = facilities.to_crs(config.METRIC_CRS)
    risks, counts = [], []
    for point in metric.geometry.values:
        near = _edges_within(point, edge_index, edge_geoms, FACILITY_RADIUS_M)
        counts.append(len(near))
        risks.append(float(np.mean([edge_sun[i] for i in near])) if near
                     else math.nan)

    facilities = facilities.copy()
    facilities["heat_risk"] = [round(r, 3) if not math.isnan(r) else math.nan
                               for r in risks]
    facilities["n_edges"] = counts
    facilities = facilities.sort_values(
        "heat_risk", ascending=False, na_position="last").reset_index(drop=True)

    _print_top_risk(facilities)
    return facilities


def _edge_sun_arrays(G) -> tuple[list, list[float]]:
    """Parallel lists of metric edge geometries and their ``sun_frac``."""
    geoms, sun = [], []
    for _, _, d in G.edges(data=True):
        geoms.append(d["geometry"])
        sun.append(d.get("sun_frac", 1.0))
    return geoms, sun


def _edges_within(point, edge_index: STRtree, edge_geoms: list,
                  radius_m: float) -> list[int]:
    """Indices of edges whose geometry is within ``radius_m`` of ``point``."""
    buffer = point.buffer(radius_m)
    hits = edge_index.query(buffer)
    return [int(i) for i in hits if edge_geoms[int(i)].distance(point) <= radius_m]


# --- H3 aggregation ----------------------------------------------------------
def h3_shade(area: Area, res: int = 11) -> gpd.GeoDataFrame:
    """Aggregate trees and edge sun fraction into H3 hexagons.

    Each tree is binned to its H3 cell at resolution ``res``; each walk edge is
    binned by its midpoint. Returns a WGS84 GeoDataFrame of hexagon polygons
    with ``h3`` (cell id), ``tree_count``, and ``mean_sun`` (mean edge sun
    fraction, NaN where a cell has trees but no edge midpoint). Prints the cell
    count and a sample.
    """
    out = pipeline.analyze(
        area, config.DEFAULT_DATETIME_LOCAL, config.DEFAULT_ALPHA,
        from_latlon=area.demo_from, to_latlon=area.demo_to)

    # Trees -> per-cell counts.
    trees_wgs = out["trees"].to_crs(config.WGS84)
    tree_count: dict[str, int] = {}
    for point in trees_wgs.geometry.values:
        cell = h3.latlng_to_cell(point.y, point.x, res)
        tree_count[cell] = tree_count.get(cell, 0) + 1

    # Edge midpoints -> per-cell mean sun fraction.
    sun_samples: dict[str, list[float]] = {}
    for _, _, d in out["graph"].edges(data=True):
        mid = d["geometry"].interpolate(0.5, normalized=True)
        lat, lon = geoutil.xy_to_latlon(mid.x, mid.y)
        cell = h3.latlng_to_cell(lat, lon, res)
        sun_samples.setdefault(cell, []).append(d.get("sun_frac", 1.0))

    cells = sorted(set(tree_count) | set(sun_samples))
    records, geoms = [], []
    for cell in cells:
        samples = sun_samples.get(cell)
        records.append({
            "h3": cell,
            "tree_count": tree_count.get(cell, 0),
            "mean_sun": round(float(np.mean(samples)), 3) if samples else math.nan,
        })
        geoms.append(_cell_polygon(cell))

    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs=config.WGS84)
    _print_h3_summary(gdf, res)
    return gdf


def _cell_polygon(cell: str) -> Polygon:
    """WGS84 polygon for an H3 cell (h3 v4 returns (lat, lon) vertices)."""
    boundary = h3.cell_to_boundary(cell)
    return Polygon([(lon, lat) for lat, lon in boundary])


# --- Small helpers -----------------------------------------------------------
def _text(value) -> str | None:
    """Normalize an OSM tag value to a lowercase string, or None if missing."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return str(value).strip().lower()


def _print_category_counts(gdf: gpd.GeoDataFrame) -> None:
    print(f"vulnerability_inventory: {len(gdf)} facilities")
    if len(gdf) == 0:
        return
    for category, count in gdf["category"].value_counts().items():
        print(f"  {category:<14} {count}")


def _print_top_risk(gdf: gpd.GeoDataFrame, top: int = 5) -> None:
    print(f"facility_heat_risk: scored {len(gdf)} facilities "
          f"(1.0 = full sun, 0.0 = full shade)")
    for _, row in gdf.head(top).iterrows():
        risk = "n/a" if math.isnan(row["heat_risk"]) else f"{row['heat_risk']:.3f}"
        name = row["name"] or "unnamed"
        print(f"  risk={risk:<6} edges={row['n_edges']:<3} "
              f"{row['category']:<14} {name}")


def _print_h3_summary(gdf: gpd.GeoDataFrame, res: int) -> None:
    with_trees = int((gdf["tree_count"] > 0).sum())
    print(f"h3_shade: {len(gdf)} cells at res {res} "
          f"({with_trees} with >=1 tree)")
    for _, row in gdf.sort_values("tree_count", ascending=False).head(3).iterrows():
        mean_sun = "n/a" if math.isnan(row["mean_sun"]) else f"{row['mean_sun']:.3f}"
        print(f"  {row['h3']}  trees={row['tree_count']:<3} mean_sun={mean_sun}")


def run_demo(area_key: str = config.DEFAULT_AREA) -> None:
    """Run all three overlays for one area and print real counts."""
    area = config.AREAS[area_key]
    print(f"=== hazard overlay: {area.name} ===")
    vulnerability_inventory(area)
    print()
    facility_heat_risk(area)
    print()
    h3_shade(area)


if __name__ == "__main__":
    run_demo()
