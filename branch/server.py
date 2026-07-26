"""branch backend: address geocoding + on-demand shade-graph preparation.

Two endpoints power the Google-Maps-style front end:

* ``GET  /api/geocode?q=...`` proxies OpenStreetMap's Nominatim geocoder (free,
  no API key, biased to the NYC metro) so the user can type real addresses.
* ``POST /api/prepare`` takes the current waypoints + time of day, downloads the
  walk network and street trees covering them (cached), models the shade, and
  returns the annotated graph. The browser then runs the routing itself, so
  dragging pins and the shade-aversion slider stay instant.

Run it with ``branch serve`` (or ``python -m branch serve``).
"""
from __future__ import annotations

import os

import requests
from flask import Flask, jsonify, request, send_from_directory

from . import agent, config, data, geoutil, registry, shade, solar

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "branch/1.0 (+https://github.com/BenMalaga/branch)"
NYC_VIEWBOX = "-74.30,40.95,-73.65,40.48"   # metro bounds to bias address search

MAX_SPAN_DEG = 0.06   # ~6.5 km cap on the on-demand area (keeps a request fast)
PAD_DEG = 0.004       # ~400 m buffer around the waypoints

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


# A jurisdiction is a place you can work in, so these are the result kinds that
# get offered as a study-area boundary rather than just a pin.
AREA_KINDS = {"county", "city", "town", "village", "borough", "suburb", "state",
              "municipality", "district", "city_district", "neighbourhood",
              "quarter", "region", "province", "hamlet", "administrative"}


@app.get("/api/geocode")
def geocode():
    """Place search via Nominatim (no key), anywhere on earth.

    The current map view only biases the ranking, it does not restrict it: this
    used to be pinned to a New York viewbox, which quietly made every search
    outside New York useless.
    """
    q = request.args.get("q", "").strip()
    if len(q) < 3:
        return jsonify([])
    params = {"q": q, "format": "json", "limit": 8, "addressdetails": 0}
    view = request.args.get("viewbox", "").strip()
    if view:
        params["viewbox"] = view          # bias toward what the user is looking at
    try:
        r = requests.get(NOMINATIM, params=params,
                         headers={"User-Agent": NOMINATIM_UA}, timeout=15)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    out = []
    for x in rows:
        kind = x.get("addresstype") or x.get("type") or ""
        out.append({"label": x["display_name"],
                    "lat": float(x["lat"]), "lon": float(x["lon"]),
                    "kind": kind,
                    "osm_type": x.get("osm_type"), "osm_id": x.get("osm_id"),
                    "is_area": bool(kind in AREA_KINDS
                                    and x.get("osm_type") in ("relation", "way"))})
    return jsonify(out)


@app.get("/api/boundary")
def boundary():
    """The real border of one place, as a polygon you can plan inside.

    Two steps on purpose: search stays light while typing, and the boundary
    (which can be thousands of points) is fetched only for the one you pick.
    """
    osm_type = request.args.get("osm_type", "")
    osm_id = request.args.get("osm_id", "")
    if osm_type not in ("relation", "way") or not str(osm_id).isdigit():
        return jsonify({"error": "need an osm_type of relation or way, plus a numeric osm_id"}), 400
    prefix = {"relation": "R", "way": "W"}[osm_type]
    try:
        r = requests.get(NOMINATIM.replace("/search", "/lookup"), params={
            "osm_ids": f"{prefix}{osm_id}", "format": "json", "polygon_geojson": 1,
        }, headers={"User-Agent": NOMINATIM_UA}, timeout=25)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    if not rows:
        return jsonify({"error": "no such place"}), 404
    row = rows[0]
    geom = row.get("geojson")
    if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
        return jsonify({"error": "that place has no mapped border in OpenStreetMap, "
                                 "so there is nothing to plan inside"}), 404
    name = (row.get("display_name") or "").split(",")[0]
    kind = row.get("addresstype") or row.get("type") or "area"
    return jsonify({"result": {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"name": name, "kind": kind,
                       "full_name": row.get("display_name"),
                       "source": "OpenStreetMap via Nominatim"},
        "geometry": geom}]},
        "recipe": {"tool": "boundary", "osm_type": osm_type, "osm_id": int(osm_id),
                   "name": name, "kind": kind}}) 


