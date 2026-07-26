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
import re
from dataclasses import dataclass, field
from typing import Callable

import geopandas as gpd

from . import config, data, pipeline, routing, geoutil


@dataclass
class Tool:
    id: str
    title: str
    description: str          # plain English: read by the user AND the LLM
    params: dict             # JSON Schema of the inputs
    run: Callable            # (params: dict) -> {"result":..., "recipe":...}
    returns: str = "layer"   # layer | table | value
    noun: str = ""           # short name for the layer this makes
    category: str = "shaping layers"  # groups tools in the UI, in plain English


_REGISTRY: dict[str, Tool] = {}


class ParamError(ValueError):
    """Raised when a tool is called with inputs its own schema does not allow."""


_TYPE_OK = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
}


def validate_params(tool: "Tool", params: dict) -> dict:
    """Check ``params`` against the tool's declared schema before running it.

    Every tool publishes a typed input contract; this is what makes it true at
    call time. Without it a missing or mistyped input surfaces as whatever the
    geometry library happens to raise ten frames later, which tells the caller
    nothing about what they got wrong.
    """
    schema = tool.params or {}
    props = schema.get("properties") or {}

    for key in schema.get("required") or []:
        if params.get(key) is None:
            hint = (props.get(key) or {}).get("description")
            raise ParamError(f"{tool.id} needs '{key}'"
                             + (f": {hint}." if hint else "."))

    for key, spec in props.items():
        if params.get(key) is None:
            continue
        value, want = params[key], spec.get("type")
        check = _TYPE_OK.get(want)
        if check and not check(value):
            raise ParamError(f"'{key}' must be {want}, but got "
                             f"{type(value).__name__}.")
        if spec.get("enum") and value not in spec["enum"]:
            raise ParamError(f"'{key}' must be one of: "
                             f"{', '.join(str(o) for o in spec['enum'])}. "
                             f"Got {value!r}.")
        if want == "array":
            lo, hi = spec.get("minItems"), spec.get("maxItems")
            if lo is not None and len(value) < lo:
                raise ParamError(f"'{key}' needs at least {lo} values, "
                                 f"got {len(value)}.")
            if hi is not None and len(value) > hi:
                raise ParamError(f"'{key}' takes at most {hi} values, "
                                 f"got {len(value)}.")
            item_type = (spec.get("items") or {}).get("type")
            item_check = _TYPE_OK.get(item_type)
            if item_check and not all(item_check(x) for x in value):
                raise ParamError(f"every value in '{key}' must be {item_type}.")
        if (want == "object" and value.get("type") == "FeatureCollection"
                and not isinstance(value.get("features"), list)):
            raise ParamError(f"'{key}' is a FeatureCollection with no "
                             f"'features' list.")
    return params


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


def _read_fc(fc: dict, what: str = "layer") -> gpd.GeoDataFrame:
    """A GeoDataFrame from a FeatureCollection, or a readable refusal.

    The emptiness check has to happen BEFORE geopandas sees the list: building a
    frame from no features fails inside the library with a message about the
    ``geometry=`` keyword, which means nothing to a planner and hides which of
    the tool's inputs was actually empty.
    """
    if not isinstance(fc, dict):
        raise ValueError(f"The {what} is not a layer. Pick one from the list, or "
                         f"draw an area on the map.")
    feats = fc.get("features") or []
    if not feats:
        raise ValueError(f"The {what} has no features in it, so there is nothing "
                         f"to work with.")
    gdf = gpd.GeoDataFrame.from_features(feats, crs=config.WGS84)
    if gdf.empty:
        raise ValueError(f"The {what} has no features in it, so there is nothing "
                         f"to work with.")
    return _repair(gdf, what)


def _has_shape(geom) -> bool:
    """Is there a real geometry here at all?"""
    return geom is not None and not geom.is_empty


def _dimension(geom) -> int:
    """2 for anything polygonal, 1 for lines, 0 for points."""
    kind = geom.geom_type
    return 2 if "Polygon" in kind else 1 if "Line" in kind else 0


def _same_dimension(geom, want: int):
    """Keep only the repaired parts that are still the shape we started with.

    ``make_valid`` turns a self-intersecting polygon into real polygons, but it
    can also return a collection carrying stray lines where an edge collapsed.
    Letting those through would swap one wrong number for another.
    """
    import shapely
    from shapely.geometry import GeometryCollection

    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, GeometryCollection) or geom.geom_type.startswith("Multi"):
        parts = [g for g in getattr(geom, "geoms", []) if _dimension(g) == want]
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else shapely.union_all(parts)
    return geom if _dimension(geom) == want else None


def _repair(gdf: gpd.GeoDataFrame, what: str) -> gpd.GeoDataFrame:
    """Fix invalid geometry before anything measures it.

    Municipal parcel exports routinely contain self-intersecting rings. Shapely
    does not raise on them; it returns an answer. A bow-tie polygon measured
    0.0 acres and contained 0 points, which is a confident wrong number of
    exactly the kind this project refuses to ship. Repair here, once, at the
    boundary, so no individual tool has to remember to.
    """
    geom = gdf.geometry
    missing = ~geom.map(_has_shape)
    invalid = (~missing) & (~geom.is_valid)
    if invalid.any():
        wanted = geom[invalid].apply(_dimension)
        fixed = geom[invalid].make_valid()
        gdf = gdf.copy()
        gdf.loc[invalid, "geometry"] = [
            _same_dimension(g, w) for g, w in zip(fixed, wanted)
        ]
    if invalid.any() or missing.any():
        gdf = gdf[gdf.geometry.map(_has_shape)]
    if gdf.empty:
        raise ValueError(
            f"Every shape in the {what} is broken beyond repair or empty, so "
            f"there is nothing to measure. Check the file in whatever produced it.")
    return gdf


# --- geoprocessing tools -----------------------------------------------------
BUFFER_LIMIT_M = 500_000          # 500 km. Past this it is not a planning question.


def _run_buffer(params: dict) -> dict:
    gdf = _read_fc(params["layer"])
    dist = float(params["distance_m"])
    if abs(dist) > BUFFER_LIMIT_M:
        raise ValueError(
            f"A {abs(dist):,.0f} m buffer is larger than branch will draw "
            f"({BUFFER_LIMIT_M:,} m). Check the units: this value is in metres, "
            f"not feet.")
    metric = gdf.to_crs(gdf.estimate_utm_crs())     # grounding: reproject to meters
    metric["geometry"] = metric.buffer(dist)
    # A negative buffer can consume a shape completely. Handing back a feature
    # with no geometry looks like an answer and breaks everything downstream.
    kept = metric[metric.geometry.map(_has_shape)]
    lost = len(metric) - len(kept)
    if kept.empty:
        raise ValueError(
            f"A {dist:,.0f} m buffer removes every shape in this layer entirely, "
            f"so there is nothing left to draw. Use a smaller inward distance.")
    return {"result": _fc(kept),
            "recipe": {"tool": "buffer", "distance_m": dist,
                       "features": int(len(kept)),
                       **({"shapes_consumed": int(lost),
                           "note": f"{lost} shape{'s' if lost != 1 else ''} vanished "
                                   f"entirely at this inward distance and were left out."}
                          if lost else {})}}


