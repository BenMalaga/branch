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


@app.get("/api/geocode")
def geocode():
    """Address search via Nominatim (no key). Returns up to 6 NYC-area matches."""
    q = request.args.get("q", "").strip()
    if len(q) < 3:
        return jsonify([])
    try:
        r = requests.get(NOMINATIM, params={
            "q": q, "format": "json", "limit": 6,
            "viewbox": NYC_VIEWBOX, "bounded": 1,
        }, headers={"User-Agent": NOMINATIM_UA}, timeout=15)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify([{"label": x["display_name"],
                     "lat": float(x["lat"]), "lon": float(x["lon"])}
                    for x in rows])


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
                     "category": t.category, "returns": t.returns, "params": t.params}
                    for t in registry.all_tools()])


@app.post("/api/tools/<tool_id>/run")
def run_tool(tool_id):
    """Execute one tool deterministically. Returns {result, recipe}."""
    tool = registry.get(tool_id)
    if tool is None:
        return jsonify({"error": f"unknown tool {tool_id}"}), 404
    try:
        return jsonify(tool.run(request.get_json(force=True, silent=True) or {}))
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
                                       body.get("context")))
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    print(f"branch serving on http://{host}:{port}")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