@app.post("/api/prepare")
def prepare():
    """Download + shade the walk graph covering the waypoints, for the browser."""
    body = request.get_json(force=True, silent=True) or {}
    wps = body.get("waypoints") or []
    when_local = body.get("datetime") or config.DEFAULT_DATETIME_LOCAL
    if len(wps) < 1:
        return jsonify({"error": "need at least one waypoint"}), 400

    lats = [float(w[0]) for w in wps]
    lons = [float(w[1]) for w in wps]
    if (max(lats) - min(lats) > MAX_SPAN_DEG) or (max(lons) - min(lons) > MAX_SPAN_DEG):
        return jsonify({"error": "Those points are too far apart for the live "
                        "demo. Try points within about 6 km of each other."}), 400

    bbox = (min(lons) - PAD_DEG, min(lats) - PAD_DEG,
            max(lons) + PAD_DEG, max(lats) + PAD_DEG)
    area = data.area_from_bbox(bbox)
    try:
        G = data.get_graph(area)
        trees = data.get_trees(area)
    except Exception as e:
        return jsonify({"error": f"Could not load map data for that area: {e}"}), 502

    lat, lon = area.center
    altitude, azimuth = solar.sun_position(lat, lon, solar.to_utc(when_local, config.TIMEZONE))
    shade.annotate_edges(G, shade.compute_shadows(trees, altitude, azimuth))

    node_ids = list(G.nodes())
    idx = {n: i for i, n in enumerate(node_ids)}
    nodes = [list(geoutil.xy_to_latlon(G.nodes[n]["x"], G.nodes[n]["y"])) for n in node_ids]

    edges = []
    for u, v, d in G.edges(data=True):
        wgs = geoutil.geom_to_wgs(d["geometry"])
        edges.append({
            "u": idx[u], "v": idx[v], "len": round(d["length"], 1),
            "c": [[round(la, 6), round(lo, 6)] for lo, la in wgs.coords],
            "s": round(d.get("sun_frac", 1.0), 3),
        })

    pts = list(trees.geometry.values)
    step = max(1, len(pts) // 3000)
    tree_pts = [list(geoutil.xy_to_latlon(p.x, p.y)) for p in pts[::step]]

    return jsonify({
        "bbox": list(bbox),
        "date": when_local.split(" ")[0],
        "sun_alt": round(altitude, 1),
        "nodes": nodes,
        "edges": edges,
        "trees": tree_pts,
    })


@app.get("/api/tools")
def list_tools():
    """The tool registry: what a human can click and the agent can call."""
    return jsonify([{"id": t.id, "title": t.title, "description": t.description,
                     "category": t.category, "returns": t.returns, "params": t.params,
                     "noun": t.noun or t.title}
                    for t in registry.all_tools()])


@app.get("/api/esri/describe")
def esri_describe():
    """What an ArcGIS layer holds, before anyone commits to downloading it.

    Pasting a URL and getting back "12,000 shapes with no fields you wanted" is
    the slow way to find out. This answers the question first: what it is called,
    what shape it is, which columns exist, and the ground it actually covers.
    """
    from . import esri

    try:
        url = esri.check_url(request.args.get("url", ""))
        return jsonify({"result": esri.describe(url)})
    except esri.EsriError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 400


@app.post("/api/tools/<tool_id>/run")
def run_tool(tool_id):
    """Execute one tool deterministically. Returns {result, recipe}."""
    tool = registry.get(tool_id)
    if tool is None:
        return jsonify({"error": f"unknown tool {tool_id}"}), 404
    params = request.get_json(force=True, silent=True) or {}
    try:
        registry.validate_params(tool, params)
    except registry.ParamError as e:
        return jsonify({"error": str(e)}), 400
    try:
        return jsonify(tool.run(params))
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 400


@app.post("/api/agent")
def agent_endpoint():
    """Plain-English question -> tool calls -> layers + answer + provenance.

    Body: {question, llm: {provider, model, key?, host?}, context?}. The LLM key
    is used for this request only and never stored.
    """
    body = request.get_json(force=True, silent=True) or {}
    if not body.get("question"):
        return jsonify({"error": "question is required"}), 400
    try:
        return jsonify(agent.run_agent(body["question"], body.get("llm") or {},
                                       body.get("context"),
                                       history=body.get("history") or []))
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    print(f"branch serving on http://{host}:{port}")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