register(Tool(
    id="buffer", title="Everything within a distance", noun="Buffer zone", category="shaping layers", returns="layer",
    description="Draws a zone of a set distance in meters around everything in a "
                "layer. Use it for questions like within 400 meters of a park, or for "
                "a setback. Also called a buffer.",
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
    id="spatial_join", title="Combine two layers", noun="Joined layers", category="shaping layers", returns="layer",
    description="Attaches the information from one layer onto another wherever they "
                "overlap, for example tagging each point with the neighborhood it "
                "falls in. Also called a spatial join.",
    params={"type": "object", "required": ["target", "join"], "properties": {
        "target": {"type": "object", "description": "layer to keep (FeatureCollection)"},
        "join": {"type": "object", "description": "layer whose attributes to attach"},
        "predicate": {"type": "string", "enum": ["intersects", "within", "contains"],
                      "default": "intersects"}}},
    run=_run_spatial_join))


def _run_clip(params: dict) -> dict:
    """Keep only the parts of a layer that fall inside a boundary.

    The boundary is usually an area drawn on the map, so this is what turns a
    hand-drawn study area into a real filter: draw the block you care about,
    then trim parcels, trees or streets down to it before costing anything.
    """
    gdf = _read_fc(params["layer"])
    boundary = _read_fc(params["boundary"]).to_crs(gdf.crs)
    mask = (boundary.union_all() if hasattr(boundary, "union_all")
            else boundary.unary_union)
    clipped = gdf.clip(mask)
    if clipped.empty:
        raise ValueError(
            "Nothing in that layer falls inside the boundary, so trimming it "
            "leaves an empty layer. Check that the two are in the same place: "
            "an empty result on a map is indistinguishable from a real finding.")
    return {"result": _fc(clipped),
            "recipe": {"tool": "clip", "kept": int(len(clipped)),
                       "of": int(len(gdf))}}


register(Tool(
    id="clip", title="Trim to an area", noun="Clipped", category="shaping layers", returns="layer",
    description="Keeps only the part of a layer that falls inside a boundary, such as "
                "an area you drew on the map. Use it to focus an analysis on one "
                "block, corridor, or study area. Also called a clip, or intersect.",
    params={"type": "object", "required": ["layer", "boundary"], "properties": {
        "layer": {"type": "object", "description": "the layer to trim (FeatureCollection)"},
        "boundary": {"type": "object",
                     "description": "polygon layer to keep inside, e.g. a drawn area"}}},
    run=_run_clip))


def _coord_sample(fc: dict, limit: int = 200) -> list[tuple[float, float]]:
    """A few raw coordinates, without assuming they mean anything yet."""
    out = []
    def walk(c):
        if len(out) >= limit:
            return
        if (isinstance(c, (list, tuple)) and len(c) >= 2
                and all(isinstance(v, (int, float)) for v in c[:2])):
            out.append((float(c[0]), float(c[1])))
            return
        if isinstance(c, (list, tuple)):
            for part in c:
                walk(part)
    for f in (fc.get("features") or [])[:limit]:
        walk(((f or {}).get("geometry") or {}).get("coordinates"))
    return out


def looks_projected(fc: dict) -> dict:
    """Do these coordinates look like degrees, or like a projected grid?

    GeoJSON is defined as longitude and latitude, but exports from desktop GIS
    routinely carry State Plane feet or UTM meters anyway. Numbers in the
    hundreds of thousands are the giveaway, and catching it here is the
    difference between a clear question and a map of an empty ocean.
    """
    pts = _coord_sample(fc)
    if not pts:
        return {"projected": False}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bad = any(abs(x) > 180 for x in xs) or any(abs(y) > 90 for y in ys)
    return {"projected": bad,
            "x_range": (min(xs), max(xs)), "y_range": (min(ys), max(ys))}


def crs_candidates(near: list | None, limit: int = 8) -> list[dict]:
    """Projected coordinate systems in use where the user is looking."""
    if not near or len(near) != 4:
        return []
    try:
        from pyproj.aoi import AreaOfInterest
        from pyproj.database import query_crs_info
        w, s_, e, n = near
        rows = query_crs_info(auth_name="EPSG", pj_types=["PROJECTED_CRS"],
                              area_of_interest=AreaOfInterest(w, s_, e, n),
                              contains=False)
    except Exception:
        return []
    seen, out = set(), []
    for c in rows:
        name = c.name
        if name in seen:
            continue
        seen.add(name)
        out.append({"code": f"EPSG:{c.code}", "name": name})
        if len(out) >= limit:
            break
    return out


def _run_reproject(params: dict) -> dict:
    """Move a layer from the coordinate system it was drawn in into lon/lat.

    Needed whenever a file arrives from desktop GIS in State Plane or UTM. The
    result is checked: if the numbers still do not look like degrees, the chosen
    system was wrong and saying so beats drawing it in the wrong hemisphere.
    """
    layer = params["layer"]
    from_crs = str(params.get("from_crs") or "").strip()
    if not from_crs:
        cands = crs_candidates(params.get("near"))
        hint = ("Likely systems where you are looking: "
                + "; ".join(f"{c['code']} ({c['name']})" for c in cands)
                if cands else
                "Open the file's .prj or metadata to find its EPSG code.")
        raise ValueError("Tell me which coordinate system this layer is already "
                         "in, as an EPSG code. " + hint)

    gdf = gpd.GeoDataFrame.from_features(layer.get("features", []))
    if gdf.empty:
        raise ValueError("input layer has no features")
    try:
        gdf = gdf.set_crs(from_crs, allow_override=True)
        moved = gdf.to_crs(config.WGS84)
    except Exception as exc:
        raise ValueError(f"{from_crs!r} is not a coordinate system pyproj "
                         f"recognises ({exc}).") from None

    check = looks_projected(json.loads(moved.to_json()))
    if check.get("projected"):
        raise ValueError(
            f"Reprojecting from {from_crs} did not produce longitude and "
            f"latitude (it gave x {check['x_range'][0]:.0f} to "
            f"{check['x_range'][1]:.0f}), so that is not the right system for "
            f"this file.")

    # A wrong EPSG often yields coordinates that are perfectly valid and
    # completely elsewhere: read as Web Mercator, a New York file lands in the
    # Gulf of Guinea. Range alone cannot catch that, but if the caller said
    # where the data belongs, distance can.
    near = params.get("near")
    if near and len(near) == 4:
        w, s_, e, n = near
        minx, miny, maxx, maxy = moved.total_bounds
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        pad = max(abs(e - w), abs(n - s_), 0.5) * 5.0
        if not (w - pad <= cx <= e + pad and s_ - pad <= cy <= n + pad):
            raise ValueError(
                f"Read as {from_crs}, this layer lands at {cy:.3f}, {cx:.3f}, "
                f"which is nowhere near where you are looking "
                f"({(s_ + n) / 2:.3f}, {(w + e) / 2:.3f}). That is the signature "
                f"of the wrong EPSG code, so branch will not place it there.")
    return {"result": _fc(moved),
            "recipe": {"tool": "reproject", "from_crs": from_crs,
                       "to_crs": config.WGS84, "features": int(len(moved))}}


