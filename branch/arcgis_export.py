"""Esri / ArcGIS interoperability: write branch results as GIS layers.

branch's engine lives in memory (a NetworkX graph plus shapely geometry). GIS
analysts work in ArcGIS Pro, so this module bridges the two: it runs the full
pipeline for one area and time, then serializes every product an analyst would
want as standard, Esri-consumable files.

Outputs (all reprojected to WGS84 / EPSG:4326):

* ``scored_segments`` (.shp + .geojson): every walk edge as a LineString with
  ``length_m``, ``sun_frac``, and ``shade_len`` attributes.
* ``planting_gaps`` (.shp + .geojson + .csv): the ranked tree-planting sites as
  points. The CSV carries a WKT geometry column plus lat/lon for spreadsheet use.
* ``trees.geojson``: the street trees as points (dbh / species / health).
* ``shade.geojson``: the dissolved tree-shadow footprint polygon.

Shapefiles and GeoJSON are written through geopandas on the pyogrio backend. The
companion ArcGIS Pro Python Toolbox in ``arcgis/branch_toolbox.pyt`` calls
``export_all`` and loads the shapefiles into the active map.
"""
from __future__ import annotations

import os

import geopandas as gpd
from shapely.geometry import Point

from . import config, data, geoutil, pipeline, routing
from .config import Area, WGS84


def export_all(area: Area, when_local: str = config.DEFAULT_DATETIME_LOCAL,
               alpha: float = config.DEFAULT_ALPHA,
               out_dir: str = "exports") -> list[str]:
    """Run the pipeline for ``area`` and write every Esri-consumable product.

    Args:
        area: the study area to analyze.
        when_local: local wall-clock time to model shade for ("YYYY-MM-DD HH:MM").
        alpha: shade-aversion weight passed through to the router.
        out_dir: destination folder for all output files (created if missing).

    Returns:
        The list of file paths written, in creation order.
    """
    os.makedirs(out_dir, exist_ok=True)

    out = pipeline.analyze(area, when_local, alpha,
                           from_latlon=area.demo_from, to_latlon=area.demo_to)
    streets = data.get_named_streets(area)
    gaps = routing.plant_gaps(out["graph"], area.bbox, streets, top_n=15)

    written: list[str] = []
    written += _write_scored_segments(out["graph"], out_dir)
    written += _write_planting_gaps(gaps, out_dir)
    written.append(_write_trees(out["trees"], out_dir))
    shade_path = _write_shade(out["shade_geom"], out_dir)
    if shade_path is not None:
        written.append(shade_path)

    _print_summary(area, when_local, out_dir, written)
    return written


# --- Layer builders ----------------------------------------------------------
def _write_scored_segments(G, out_dir: str) -> list[str]:
    """Write every walk edge (WGS84 LineString) to shapefile + GeoJSON."""
    geoms, records = [], []
    for _, _, edge in G.edges(data=True):
        geoms.append(geoutil.geom_to_wgs(edge["geometry"]))
        records.append({
            "length_m": round(float(edge["length"]), 2),
            "sun_frac": round(float(edge.get("sun_frac", 1.0)), 4),
            "shade_len": round(float(edge.get("shade_len", 0.0)), 2),
        })
    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs=WGS84)

    shp = os.path.join(out_dir, "scored_segments.shp")
    geojson = os.path.join(out_dir, "scored_segments.geojson")
    gdf.to_file(shp, driver="ESRI Shapefile", engine="pyogrio")
    gdf.to_file(geojson, driver="GeoJSON", engine="pyogrio")
    return [shp, geojson]


def _write_planting_gaps(gaps: list[dict], out_dir: str) -> list[str]:
    """Write the ranked planting sites as points: shapefile + GeoJSON + CSV.

    Field names are kept to 10 characters so they survive the shapefile DBF
    format unchanged. The CSV mirrors the same attributes but stores geometry as
    a WKT string alongside explicit lat/lon columns for spreadsheet users.
    """
    geoms = [Point(g["lon"], g["lat"]) for g in gaps]
    records = [{
        "street": g["street"],
        "length_m": g["length_m"],
        "sun_frac": g["sun_frac"],
        "centrality": g["centrality"],
        "score": g["score"],
        "lat": g["lat"],
        "lon": g["lon"],
    } for g in gaps]
    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs=WGS84)

    shp = os.path.join(out_dir, "planting_gaps.shp")
    geojson = os.path.join(out_dir, "planting_gaps.geojson")
    csv = os.path.join(out_dir, "planting_gaps.csv")
    gdf.to_file(shp, driver="ESRI Shapefile", engine="pyogrio")
    gdf.to_file(geojson, driver="GeoJSON", engine="pyogrio")

    # CSV with a WKT geometry column (Esri "XY Table To Point" and QGIS both
    # read this) plus the lat/lon already carried in the attributes.
    csv_df = gdf.drop(columns="geometry").copy()
    csv_df["geometry_wkt"] = [geom.wkt for geom in geoms]
    csv_df.to_csv(csv, index=False)
    return [shp, geojson, csv]


def _write_trees(trees, out_dir: str) -> str:
    """Write the street trees (points, WGS84) to GeoJSON."""
    cols = [c for c in ("tree_id", "dbh", "species", "health") if c in trees.columns]
    gdf = trees[cols + ["geometry"]].to_crs(WGS84)
    path = os.path.join(out_dir, "trees.geojson")
    gdf.to_file(path, driver="GeoJSON", engine="pyogrio")
    return path


def _write_shade(shade_geom, out_dir: str) -> str | None:
    """Write the dissolved tree-shadow footprint (polygon, WGS84) to GeoJSON."""
    if shade_geom is None or shade_geom.is_empty:
        return None
    gdf = gpd.GeoDataFrame({"layer": ["tree_shade"]},
                           geometry=[geoutil.geom_to_wgs(shade_geom)], crs=WGS84)
    path = os.path.join(out_dir, "shade.geojson")
    gdf.to_file(path, driver="GeoJSON", engine="pyogrio")
    return path


# --- Reporting ---------------------------------------------------------------
def _print_summary(area: Area, when_local: str, out_dir: str,
                   written: list[str]) -> None:
    """Print each written file with its on-disk size (shapefile sidecars too)."""
    print(f"\n  ArcGIS export: {area.name} @ {when_local} -> {out_dir}/")
    seen: set[str] = set()
    for path in written:
        stem = os.path.splitext(path)[0]
        # A shapefile is a set of sidecar files (.shp/.shx/.dbf/.prj/...); list
        # them all once, grouped under their stem.
        if path.endswith(".shp"):
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                sidecar = stem + ext
                if os.path.exists(sidecar) and sidecar not in seen:
                    seen.add(sidecar)
                    print(f"    {os.path.basename(sidecar):<26} {_size(sidecar)}")
        else:
            if path not in seen:
                seen.add(path)
                print(f"    {os.path.basename(path):<26} {_size(path)}")


def _size(path: str) -> str:
    """Human-readable file size."""
    n = os.path.getsize(path)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
