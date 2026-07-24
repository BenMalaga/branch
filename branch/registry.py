"""The typed tool registry: the deterministic execution floor of branch.

Every capability is a ``Tool`` with a typed JSON-Schema contract. A Tool is what
a human clicks in the UI *and* what the AI agent calls. Correctness (CRS,
topology, units) lives in the tools, never in the model: the agent only selects
and parameterizes; the tools execute deterministically.

Each ``run`` returns ``{"result": <geojson|table|value>, "recipe": {...}}`` where
the recipe is a re-runnable record of the tool id + resolved params (provenance).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import geopandas as gpd

from . import config, data, pipeline, routing, geoutil


@dataclass
class Tool:
    id: str
    title: str
    description: str          # written for the LLM: what it does + when to use it
    params: dict             # JSON Schema of the inputs
    run: Callable            # (params: dict) -> {"result":..., "recipe":...}
    returns: str = "layer"   # layer | table | value
    category: str = "geoprocessing"   # geoprocessing | connector | planning


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    _REGISTRY[tool.id] = tool
    return tool


def get(tool_id: str) -> Tool | None:
    return _REGISTRY.get(tool_id)


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def as_llm_tools() -> list[dict]:
    """Registry -> the function schemas an LLM tool-calling API expects."""
    return [{
        "name": t.id,
        "description": t.description,
        "input_schema": t.params,
    } for t in _REGISTRY.values()]


# --- helpers -----------------------------------------------------------------
def _fc(gdf: gpd.GeoDataFrame) -> dict:
    """GeoDataFrame (any CRS) -> WGS84 GeoJSON FeatureCollection."""
    return json.loads(gdf.to_crs(config.WGS84).to_json())


def _read_fc(fc: dict) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame.from_features(fc.get("features", []), crs=config.WGS84)
    if gdf.empty:
        raise ValueError("input layer has no features")
    return gdf


# --- geoprocessing tools -----------------------------------------------------
def _run_buffer(params: dict) -> dict:
    gdf = _read_fc(params["layer"])
    dist = float(params["distance_m"])
    metric = gdf.to_crs(gdf.estimate_utm_crs())     # grounding: reproject to meters
    metric["geometry"] = metric.buffer(dist)
    return {"result": _fc(metric),
            "recipe": {"tool": "buffer", "distance_m": dist}}


register(Tool(
    id="buffer", title="Buffer", category="geoprocessing", returns="layer",
    description="Grow a zone of a given radius (in meters) around every feature "
                "in a layer. Use for 'within X of', catchment zones, setbacks.",
    params={"type": "object", "required": ["layer", "distance_m"], "properties": {
        "layer": {"type": "object", "description": "a GeoJSON FeatureCollection"},
        "distance_m": {"type": "number", "description": "buffer radius in meters"}}},
    run=_run_buffer))


def _run_spatial_join(params: dict) -> dict:
    left = _read_fc(params["target"])
    right = _read_fc(params["join"])
    predicate = params.get("predicate", "intersects")
    joined = gpd.sjoin(left, right.to_crs(left.crs), predicate=predicate, how="left")
    joined = joined.drop(columns=[c for c in joined.columns if c.startswith("index_")],
                         errors="ignore")
    return {"result": _fc(joined),
            "recipe": {"tool": "spatial_join", "predicate": predicate}}


register(Tool(
    id="spatial_join", title="Spatial join", category="geoprocessing", returns="layer",
    description="Attach attributes from one layer to another based on a spatial "
                "relationship (which polygon a point falls in, etc).",
    params={"type": "object", "required": ["target", "join"], "properties": {
        "target": {"type": "object", "description": "layer to keep (FeatureCollection)"},
        "join": {"type": "object", "description": "layer whose attributes to attach"},
        "predicate": {"type": "string", "enum": ["intersects", "within", "contains"],
                      "default": "intersects"}}},
    run=_run_spatial_join))


# --- connectors (free public data) -------------------------------------------
def _run_osm(params: dict) -> dict:
    import osmnx as ox
    w, s, e, n = params["bbox"]
    tags = params.get("tags", {"amenity": True})
    gdf = ox.features.features_from_bbox((w, s, e, n), tags)
    gdf = gdf[gdf.geometry.notna()].copy()
    keep = [c for c in ("name", "amenity", "shop", "leisure", "geometry") if c in gdf.columns]
    return {"result": _fc(gdf[keep].reset_index(drop=True)),
            "recipe": {"tool": "osm", "bbox": params["bbox"], "tags": tags}}


register(Tool(
    id="osm", title="OpenStreetMap features", category="connector", returns="layer",
    description="Fetch map features (parks, schools, shops, transit, roads...) "
                "from OpenStreetMap for a bounding box. Key-free.",
    params={"type": "object", "required": ["bbox"], "properties": {
        "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                 "description": "[west, south, east, north] in degrees"},
        "tags": {"type": "object", "description": "OSM tag filter, e.g. {\"amenity\": \"school\"}"}}},
    run=_run_osm))


# --- planning tools ----------------------------------------------------------
def _run_coolwalk(params: dict) -> dict:
    frm = tuple(params["from"]); to = tuple(params["to"])
    when = params.get("datetime", config.DEFAULT_DATETIME_LOCAL)
    alpha = float(params.get("alpha", config.DEFAULT_ALPHA))
    pad = 0.004
    bbox = (min(frm[1], to[1]) - pad, min(frm[0], to[0]) - pad,
            max(frm[1], to[1]) + pad, max(frm[0], to[0]) + pad)
    out = pipeline.analyze(data.area_from_bbox(bbox), when, alpha, frm, to)
    r = out["result"]
    feats = []
    for key, color in (("fast", "#f2683c"), ("cool", "#4f8bf0")):
        geom = geoutil.geom_to_wgs(r[key]["geometry"])
        feats.append({"type": "Feature", "geometry": json.loads(gpd.GeoSeries([geom]).to_json())["features"][0]["geometry"],
                      "properties": {"route": key, "length_m": round(r[key]["length_m"]),
                                     "sun_frac": round(r[key]["sun_frac"], 3), "color": color}})
    return {"result": {"type": "FeatureCollection", "features": feats},
            "recipe": {"tool": "coolwalk", "from": list(frm), "to": list(to),
                       "datetime": when, "alpha": alpha}}


register(Tool(
    id="coolwalk", title="CoolWalk (shade routing)", category="planning", returns="layer",
    description="Find the coolest (most tree-shaded) walking route between two "
                "points vs the fastest, for a time of day. Use for heat-safe "
                "pedestrian routing.",
    params={"type": "object", "required": ["from", "to"], "properties": {
        "from": {"type": "array", "items": {"type": "number"}, "description": "[lat, lon] start"},
        "to": {"type": "array", "items": {"type": "number"}, "description": "[lat, lon] destination"},
        "datetime": {"type": "string", "description": "local 'YYYY-MM-DD HH:MM'"},
        "alpha": {"type": "number", "description": "shade aversion, 0-10"}}},
    run=_run_coolwalk))


# Order-of-magnitude public unit costs (USD), editable by the user. Real budgets
# vary widely by region and scope; these are planning-grade defaults, not quotes.
UNIT_COSTS = {
    "tree":                {"measure": "each", "cost": 400,    "label": "tree planting (incl. establishment)"},
    "gazebo":              {"measure": "each", "cost": 15000,  "label": "gazebo / shelter"},
    "bench":               {"measure": "each", "cost": 1200,   "label": "park bench"},
    "streetlight":         {"measure": "each", "cost": 4000,   "label": "street light"},
    "sidewalk":            {"measure": "sqft", "cost": 15,     "label": "concrete sidewalk"},
    "water_main":          {"measure": "lft",  "cost": 400,    "label": "water main"},
    "sewer":               {"measure": "lft",  "cost": 500,    "label": "sewer line"},
    "electric_underground":{"measure": "lft",  "cost": 550,    "label": "underground electric"},
    "bike_lane":           {"measure": "mile", "cost": 130000, "label": "protected bike lane"},
    "road_repave":         {"measure": "sqft", "cost": 9,      "label": "road repaving"},
}


def _run_cost(params: dict) -> dict:
    gdf = _read_fc(params["layer"])
    item = params["item"]
    spec = UNIT_COSTS.get(item, {"measure": "each", "cost": 0, "label": item})
    unit = float(params.get("unit_cost", spec["cost"]))
    m = gdf.to_crs(gdf.estimate_utm_crs())
    measure = spec["measure"]
    if measure == "lft":
        qty, unit_label = m.length.sum() * 3.28084, "linear ft"
    elif measure == "mile":
        qty, unit_label = m.length.sum() / 1609.34, "miles"
    elif measure == "sqft":
        qty, unit_label = m.area.sum() * 10.7639, "sq ft"
    else:
        qty, unit_label = float(len(m)), "each"
    total = round(qty * unit)
    return {"result": {"item": spec["label"], "quantity": round(qty, 1),
                       "quantity_unit": unit_label, "unit_cost_usd": unit,
                       "estimated_total_usd": total,
                       "note": "planning-grade estimate, not a quote"},
            "recipe": {"tool": "cost_estimate", "item": item, "unit_cost": unit}}


register(Tool(
    id="cost_estimate", title="Cost estimate", category="fiscal", returns="value",
    description="Estimate the budget for building something (trees, sidewalk, "
                "water/sewer/electric line, bike lane, gazebo...) from its drawn "
                "geometry x a public unit cost. Points are counted, lines measured "
                "by length, polygons by area. Use for 'how much would this cost'.",
    params={"type": "object", "required": ["layer", "item"], "properties": {
        "layer": {"type": "object", "description": "GeoJSON of what to build"},
        "item": {"type": "string", "enum": list(UNIT_COSTS.keys()),
                 "description": "what is being built"},
        "unit_cost": {"type": "number", "description": "override the default unit cost (USD)"}}},
    run=_run_cost))


def _run_value_per_acre(params: dict) -> dict:
    """Fiscal productivity: taxable value (or revenue) per acre, per parcel.

    The Urban3 / Strong Towns analysis: which land pays for the infrastructure
    that serves it. Dense mixed-use usually far out-produces sprawl per acre.
    """
    gdf = _read_fc(params["layer"])
    field = params.get("value_field", "assessed_value")
    if field not in gdf.columns:
        raise ValueError(f"layer has no '{field}' field (set value_field to your value column)")
    acres = gdf.to_crs(gdf.estimate_utm_crs()).area / 4046.856
    gdf["value_per_acre"] = (gdf[field].astype(float) / acres).round(0)
    tax_rate = params.get("tax_rate")
    if tax_rate:
        gdf["tax_per_acre"] = (gdf["value_per_acre"] * float(tax_rate)).round(0)
    return {"result": _fc(gdf),
            "recipe": {"tool": "value_per_acre", "value_field": field,
                       "total_value_usd": round(float(gdf[field].astype(float).sum())),
                       "total_acres": round(float(acres.sum()), 1)}}


register(Tool(
    id="value_per_acre", title="Value per acre (fiscal productivity)",
    category="fiscal", returns="layer",
    description="Compute taxable value (and optionally tax revenue) per acre for "
                "each parcel, revealing which land is most fiscally productive. "
                "Needs a parcel layer with an assessed-value field.",
    params={"type": "object", "required": ["layer"], "properties": {
        "layer": {"type": "object", "description": "parcel GeoJSON with a value field"},
        "value_field": {"type": "string", "description": "value column (default assessed_value)"},
        "tax_rate": {"type": "number", "description": "optional effective tax rate, e.g. 0.012"}}},
    run=_run_value_per_acre))


def _run_benefit_cost(params: dict) -> dict:
    """Discounted benefit-cost of a project: NPV, benefit-cost ratio, payback."""
    cost = float(params["cost"])
    annual = float(params["annual_benefit"])
    years = int(params.get("years", 20))
    rate = float(params.get("discount_rate", 0.03))
    disc_benefits = sum(annual / (1 + rate) ** t for t in range(1, years + 1))
    npv = disc_benefits - cost
    return {"result": {"npv_usd": round(npv),
                       "benefit_cost_ratio": round(disc_benefits / cost, 2) if cost else None,
                       "payback_years": round(cost / annual, 1) if annual > 0 else None,
                       "horizon_years": years, "discount_rate": rate},
            "recipe": {"tool": "benefit_cost", "cost": cost, "annual_benefit": annual,
                       "years": years, "discount_rate": rate}}


register(Tool(
    id="benefit_cost", title="Benefit-cost / ROI", category="fiscal", returns="value",
    description="Discounted benefit-cost analysis of a project: net present value, "
                "benefit-cost ratio, and payback period. Pair with cost_estimate "
                "(the cost) and an annual benefit (stormwater/energy/health savings).",
    params={"type": "object", "required": ["cost", "annual_benefit"], "properties": {
        "cost": {"type": "number", "description": "upfront capital cost (USD)"},
        "annual_benefit": {"type": "number", "description": "recurring annual benefit (USD/yr)"},
        "years": {"type": "integer", "description": "analysis horizon (default 20)"},
        "discount_rate": {"type": "number", "description": "annual discount rate (default 0.03)"}}},
    run=_run_benefit_cost))


def _run_walkshed(params: dict) -> dict:
    """Isochrone / '15-minute city': the area reachable on foot from a point.

    Builds the walk network around the point, weights edges by walking time, and
    returns the footprint of every street reachable within the time budget.
    """
    import networkx as nx
    from shapely.ops import unary_union

    lat, lon = params["point"]
    minutes = float(params.get("minutes", 15))
    speed_kmh = float(params.get("speed_kmh", 4.8))    # average walking speed
    speed_ms = speed_kmh * 1000 / 3600
    reach_m = (minutes / 60) * speed_kmh * 1000
    pad = (reach_m / 111000) * 1.35
    bbox = (lon - pad, lat - pad, lon + pad, lat + pad)

    G = data.get_graph(data.area_from_bbox(bbox))
    for _, _, d in G.edges(data=True):
        d["time_s"] = d["length"] / speed_ms
    center = routing.nearest_node(G, lat, lon)
    reachable = nx.ego_graph(G, center, radius=minutes * 60, distance="time_s")

    geoms = [d["geometry"] for u, v, d in reachable.edges(data=True)]
    if not geoms:
        raise ValueError("no walkable streets reachable from that point")
    foot = unary_union([g.buffer(25) for g in geoms]).simplify(8)
    area_acres = foot.area / 4046.856
    return {"result": {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {
            "minutes": minutes, "reach_acres": round(area_acres, 1),
            "streets_reached": reachable.number_of_edges()},
        "geometry": json.loads(gpd.GeoSeries([geoutil.geom_to_wgs(foot)]).to_json())["features"][0]["geometry"]}]},
            "recipe": {"tool": "walkshed", "point": [lat, lon], "minutes": minutes}}


register(Tool(
    id="walkshed", title="Walkshed (15-minute city)", category="planning", returns="layer",
    description="The area reachable on foot from a point within a time budget "
                "(a walking isochrone). Use for '15-minute city' access, catchment "
                "areas, or what a location can reach on foot.",
    params={"type": "object", "required": ["point"], "properties": {
        "point": {"type": "array", "items": {"type": "number"}, "description": "[lat, lon] origin"},
        "minutes": {"type": "number", "description": "walk-time budget (default 15)"},
        "speed_kmh": {"type": "number", "description": "walking speed km/h (default 4.8)"}}},
    run=_run_walkshed))


def _run_clearance(params: dict) -> dict:
    """Flag proposed features that violate a clearance distance from utilities.

    e.g. trees within 3 m of a water/electric line. Returns the proposed layer
    with a 'conflict' flag per feature.
    """
    feats = _read_fc(params["features"])
    util = _read_fc(params["utilities"])
    clearance = float(params.get("clearance_m", 3.0))
    fm = feats.to_crs(feats.estimate_utm_crs())
    zone = util.to_crs(fm.crs).buffer(clearance).union_all()
    fm["conflict"] = fm.geometry.intersects(zone)
    conflicts = int(fm["conflict"].sum())
    return {"result": _fc(fm),
            "recipe": {"tool": "clearance_check", "clearance_m": clearance,
                       "conflicts": conflicts, "total": int(len(fm))}}


register(Tool(
    id="clearance_check", title="Utility clearance check", category="planning", returns="layer",
    description="Flag proposed features (e.g. new trees) that fall within a "
                "clearance distance of utility lines (water, sewer, electric), so "
                "you can see conflicts before you plant or build.",
    params={"type": "object", "required": ["features", "utilities"], "properties": {
        "features": {"type": "object", "description": "proposed features GeoJSON (e.g. trees)"},
        "utilities": {"type": "object", "description": "utility lines GeoJSON to keep clear of"},
        "clearance_m": {"type": "number", "description": "required clearance in meters (default 3)"}}},
    run=_run_clearance))


def _run_density(params: dict) -> dict:
    """Aggregate a point layer into H3 hexagons colored by count (a hotspot map)."""
    import h3
    from collections import Counter
    gdf = _read_fc(params["layer"]).to_crs(config.WGS84)
    res = int(params.get("resolution", 8))
    counts = Counter()
    for g in gdf.geometry:
        p = g if g.geom_type == "Point" else g.centroid
        counts[h3.latlng_to_cell(p.y, p.x, res)] += 1
    feats = []
    for cell, c in counts.items():
        ring = [[lng, lat] for lat, lng in h3.cell_to_boundary(cell)]
        ring.append(ring[0])
        feats.append({"type": "Feature", "properties": {"count": c, "h3": cell},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"result": {"type": "FeatureCollection", "features": feats},
            "recipe": {"tool": "density_hexbin", "resolution": res, "cells": len(feats)}}


register(Tool(
    id="density_hexbin", title="Density hotspots (H3)", category="geoprocessing", returns="layer",
    description="Aggregate a point layer into H3 hexagons colored by how many "
                "points fall in each, revealing concentrations (a hotspot map).",
    params={"type": "object", "required": ["layer"], "properties": {
        "layer": {"type": "object", "description": "point GeoJSON to aggregate"},
        "resolution": {"type": "integer", "description": "H3 resolution 7-10 (default 8, higher = finer)"}}},
    run=_run_density))