register(Tool(
    id="reproject", title="Fix a layer's coordinate system", noun="Reprojected",
    category="shaping layers", returns="layer",
    description="Convert a layer that was drawn in a projected coordinate system "
                "(State Plane feet, UTM meters, a local grid) into longitude and "
                "latitude so it lands in the right place. Use it when a file from "
                "ArcGIS or QGIS appears in the wrong part of the world, or is "
                "rejected for having coordinates in the hundreds of thousands. "
                "Give the EPSG code the file is already in.",
    params={"type": "object", "required": ["layer"], "properties": {
        "layer": {"type": "object", "description": "the layer to convert"},
        "from_crs": {"type": "string",
                     "description": "EPSG code the data is currently in, e.g. EPSG:2263"},
        "near": {"type": "array", "items": {"type": "number"}, "minItems": 4,
                 "maxItems": 4,
                 "description": "optional [west, south, east, north] of where the "
                                "data belongs, used to suggest likely systems"}}},
    run=_run_reproject))


WORKSPACE_ACTIONS = {
    "show_overlay": "turn a map overlay on (buildings, roads, water, green)",
    "hide_overlay": "turn a map overlay off",
    "set_basemap": "switch the basemap (dark, street, satellite)",
    "fly_to": "move the map to a longitude and latitude, optionally a zoom",
    "open_table": "open the attribute table for a layer, by name",
    "check_layer": "open the data-quality check for a layer, by name",
    "zoom_to_layer": "frame a layer on the map, by name",
    "hide_layer": "hide a layer without deleting it, by name",
    "show_layer": "show a hidden layer, by name",
    "set_dock": "put the results panel on the right or the bottom",
}


def _run_workspace(params: dict) -> dict:
    """Ask the browser to do something to the workspace.

    The assistant can arrange the map and panels, not only compute. Nothing here
    touches data or produces a number: it is validated, recorded in the audit
    trail like any other step, and then carried out by the page. Keeping it a
    real tool means the model cannot invent an action that does not exist.
    """
    action = str(params.get("action", "")).strip()
    if action not in WORKSPACE_ACTIONS:
        raise ValueError(f"{action!r} is not something branch can do to the "
                         f"workspace. Available: " + ", ".join(sorted(WORKSPACE_ACTIONS)) + ".")
    payload = {"action": action}
    for key in ("target", "lon", "lat", "zoom"):
        if params.get(key) is not None:
            payload[key] = params[key]
    return {"result": {"workspace": payload},
            "recipe": {"tool": "workspace", **payload}}


register(Tool(
    id="workspace", title="Arrange the workspace", noun="Workspace",
    category="map data", returns="value",
    description="Change what the user is looking at: turn a map overlay on or "
                "off, switch the basemap, move the map somewhere, open a layer's "
                "table or data check, show or hide a layer, or move the results "
                "panel. Use it to set the scene for an answer, for example "
                "turning on Buildings before talking about them. It never "
                "computes anything and never changes data.",
    params={"type": "object", "required": ["action"], "properties": {
        "action": {"type": "string", "enum": sorted(WORKSPACE_ACTIONS),
                   "description": "; ".join(f"{k}: {v}" for k, v in sorted(WORKSPACE_ACTIONS.items()))},
        "target": {"type": "string",
                   "description": "which overlay, layer name, basemap or side the action applies to"},
        "lon": {"type": "number", "description": "longitude, for fly_to"},
        "lat": {"type": "number", "description": "latitude, for fly_to"},
        "zoom": {"type": "number", "description": "zoom level, for fly_to"}}},
    run=_run_workspace))


def _run_boundary(params: dict) -> dict:
    """Find a real jurisdiction and return its border as a polygon.

    This is what lets someone say "the Bronx" and then scope every other tool to
    it. Borders come from OpenStreetMap via Nominatim, which is key-free and
    worldwide, so it works for a county, a borough, a town, or a neighborhood.
    """
    import requests
    name = str(params["name"]).strip()
    kind_wanted = (params.get("kind") or "").strip().lower()
    ua = {"User-Agent": "branch (open-source city planning; planwithbranch.com)"}
    AREA_KINDS = {"county", "city", "town", "village", "borough", "suburb",
                  "state", "municipality", "district", "city_district",
                  "neighbourhood", "quarter", "region", "province", "hamlet"}
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": name, "format": "json", "limit": 8},
                     headers=ua, timeout=25)
    r.raise_for_status()
    rows = [x for x in r.json()
            if x.get("osm_type") in ("relation", "way")
            and (x.get("addresstype") or x.get("type")) in AREA_KINDS]
    if kind_wanted:
        rows = [x for x in rows
                if (x.get("addresstype") or x.get("type")) == kind_wanted] or rows
    if not rows:
        raise ValueError(f"No mapped jurisdiction called {name!r} was found. "
                         f"Try a fuller name, such as 'Bronx County, New York'.")
    pick = rows[0]
    prefix = {"relation": "R", "way": "W"}[pick["osm_type"]]
    r2 = requests.get("https://nominatim.openstreetmap.org/lookup",
                      params={"osm_ids": f"{prefix}{pick['osm_id']}",
                              "format": "json", "polygon_geojson": 1},
                      headers=ua, timeout=30)
    r2.raise_for_status()
    got = r2.json()
    geom = (got[0].get("geojson") if got else None)
    if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"{name!r} exists but has no mapped border in "
                         f"OpenStreetMap, so there is nothing to plan inside.")
    label = (got[0].get("display_name") or name).split(",")[0]
    kind = got[0].get("addresstype") or got[0].get("type") or "area"
    return {"result": {"type": "FeatureCollection", "features": [{
                "type": "Feature",
                "properties": {"name": label, "kind": kind,
                               "full_name": got[0].get("display_name"),
                               "source": "OpenStreetMap via Nominatim"},
                "geometry": geom}]},
            "recipe": {"tool": "boundary", "name": label, "kind": kind,
                       "osm_type": pick["osm_type"], "osm_id": int(pick["osm_id"])}}


register(Tool(
    id="boundary", title="Find a place's border", noun="Boundary",
    category="map data", returns="layer",
    description="Look up the real border of a county, city, town, borough, "
                "district or neighborhood and put it on the map as an area. Use "
                "it to scope work to one jurisdiction, for example 'the Bronx' "
                "or 'Boulder, Colorado', then clip other layers to it. Borders "
                "come from OpenStreetMap and cover the whole world.",
    params={"type": "object", "required": ["name"], "properties": {
        "name": {"type": "string",
                 "description": "the place, e.g. 'Bronx County, New York'"},
        "kind": {"type": "string",
                 "description": "optional preferred kind: county, city, town, "
                                "borough, neighbourhood"}}},
    run=_run_boundary))


CENSUS_BASE = ("https://tigerweb.geo.census.gov/arcgis/rest/services/Census2020/"
               "tigerWMS_Census2020/MapServer")
CENSUS_LEVELS = {                     # plain name -> TIGERweb layer id
    "tract": 6, "block group": 8, "block": 10,
    "county subdivision": 20, "place": 26, "county": 82, "state": 80,
}
CENSUS_CAP = 3000


def _run_census_geo(params: dict) -> dict:
    """Official census geography for an area, with GEOID on every feature.

    Planners argue in tracts and block groups, not in hexagons, and GEOID is the
    key that joins any census or assessor table to a shape. Free and key-free.
    """
    import json as _json
    import requests
    from . import sources

    bbox = params["bbox"]
    level = str(params.get("level", "tract")).strip().lower()
    if level not in CENSUS_LEVELS:
        raise ValueError(f"{level!r} is not a census level. Choose one of: "
                         + ", ".join(sorted(CENSUS_LEVELS)) + ".")
    sources.require("us_census_geography", tuple(bbox))

    w, s_, e, n = bbox
    envelope = _json.dumps({"xmin": w, "ymin": s_, "xmax": e, "ymax": n,
                            "spatialReference": {"wkid": 4326}})
    layer = CENSUS_LEVELS[level]
    common = {"where": "1=1", "geometry": envelope,
              "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326}

    # A view bigger than this cannot be answered usefully, and asking the census
    # service to count it just times out slowly. Refuse fast and say why.
    span = abs(e - w) * abs(n - s_)
    MAX_SPAN = {"block": 0.02, "block group": 0.2, "tract": 1.0,
                "county subdivision": 4.0, "place": 8.0,
                "county": 60.0, "state": 4000.0}[level]
    if span > MAX_SPAN:
        raise ValueError(f"That view is too large to pull {level}s for. Zoom in, "
                         f"or choose a coarser level such as "
                         f"{'county' if level != 'county' else 'state'}.")

    # Ask how many first: a silent truncation is worse than a refusal.
    try:
        probe = requests.get(f"{CENSUS_BASE}/{layer}/query",
                             params={**common, "returnCountOnly": "true", "f": "json"},
                             timeout=25)
        probe.raise_for_status()
        count = int(probe.json().get("count", 0))
    except requests.exceptions.Timeout:
        raise ValueError(f"The census service took too long to count the {level}s "
                         f"in this view. Zoom in and try again.") from None
    if count == 0:
        raise ValueError(f"No census {level}s fall in this view. If you are "
                         f"outside the United States, this source does not reach.")
    if count > CENSUS_CAP:
        raise ValueError(f"That view covers {count:,} {level}s, more than the "
                         f"{CENSUS_CAP:,} branch will fetch at once. Zoom in, or "
                         f"choose a coarser level such as tract or county.")

    r = requests.get(f"{CENSUS_BASE}/{layer}/query",
                     params={**common, "outFields": "GEOID,NAME,BASENAME",
                             "returnGeometry": "true", "geometryPrecision": 6,
                             "f": "geojson"}, timeout=90)
    r.raise_for_status()
    fc = r.json()
    feats = fc.get("features") or []
    if not feats:
        raise ValueError(f"The census service returned no {level} shapes for this view.")
    return {"result": {"type": "FeatureCollection", "features": feats},
            "recipe": {"tool": "census_geo", "level": level, "bbox": list(bbox),
                       "features": len(feats), "vintage": "2020",
                       "source": "US Census Bureau TIGERweb"}}


register(Tool(
    id="census_geo", title="Official census areas", noun="Census areas",
    category="map data", returns="layer",
    description="Fetch official US census geography for the current view: "
                "tracts, block groups, blocks, places, county subdivisions, "
                "counties or states. Every shape carries its GEOID, which is the "
                "key that joins census, ACS or assessor tables to the map. Use it "
                "when an analysis has to be reported in the units a council or a "
                "grant expects. United States only.",
    params={"type": "object", "required": ["bbox"], "properties": {
        "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4,
                 "maxItems": 4, "description": "[west, south, east, north] in degrees"},
        "level": {"type": "string",
                  "enum": ["tract", "block group", "block", "county subdivision",
                           "place", "county", "state"],
                  "default": "tract"}}},
    run=_run_census_geo))


def _run_filter(params: dict) -> dict:
    """Keep only the features whose attribute passes a test."""
    gdf = _read_fc(params["layer"])
    field, op = params["field"], params.get("op", "equals")
    value = params.get("value")
    if field not in gdf.columns:
        have = ", ".join([c for c in gdf.columns if c != "geometry"][:12]) or "none"
        raise ValueError(f"This layer has no field called {field!r}. "
                         f"Fields it does have: {have}.")
    col = gdf[field]
    if op in ("greater_than", "less_than", "at_least", "at_most"):
        nums = gpd.pd.to_numeric(col, errors="coerce")
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{op} needs a number, but got {value!r}.")
        mask = {"greater_than": nums > v, "less_than": nums < v,
                "at_least": nums >= v, "at_most": nums <= v}[op]
    elif op == "contains":
        mask = col.astype(str).str.contains(str(value), case=False, na=False)
    elif op == "not_equals":
        mask = col.astype(str) != str(value)
    elif op == "is_present":
        mask = col.notna() & (col.astype(str) != "")
    else:
        mask = col.astype(str) == str(value)
    kept = gdf[mask.fillna(False)]
    if kept.empty:
        raise ValueError(f"No features matched {field} {op.replace('_',' ')} "
                         f"{value!r}, so there is nothing to draw.")
    return {"result": _fc(kept),
            "recipe": {"tool": "filter", "field": field, "op": op,
                       "value": value, "kept": int(len(kept)),
                       "of": int(len(gdf))}}


register(Tool(
    id="filter", title="Keep only what matches", noun="Filtered",
    category="shaping layers", returns="layer",
    description="Keep only the features in a layer whose attribute passes a "
                "test, for example only schools, only parcels worth more than a "
                "number, or only roads whose name contains something. Say which "
                "field to test and how. Also called a filter, or a definition query.",
    params={"type": "object", "required": ["layer", "field"], "properties": {
        "layer": {"type": "object", "description": "the layer to filter"},
        "field": {"type": "string", "description": "the attribute to test"},
        "op": {"type": "string",
               "enum": ["equals", "not_equals", "contains", "greater_than",
                        "less_than", "at_least", "at_most", "is_present"],
               "default": "equals"},
        "value": {"type": "string", "description": "what to compare against"}}},
    run=_run_filter))


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
    id="osm", title="Get map data for this view", noun="Map data", category="map data", returns="layer",
    description="Pulls real features (parks, schools, shops, restaurants, transit "
                "stops, roads) straight from OpenStreetMap for wherever the map is "
                "pointed. Free, and no key needed. Also called an OSM or Overpass "
                "query.",
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
    id="coolwalk", title="Find the shadiest walk", noun="Shady route", category="getting around", returns="layer",
    description="Compares the fastest walking route with the most tree-shaded one "
                "between two points at a given time of day, so you can see what shade "
                "costs in extra minutes. New York only, because it uses the city "
                "street tree census. Also called shade routing.",
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
    id="cost_estimate", title="What will this cost to build?", noun="Cost estimate", category="money", returns="value",
    description="Budget for building something: trees, sidewalk, bike lane, water "
                "main, sewer, underground electric, a bench, a streetlight, or "
                "repaving. Give it a layer of what you plan to build and it prices "
                "the job from editable public unit costs. Also called a cost "
                "estimate.",
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
    id="value_per_acre", title="Which land pays for itself?", noun="Value per acre",
    category="money", returns="layer",
    description="Ranks parcels by how much taxable value each acre carries, the "
                "number that shows which blocks fund the city and which are "
                "subsidized by them. Needs a parcel layer with a value column. Also "
                "called value per acre, or fiscal productivity.",
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
    id="benefit_cost", title="Is it worth it?", noun="Benefit and cost", category="money", returns="value",
    description="Weighs an upfront cost against a yearly benefit over time and "
                "returns the net present value, the benefit-to-cost ratio, and the "
                "year it pays itself back. Also called benefit-cost analysis, or ROI.",
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
    center = routing.nearest_node(G, lat, lon)

    # Hills change what a walk costs. Sample elevation once for the whole area,
    # then price each edge by its own grade rather than pretending the ground is
    # flat. Where there is no elevation data the flat assumption stays, and the
    # recipe says so instead of implying terrain was considered.
    from . import terrain
    dem = terrain.fetch_dem(bbox) if params.get("terrain", True) else None
    elev = {}
    if dem is not None:
        nodes = list(G.nodes)
        lonlats = []
        for nid in nodes:
            nlat, nlon = geoutil.xy_to_latlon(G.nodes[nid]["x"], G.nodes[nid]["y"])
            lonlats.append((nlon, nlat))
        zs = terrain.sample(dem, lonlats)
        elev = {nid: z for nid, z in zip(nodes, zs)}

    steep = {"count": 0, "length_m": 0.0}

    def walk_time(u, v, edge):
        best = min((e.get("length", 0.0) for e in edge.values()), default=0.0)
        if not elev:
            return best / speed_ms
        slope = terrain.edge_slope(elev.get(u, float("nan")),
                                  elev.get(v, float("nan")), best)
        if abs(slope) >= 0.0833:
            steep["count"] += 1
            steep["length_m"] += best
        kmh = terrain.tobler_speed(slope, speed_kmh)
        return best / max(kmh * 1000.0 / 3600.0, 0.1)

    reachable = nx.ego_graph(G, center, radius=minutes * 60, distance=walk_time)

    geoms = [d["geometry"] for u, v, d in reachable.edges(data=True)]
    if not geoms:
        raise ValueError("no walkable streets reachable from that point")
    foot = unary_union([g.buffer(25) for g in geoms]).simplify(8)
    area_acres = foot.area / 4046.856
    return {"result": {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {
            "minutes": minutes, "reach_acres": round(area_acres, 1),
            "terrain": "measured from USGS 3DEP elevation" if elev
                       else "flat ground assumed, no elevation data here",
            "streets_reached": reachable.number_of_edges()},
        "geometry": json.loads(gpd.GeoSeries([geoutil.geom_to_wgs(foot)]).to_json())["features"][0]["geometry"]}]},
            "recipe": {"tool": "walkshed", "point": [lat, lon], "minutes": minutes,
                       "speed_kmh": speed_kmh,
                       "terrain": ("USGS 3DEP elevation, Tobler hiking function"
                                   if elev else "flat ground assumed"),
                       "steep_edges_over_8_33pct": steep["count"],
                       "steep_length_m": round(steep["length_m"])}}


register(Tool(
    id="walkshed", title="What is within a short walk?", noun="Walkshed", category="getting around", returns="layer",
    description="Shows everywhere a person can actually reach on foot in a set number "
                "of minutes, following real streets instead of drawing a circle. Use "
                "it for 15-minute-city access, catchment areas, or what a site can "
                "reach. Also called a walkshed, or isochrone.",
    params={"type": "object", "required": ["point"], "properties": {
        "point": {"type": "array", "items": {"type": "number"}, "description": "[lat, lon] origin"},
        "minutes": {"type": "number", "description": "walk-time budget (default 15)"},
        "speed_kmh": {"type": "number", "description": "walking speed on the flat, km/h (default 4.8)"},
        "terrain": {"type": "boolean",
                    "description": "account for hills using elevation data (default true)"}}},
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
    id="clearance_check", title="What is too close to utility lines?", noun="Clearance check", category="safety", returns="layer",
    description="Flags anything you plan to put in the ground that sits within a safe "
                "distance of water, sewer, gas, or electric lines, so conflicts show "
                "up before design does. Also called a clearance or setback check.",
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


def _run_hotspots(params: dict) -> dict:
    """Which clusters are real, and which are noise.

    Aggregates to H3 like the density tool, then runs Getis-Ord Gi* with
    permutation p-values and a false-discovery correction, so each cell is
    labelled with a confidence rather than just a colour.
    """
    import h3
    import numpy as np
    from collections import defaultdict
    from . import stats

    gdf = _read_fc(params["layer"]).to_crs(config.WGS84)
    res = int(params.get("resolution", 8))
    field = params.get("value_field")
    perms = max(99, min(int(params.get("permutations", 199)), 999))
    seed = int(params.get("seed", 0))

    bucket = defaultdict(list)
    for geom, row in zip(gdf.geometry, gdf.to_dict("records")):
        if geom is None or geom.is_empty:
            continue
        pt = geom if geom.geom_type == "Point" else geom.centroid
        cell = h3.latlng_to_cell(pt.y, pt.x, res)
        if field:
            v = row.get(field)
            try:
                bucket[cell].append(float(v))
            except (TypeError, ValueError):
                continue          # a row with no usable number adds nothing
        else:
            bucket[cell].append(1.0)

    cells = sorted(bucket)
    if not cells:
        raise ValueError("Nothing landed in a cell. Check the layer has points "
                         "and, if you named a value field, that it holds numbers.")
    values = np.array([sum(bucket[c]) for c in cells], dtype=np.float64)

    w = stats.neighbor_matrix(cells)
    out = stats.getis_ord(values, w, permutations=perms, seed=seed)

    feats = []
    for i, cell in enumerate(cells):
        ring = [[lng, lat] for lat, lng in h3.cell_to_boundary(cell)]
        ring.append(ring[0])
        feats.append({"type": "Feature", "properties": {
            "h3": cell,
            "value": round(float(values[i]), 4),
            "z_score": round(float(out["z"][i]), 3),
            "p_value": round(float(out["p"][i]), 5),
            "p_permutation": round(float(out["p_perm"][i]), 4),
            "significance": out["classes"][i],
        }, "geometry": {"type": "Polygon", "coordinates": [ring]}})

    hot = sum(1 for c in out["classes"] if c.startswith("hot"))
    cold = sum(1 for c in out["classes"] if c.startswith("cold"))
    return {"result": {"type": "FeatureCollection", "features": feats},
            "recipe": {"tool": "hotspots", "resolution": res,
                       "value_field": field or "count of features",
                       "permutations": perms, "seed": seed,
                       "cells": len(cells), "hot_cells": hot, "cold_cells": cold,
                       "method": "Getis-Ord Gi*; significance from the analytic "
                                 "z-score corrected for multiple testing "
                                 "(Benjamini-Hochberg); a permutation p-value is "
                                 "reported alongside as a robustness check",
                       "permutation_p_floor": round(1.0 / (perms + 1.0), 4)}}


register(Tool(
    id="hotspots", title="Is this cluster real?", noun="Hot and cold spots",
    category="shaping layers", returns="layer",
    description="Tests whether the clusters in a layer are more than chance. "
                "Groups features into hexagons and labels each one hot, cold or "
                "not significant with a confidence level, so a finding can stand "
                "up in a hearing or a grant application rather than just looking "
                "convincing. Also called Getis-Ord Gi*, or hot spot analysis.",
    params={"type": "object", "required": ["layer"], "properties": {
        "layer": {"type": "object", "description": "the point layer to test"},
        "value_field": {"type": "string",
                        "description": "number to test, e.g. value or complaints. "
                                       "Leave empty to test how many features fall in each cell"},
        "resolution": {"type": "integer", "description": "H3 resolution 7-10 (default 8)"},
        "permutations": {"type": "integer", "description": "randomisations, default 199"},
        "seed": {"type": "integer", "description": "seed, so the same run repeats exactly"}}},
    run=_run_hotspots))


register(Tool(
    id="density_hexbin", title="Where is it most concentrated?", noun="Density", category="shaping layers", returns="layer",
    description="Groups points into hexagons and colors them by how many fall in "
                "each, so clusters and empty gaps are obvious at a glance. Also "
                "called a density hexbin, or H3 aggregation.",
    params={"type": "object", "required": ["layer"], "properties": {
        "layer": {"type": "object", "description": "point GeoJSON to aggregate"},
        "resolution": {"type": "integer", "description": "H3 resolution 7-10 (default 8, higher = finer)"}}},
    run=_run_density))


def _run_arcgis(params: dict) -> dict:
    from . import esri

    url = str(params.get("url", "")).strip()
    bbox = params["bbox"]
    where = str(params.get("where", "") or "1=1").strip()
    limit = int(params.get("limit", 2000) or 2000)
    try:
        got = esri.fetch(url, tuple(bbox), where=where, limit=limit)
    except esri.EsriError as exc:
        raise ValueError(str(exc)) from None
    info = got["info"]
    return {"result": {"type": "FeatureCollection", "features": got["features"]},
            "recipe": {"tool": "arcgis", "url": url, "layer": info["name"],
                       "bbox": list(bbox), "where": where,
                       "features": len(got["features"]),
                       "geometry_type": info["geometry_type"],
                       "source": f"{info['name']}, published by its own agency "
                                 f"on ArcGIS REST"}}


register(Tool(
    id="arcgis", title="Bring in a layer your town already publishes",
    noun="Local layer", category="map data", returns="layer",
    description="Pull parcels, zoning, flood zones, capital projects or any "
                "other layer straight from an ArcGIS REST service, the way most "
                "US towns, counties and states already publish their data. Paste "
                "the layer URL, ending in the layer number. No account and no key: "
                "if the agency publishes it openly, branch can read it. This is "
                "how you get the local data OpenStreetMap does not have. Also "
                "called an ArcGIS FeatureServer or MapServer query.",
    params={"type": "object", "required": ["url", "bbox"], "properties": {
        "url": {"type": "string",
                "description": "the layer URL, ending in its number, "
                               "for example .../FeatureServer/0"},
        "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4,
                 "maxItems": 4, "description": "[west, south, east, north] in degrees"},
        "where": {"type": "string", "default": "1=1",
                  "description": "optional filter, written as SQL, "
                                 "for example LANDUSE = 'VACANT'"},
        "limit": {"type": "integer", "default": 2000, "minimum": 1, "maximum": 6000}}},
    run=_run_arcgis))


# The abutter list. Every US zoning hearing needs one: the statute says notify
# everyone whose property lies within N feet of the subject property, and today
# that is produced by hand from a paper map or bought from a title company.
# Three things make it right or wrong, and none of them are visible in the output:
# the distance must be measured in meters on the ground rather than in degrees,
# it must run from the property LINE and not the centroid, and the subject
# parcel must not appear in its own notice list.
FEET_PER_M = 3.280839895

# Column names assessors actually use. Matched case-insensitively, in order.
OWNER_HINTS = ["owner", "own_name", "ownername", "owner_name", "owner1",
               "deed_owner", "taxpayer", "prop_owner", "grantee"]
ADDR_HINTS = ["mail_addr", "mailing", "mail_add", "owner_addr", "own_addr",
              "address", "situs", "prop_addr", "site_addr", "location",
              "street", "addr"]     # bare "addr" last: SITE_ADDR is still an address
# A column can contain an owner hint and still not be a name: OWNER_ADDRESS,
# OWNER_CITY, OWNER_OCCUPIED and NO_OWNER_FLAG all contain "owner". Matching one
# of these put mailing addresses, or a Y/N flag, in the owner column of a legal
# notice.
NOT_A_NAME = ["addr", "address", "city", "state", "zip", "postal", "phone",
              "email", "_id", "id_", "date", "class", "code", "flag",
              "occupied", "count", "num", "type", "status", "no_", "pct",
              "year", "acre", "area", "value", "amount"]
# "ADDRESS_ID" is a key, not somewhere you post a letter.
NOT_AN_ADDRESS = ["_id", "id_", "flag", "code", "type", "status", "count",
                  "date", "num_"]
# Assessors often split a name across columns. Serving "Nguyen" is not serving
# "Alice Nguyen", so the parts are put back together.
NAME_PARTS = [("owner_first_name", "owner_last_name"), ("first_name", "last_name"),
              ("owner_first", "owner_last"), ("fname", "lname"),
              ("firstname", "lastname")]


def _pick_field(columns, hints, exclude=()):
    """The first column whose name looks like what we are after, or None.

    Returns None rather than a guess when nothing matches. A notice list sent to
    the wrong column is a hearing that gets challenged, so an exact name always
    beats a substring, and a substring match is refused outright when the column
    name also says it holds something else.
    """
    lower = {str(c).lower(): c for c in columns}
    for hint in hints:
        for low, actual in lower.items():
            if low == hint:
                return actual
    for hint in hints:
        for low, actual in lower.items():
            if hint in low and not any(bad in low for bad in exclude):
                return actual
    return None


def owner_col_guess(frame, params):
    """The owner column, resolved the same way the tool will resolve it."""
    return params.get("owner_field") or _pick_field(
        frame.columns, OWNER_HINTS, exclude=NOT_A_NAME)


def _run_notice_list(params: dict) -> dict:
    import pandas as pd

    parcels_in = len((params.get("parcels") or {}).get("features") or [])
    parcels = _read_fc(params["parcels"], "parcel layer")
    subject = _read_fc(params["subject"], "subject property")
    lost_on_input = parcels_in - len(parcels)

    # A notice radius runs from a property line. A point or a line has no
    # boundary to run from, and measuring from it would silently produce a
    # circle around a dot rather than around the lot.
    flat = subject.geometry.map(lambda g: g.area == 0).all()
    if flat:
        kinds = ", ".join(sorted({g.geom_type for g in subject.geometry}))
        raise ValueError(
            f"The subject property has no area to measure from ({kinds}). A "
            f"notice radius runs from the property line, so branch needs the "
            f"lot itself. Draw or select the parcel rather than a point.")

    feet = float(params.get("distance_ft", 200))
    if feet <= 0:
        raise ValueError("The notice radius has to be a positive distance in feet.")
    metres = feet / FEET_PER_M

    # Measure on the ground. Buffering in degrees is the bug that reported a
    # Los Angeles area 47.6% too large, and here it would silently widen or
    # narrow a legally defined radius.
    crs = parcels.estimate_utm_crs()
    p_m = parcels.to_crs(crs)
    s_m = subject.to_crs(crs)

    # From the property line, not the centroid. A statute says "within 200 feet
    # of the property", and on a deep lot those differ by more than the radius.
    ring = s_m.geometry.union_all().buffer(metres)

    hit = p_m[p_m.geometry.intersects(ring)].copy()
    if hit.empty:
        raise ValueError(
            f"No parcels fall within {feet:g} feet of the subject property. Check "
            f"that the parcel layer covers this area and that the subject is in "
            f"the right place.")

    # The applicant is not an abutter of their own application. Identity has to
    # be MUTUAL though: "more than half of this parcel is under the subject" is
    # also true of every condo unit sharing one footprint, and dropping those
    # removes real owners from a legal notice.
    own = s_m.geometry.union_all()
    own_area = own.area

    def _is_the_subject(g):
        if not g.area or not own_area:
            return False
        shared = g.intersection(own).area
        return shared > 0.9 * g.area and shared > 0.9 * own_area

    is_subject = hit.geometry.apply(_is_the_subject)
    excluded = int(is_subject.sum())
    # Several parcels can legitimately be co-extensive with the subject (a lot
    # split into condo units). If they do not all belong to the same owner,
    # branch will not choose which ones are the applicant.
    if excluded > 1 and owner_col_guess(hit, params) is not None:
        col = owner_col_guess(hit, params)
        names = {str(v) for v in hit.loc[is_subject, col].tolist()}
        if len(names) > 1:
            raise ValueError(
                f"{excluded} parcels occupy the same footprint as the subject "
                f"property, and they have different owners: "
                f"{', '.join(sorted(names)[:6])}. branch will not guess which one "
                f"the application is about, because the others are abutters who "
                f"must be served. Pass the subject as that single parcel.")
    hit = hit[~is_subject]
    if hit.empty:
        raise ValueError(f"Within {feet:g} feet there is only the subject property "
                         f"itself, so there is nobody to notify.")

    owner_col = params.get("owner_field") or _pick_field(
        hit.columns, OWNER_HINTS, exclude=NOT_A_NAME)
    addr_col = params.get("address_field") or _pick_field(
        hit.columns, ADDR_HINTS, exclude=NOT_AN_ADDRESS)
    # A name split across two columns is one name.
    first_col = last_col = None
    if not params.get("owner_field"):
        lower = {str(c).lower(): c for c in hit.columns}
        for first, last in NAME_PARTS:
            if first in lower and last in lower:
                first_col, last_col = lower[first], lower[last]
                owner_col = last_col
                break
    for name, col in (("owner_field", owner_col), ("address_field", addr_col)):
        if col is not None and col not in hit.columns:
            raise ValueError(f"There is no column called {col!r} in the parcel "
                             f"layer. Its columns are: "
                             f"{', '.join(map(str, list(hit.columns)[:25]))}.")

    hit["distance_ft"] = (hit.geometry.distance(own) * FEET_PER_M).round(1)
    hit = hit.sort_values("distance_ft")

    # The same parcel appearing twice in an export becomes two notices to one
    # person and an inflated count of who was served.
    dupe_key = [c for c in (owner_col_guess(hit, params),) if c] + ["distance_ft"]
    before = len(hit)
    hit = hit.drop_duplicates(subset=dupe_key) if len(dupe_key) > 1 else hit
    duplicates = before - len(hit)

    out = hit.to_crs(parcels.crs if parcels.crs else "EPSG:4326")
    def _name(row):
        if first_col and last_col:
            parts = [str(row[first_col] or "").strip(), str(row[last_col] or "").strip()]
            joined = " ".join(p for p in parts if p and p.lower() != "nan")
            return joined or None
        return str(row[owner_col]) if owner_col else None

    notice = []
    for _, row in hit.iterrows():
        notice.append({
            "owner": _name(row),
            "address": str(row[addr_col]) if addr_col else None,
            "distance_ft": float(row["distance_ft"]),
        })
    unnamed = sum(1 for n in notice if not n["owner"] or n["owner"] == "nan")

    note_bits = []
    if lost_on_input:
        note_bits.append(f"{lost_on_input} parcel"
                         f"{'s' if lost_on_input != 1 else ''} in the file had no "
                         f"usable shape and could not be checked at all. Look "
                         f"{'them' if lost_on_input != 1 else 'it'} up by hand: "
                         f"a missing shape is not the same as a parcel outside "
                         f"the radius.")
    if duplicates:
        one = duplicates == 1
        note_bits.append(f"{duplicates} duplicate record{'' if one else 's'} "
                         f"{'was' if one else 'were'} removed, so nobody is "
                         f"served twice.")
    if owner_col is None:
        note_bits.append("No column in this parcel layer looks like an owner name, "
                         "so the list has shapes but no names. Name the column with "
                         "owner_field if you know it.")
    if unnamed:
        note_bits.append(f"{unnamed} of these parcels have no owner recorded in the "
                         f"data. They still have to be notified; look them up.")
    if excluded:
        note_bits.append(
            f"{excluded} parcel{'s' if excluded != 1 else ''} co-extensive with "
            f"the subject "
            f"{'were' if excluded != 1 else 'was'} excluded as the subject "
            f"property itself.")
    note_bits.append("Confirm the radius and who must be served against your local "
                     "ordinance before sending anything. Rules differ by town.")

    return {"result": _fc(out),
            "recipe": {"tool": "notice_list", "distance_ft": feet,
                       "parcels_notified": len(notice),
                       "owner_field": owner_col, "address_field": addr_col,
                       "subject_parcels_excluded": excluded,
                       **({"parcels_unusable": lost_on_input} if lost_on_input else {}),
                       **({"duplicates_removed": duplicates} if duplicates else {}),
                       "notice_list": notice,
                       "note": " ".join(note_bits)}}


register(Tool(
    id="notice_list", title="Who has to be notified about this?",
    noun="Notice list", category="hearings and notices", returns="layer",
    description="Given a parcel layer and the property an application is about, "
                "find every parcel within a set distance in feet and build the "
                "list of owners to notify, sorted by how close they are. Measures "
                "from the property line, not the centre, and leaves the subject "
                "property out of its own list. This is the abutter list, certified "
                "list, or notice of hearing list that a zoning or planning board "
                "asks for. Confirm the radius against the local ordinance.",
    params={"type": "object", "required": ["parcels", "subject"], "properties": {
        "parcels": {"type": "object",
                    "description": "the parcel layer to search, as a GeoJSON "
                                   "FeatureCollection"},
        "subject": {"type": "object",
                    "description": "the property the application is about"},
        "distance_ft": {"type": "number", "default": 200,
                        "description": "notice radius in feet, commonly 200, "
                                       "300 or 500"},
        "owner_field": {"type": "string",
                        "description": "column holding the owner name, if branch "
                                       "cannot find it"},
        "address_field": {"type": "string",
                          "description": "column holding the mailing address"}}},
    run=_run_notice_list))


def _run_find_data(params: dict) -> dict:
    from . import esri

    query = str(params.get("query", "")).strip()
    bbox = params.get("bbox")
    try:
        rows = esri.search(query, tuple(bbox) if bbox else None,
                           limit=int(params.get("limit", 8)))
    except esri.EsriError as exc:
        raise ValueError(str(exc)) from None

    usable = [r for r in rows if r["open"]]
    here = [r for r in usable if r.get("covers") is not False]
    note = (f"{len(usable)} of {len(rows)} are readable without a login"
            + (f", and {len(here)} reach where you are looking." if bbox
               else "."))
    if not usable:
        note += (" Published does not always mean open: the rest need an account "
                 "with the agency. Ask them for a public endpoint, or for the data "
                 "as a file.")
    return {"result": {"found": len(rows), "usable": len(usable),
                       "results": rows, "note": note},
            "recipe": {"tool": "find_data", "query": query,
                       "bbox": list(bbox) if bbox else None,
                       "found": len(rows), "usable": len(usable),
                       "source": "ArcGIS Hub catalogue, each result checked directly"}}


register(Tool(
    id="find_data", title="Find data your town has published",
    noun="Data search", category="map data", returns="value",
    description="Search what agencies have actually published: parcels, zoning, "
                "flood, trees, sidewalks, capital projects. Searches the ArcGIS "
                "Hub catalogue, then checks every result directly, so the list "
                "says which layers are genuinely readable without a login and "
                "which ones reach the area you are looking at. Give it a subject "
                "and a place, for example 'parcels Mercer County NJ'. Feed a URL "
                "it returns into the arcgis tool to bring the layer in.",
    params={"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string",
                  "description": "what and where, for example 'zoning Trenton NJ'"},
        "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4,
                 "maxItems": 4,
                 "description": "[west, south, east, north], to check coverage"},
        "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 8}}},
    run=_run_find_data))


# Counting things inside areas: the operation every council map is built on.
# "How many X per neighbourhood" is one question, and the two ways to get it
# wrong are both invisible in the output. An area with nothing in it must come
# back as zero rather than vanishing, or the map quietly claims every
# neighbourhood has trees. And a per-acre rate has to be computed on the ground,
# not from degrees, or the numbers drift with latitude.
_MONEYISH = re.compile(r"^[\s$£€]*(-?[\d,]*\.?\d+)\s*$")


def _to_number(value):
    """A number from a spreadsheet cell, or None if it is not one.

    Assessor exports write 1,200 and $300 and mean numbers. They also write
    "450x" and "see deed", which mean something else. ``pd.to_numeric`` turns
    ALL of those into NaN, so a column of five values totalled to 800 instead of
    5300, silently. Accept the conventional money and thousands formats; refuse
    everything else rather than guessing at it.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if value != value else float(value)
    match = _MONEYISH.match(str(value))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _run_summarize_within(params: dict) -> dict:
    import numpy as np
    import pandas as pd

    areas = _read_fc(params["areas"], "areas layer")
    items = _read_fc(params["items"], "layer being counted")
    field = params.get("field") or None
    if field is not None:
        if field not in items.columns:
            raise ValueError(
                f"There is no column called {field!r} in the layer being counted. "
                f"Its columns are: "
                f"{', '.join(map(str, [c for c in items.columns if c != 'geometry']))[:300]}.")
        numeric = items[field].map(_to_number)
        unreadable = int(numeric.isna().sum())
        if unreadable == len(numeric):
            raise ValueError(f"Column {field!r} holds no numbers, so it cannot be "
                             f"totalled or averaged. Leave the field out to count "
                             f"features instead.")
        items = items.assign(**{"_v": numeric})

    # An items layer carrying a column called _area_id would collide with the
    # key used to group by, and geopandas raises KeyError rather than choosing.
    items = items.drop(columns=[c for c in ("_area_id", "_v_original")
                                if c in items.columns and c != "_v"],
                       errors="ignore")
    right = items.to_crs(areas.crs)
    # Represent each item by one point so a polygon straddling a boundary lands
    # in exactly one area. Counting by overlap would total more than the whole.
    reps = right.copy()
    reps["geometry"] = right.geometry.representative_point()

    out = areas.copy().reset_index(drop=True)
    # Never write over a column the layer arrived with. A parcel layer whose
    # "count" means housing units must not come back holding our count instead.
    added = ["count", "acres", "per_acre"] + (["total", "average"] if field else [])
    renamed = {}
    for name in added:
        if name in out.columns:
            keep = f"{name}_original"
            i = 2
            while keep in out.columns:
                keep, i = f"{name}_original_{i}", i + 1
            out = out.rename(columns={name: keep})
            renamed[name] = keep
    out["_area_id"] = out.index
    joined = gpd.sjoin(reps, out[["_area_id", "geometry"]], predicate="within",
                       how="inner")
    grouped = joined.groupby("_area_id")

    counts = grouped.size()
    out["count"] = out["_area_id"].map(counts).fillna(0).astype(int)
    if field is not None:
        out["total"] = out["_area_id"].map(grouped["_v"].sum())
        # An area with nothing in it has no average. Zero would be a claim.
        out["average"] = out["_area_id"].map(grouped["_v"].mean())
        out.loc[out["count"] == 0, ["total", "average"]] = np.nan

    # Rates are measured on the ground, in the local UTM zone.
    metric = out.to_crs(out.estimate_utm_crs())
    acres = metric.geometry.area / 4046.8564224
    out["acres"] = acres.round(2)
    out["per_acre"] = np.where(acres > 0, out["count"] / acres, np.nan)
    # Fixed decimals destroy small rates: 6 hospitals in a 566,000 acre county is
    # 1.06e-05 per acre, which rounds to 0.0 and reads as "none". Keep significant
    # figures instead of decimal places.
    out["per_acre"] = [float(f"{v:.6g}") if v == v else v for v in out["per_acre"]]
    out = out.drop(columns=["_area_id"])

    placed = joined.index.nunique()          # items that landed in at least one area
    unplaced = len(reps) - placed
    multi = int(len(joined) - placed)        # items that landed in more than one
    empty = int((out["count"] == 0).sum())
    bits = [f"{placed:,} of {len(reps):,} counted."]
    if multi:
        bits.append(f"{multi:,} landed in more than one area because the areas "
                    f"overlap, so the per-area counts add up to more than the "
                    f"total. That is correct per area, but do not sum the column.")
    if field is not None and unreadable:
        one = unreadable == 1
        bits.append(f"{unreadable:,} value{'' if one else 's'} in {field!r} "
                    f"could not be read as a number and "
                    f"{'was' if one else 'were'} left out of the total and the "
                    f"average. The count still "
                    f"{'includes it.' if one else 'includes them.'}")
    if renamed:
        bits.append("This layer already had "
                    + ", ".join(sorted(renamed)) + ", so "
                    + ("that column was" if len(renamed) == 1 else "those columns were")
                    + " kept as "
                    + ", ".join(renamed[k] for k in sorted(renamed)) + ".")
    if unplaced:
        bits.append(f"{unplaced:,} fell outside every area and were left out.")
    if empty:
        bits.append(f"{empty} area{'s' if empty != 1 else ''} contain nothing. "
                    f"They are kept, at zero, because an empty area is a finding.")

    return {"result": _fc(out.to_crs("EPSG:4326") if out.crs else out),
            "recipe": {"tool": "summarize_within", "field": field,
                       **({"values_unreadable": unreadable}
                          if field is not None and unreadable else {}),
                       **({"renamed_columns": renamed} if renamed else {}),
                       "areas": int(len(out)), "items_counted": int(placed),
                       "items_outside": int(unplaced),
                       **({"items_in_several_areas": multi} if multi else {}),
                       "empty_areas": empty,
                       "note": " ".join(bits)}}


register(Tool(
    id="summarize_within", title="Count what is inside each area",
    noun="Summary by area", category="shaping layers", returns="layer",
    description="For every area in one layer, count how many things from another "
                "layer fall inside it, and optionally total or average one of "
                "their columns. Adds count, acres and a per acre rate to each "
                "area, so the result can be mapped as a choropleth straight away. "
                "Areas containing nothing are kept at zero. Use it for trees per "
                "tract, complaints per ward, units per parcel. Also called zonal "
                "statistics, summarize within, or a count of points in polygon.",
    params={"type": "object", "required": ["areas", "items"], "properties": {
        "areas": {"type": "object",
                  "description": "the zones to summarise into, such as tracts"},
        "items": {"type": "object",
                  "description": "the layer being counted"},
        "field": {"type": "string",
                  "description": "optional numeric column to total and average"}}},
    run=_run_summarize_within))
