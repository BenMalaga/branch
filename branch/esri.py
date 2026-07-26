"""Reading the ArcGIS services a town already publishes.

Most US municipalities, counties and states already put their parcels, zoning,
flood layers and capital projects on an ArcGIS REST server, free and without a
key. That data is the thing a planner actually needs and cannot get out of
OpenStreetMap. This module turns any such layer into a branch layer.

Three things here are not optional:

* **The URL is user supplied**, so it is fetched by the server on a user's
  behalf. That is a request-forgery hole unless the host is checked first, and
  unless redirects are checked too: a public name that 302s to 127.0.0.1 would
  otherwise walk straight past the check. Known residual risk: DNS is resolved
  once here and again by the HTTP client, so a rebinding attack is not fully
  closed. Closing it needs pinning the resolved address at connect time.
* **The layer's real extent is checked** before anything is fetched. Pointing a
  Trenton parcel service at Los Angeles must fail with a name, not return an
  empty set that reads like "there are no parcels here".
* **Older servers do not speak GeoJSON.** ArcGIS Server before 10.4 answers
  ``f=geojson`` with an error, so Esri JSON is converted here rather than
  letting the caller see a failure it cannot act on.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

import requests

TIMEOUT = 30
MAX_FEATURES = 6000

# A layer URL ends in the layer index: .../FeatureServer/0 or .../MapServer/3
LAYER_URL = re.compile(r"^https://[^\s]+/(?:Feature|Map)Server/\d+/?$", re.I)


class EsriError(RuntimeError):
    """Anything wrong with the service, the URL, or the ground it covers."""


def check_url(url: str) -> str:
    """Return a normalised layer URL, or raise if it is not one we may fetch."""
    url = (url or "").strip().rstrip("/")
    if not url:
        raise EsriError("Paste the URL of an ArcGIS layer to bring in.")
    if url.lower().startswith("http://"):
        raise EsriError("That URL is not encrypted. Use the https:// address of "
                        "the same service.")
    if not LAYER_URL.match(url + "/"):
        raise EsriError(
            "That does not look like an ArcGIS layer URL. It should end with the "
            "layer number, for example "
            ".../FeatureServer/0 or .../MapServer/3. Open the service in a browser "
            "and copy the address of the individual layer, not the folder above it."
        )
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        raise EsriError(f"No server answers at {host}. Check the address.") from None
    # A public service must not be a way to read a private network.
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise EsriError(f"{host} resolves to a private address, which branch "
                            f"will not fetch.")
    return url


def _get(url: str, params: dict, timeout: int = TIMEOUT) -> dict:
    try:
        # Redirects are not followed. A public hostname that 302s to 127.0.0.1 or
        # to a cloud metadata address would otherwise walk straight past the host
        # check above, which is the whole point of having one.
        r = requests.get(url, params=params, timeout=timeout, allow_redirects=False,
                         headers={"User-Agent": "branch (planwithbranch.com)"})
        if r.is_redirect or r.is_permanent_redirect:
            target = r.headers.get("Location", "")
            check_url(target.split("?")[0])      # must pass the same test
            r = requests.get(target, params=params, timeout=timeout,
                             allow_redirects=False,
                             headers={"User-Agent": "branch (planwithbranch.com)"})
            if r.is_redirect or r.is_permanent_redirect:
                raise EsriError("That service redirects more than once. "
                                "Use the address it finally lands on.")
        r.raise_for_status()
    except requests.exceptions.Timeout:
        raise EsriError("The service took too long to answer. It may be busy, or "
                        "the area may be too large. Zoom in and try again.") from None
    except requests.exceptions.RequestException as exc:
        raise EsriError(f"Could not reach that service: {exc}") from None
    try:
        out = r.json()
    except ValueError:
        raise EsriError("That service did not return data branch can read. Check "
                        "that the URL points at a layer.") from None
    if isinstance(out, dict) and "error" in out:
        err = out["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise EsriError(f"The service refused that request: {msg}")
    return out


def _wgs84_extent(extent: dict) -> tuple[float, float, float, float] | None:
    """An Esri extent as (west, south, east, north) in degrees, or None.

    The extent is usually published in the service's own projection. Comparing
    Web Mercator metres against degrees without converting is the mistake that
    makes every coverage check pass or every one fail.
    """
    if not isinstance(extent, dict):
        return None
    try:
        xmin, ymin = float(extent["xmin"]), float(extent["ymin"])
        xmax, ymax = float(extent["xmax"]), float(extent["ymax"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(map(_finite, (xmin, ymin, xmax, ymax))):
        return None
    sr = extent.get("spatialReference") or {}
    wkid = sr.get("latestWkid") or sr.get("wkid")
    if wkid in (4326, 4269, 4267):            # already degrees
        pass
    elif wkid in (102100, 3857, 900913, 102113):
        xmin, ymin = _merc_to_deg(xmin, ymin)
        xmax, ymax = _merc_to_deg(xmax, ymax)
    else:
        try:
            from pyproj import Transformer
            tf = Transformer.from_crs(f"EPSG:{int(wkid)}", "EPSG:4326", always_xy=True)
            xmin, ymin = tf.transform(xmin, ymin)
            xmax, ymax = tf.transform(xmax, ymax)
        except Exception:
            return None                        # unknown projection: do not guess
    if not all(map(_finite, (xmin, ymin, xmax, ymax))):
        return None
    if abs(xmin) > 180 or abs(xmax) > 180 or abs(ymin) > 90 or abs(ymax) > 90:
        return None
    return (min(xmin, xmax), min(ymin, ymax), max(xmin, xmax), max(ymin, ymax))


def _finite(v: float) -> bool:
    return v == v and abs(v) != float("inf")


def _merc_to_deg(x: float, y: float) -> tuple[float, float]:
    import math
    lon = x / 20037508.34 * 180.0
    lat = y / 20037508.34 * 180.0
    lat = 180.0 / math.pi * (2 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2)
    return lon, lat


def describe(url: str) -> dict:
    """What a layer is, and the ground it covers."""
    meta = _get(url, {"f": "json"})
    if meta.get("type") not in (None, "Feature Layer", "Table"):
        raise EsriError(f"That is a {meta.get('type')}, not a feature layer.")
    if not meta.get("geometryType"):
        raise EsriError(
            f"'{meta.get('name') or 'That layer'}' is a table with no shapes, so "
            f"there is nothing to draw. Pick a layer with geometry.")
    fields = [f for f in (meta.get("fields") or [])
              if f.get("type") != "esriFieldTypeGeometry"]
    return {
        "name": meta.get("name") or "Layer",
        "geometry_type": meta.get("geometryType"),
        "extent": _wgs84_extent(meta.get("extent") or {}),
        "max_record_count": int(meta.get("maxRecordCount") or 1000),
        "supports_geojson": "geoJSON" in (meta.get("supportedQueryFormats") or ""),
        "fields": [{"name": f.get("name"), "alias": f.get("alias"),
                    "type": str(f.get("type", "")).replace("esriFieldType", "")}
                   for f in fields],
        "description": (meta.get("description") or "").strip(),
    }


def _overlaps(a: tuple, b: tuple) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def fetch(url: str, bbox: tuple[float, float, float, float],
          where: str = "1=1", limit: int = 2000) -> dict:
    """Features from ``url`` inside ``bbox``, as GeoJSON in degrees."""
    url = check_url(url)
    info = describe(url)
    limit = max(1, min(int(limit), MAX_FEATURES))

    extent = info["extent"]
    if extent and not _overlaps(extent, tuple(bbox)):
        w, s, e, n = extent
        raise EsriError(
            f"'{info['name']}' only covers {w:.3f}, {s:.3f} to {e:.3f}, {n:.3f}, "
            f"and you are looking somewhere else. This is a local service: it "
            f"publishes one jurisdiction, not the world. Move the map to that "
            f"area, or find the equivalent service for the place you are studying."
        )

    w, s, e, n = bbox
    envelope = json.dumps({"xmin": w, "ymin": s, "xmax": e, "ymax": n,
                           "spatialReference": {"wkid": 4326}})
    common = {"where": (where or "1=1").strip() or "1=1",
              "geometry": envelope, "geometryType": "esriGeometryEnvelope",
              "inSR": 4326, "outSR": 4326,
              "spatialRel": "esriSpatialRelIntersects"}

    # Count first. A silent truncation reads as a complete answer.
    try:
        count = int(_get(f"{url}/query",
                         {**common, "returnCountOnly": "true", "f": "json"}
                         ).get("count", 0))
    except EsriError:
        count = -1                              # some old servers cannot count
    if count == 0:
        raise EsriError(
            f"'{info['name']}' has nothing in this view. The service reaches here, "
            f"so this is a real answer, not a gap: there are no matching features. "
            + (f"Your filter was: {where}" if where and where != "1=1" else
               "Try zooming out a little."))
    if count > limit:
        raise EsriError(
            f"That view holds {count:,} features from '{info['name']}', more than "
            f"the {limit:,} branch will bring in at once. Zoom in, raise the limit, "
            f"or narrow it with a filter.")

    params = {**common, "outFields": "*", "returnGeometry": "true",
              "resultRecordCount": limit, "geometryPrecision": 6}
    if info["supports_geojson"]:
        fc = _get(f"{url}/query", {**params, "f": "geojson"}, timeout=90)
        feats = fc.get("features") or []
    else:
        feats = _esri_to_geojson(_get(f"{url}/query", {**params, "f": "json"},
                                      timeout=90))
    if not feats:
        raise EsriError(f"'{info['name']}' returned no shapes for this view.")
    return {"features": feats[:limit], "info": info,
            "count": count if count >= 0 else len(feats)}


def _esri_to_geojson(payload: dict) -> list[dict]:
    """Convert Esri JSON to GeoJSON features, for servers older than 10.4."""
    gtype = payload.get("geometryType", "")
    out = []
    for f in payload.get("features") or []:
        g = f.get("geometry") or {}
        geom = None
        if gtype == "esriGeometryPoint" and "x" in g:
            if g.get("x") is not None and g.get("y") is not None:
                geom = {"type": "Point", "coordinates": [g["x"], g["y"]]}
        elif gtype == "esriGeometryPolyline":
            paths = [p for p in (g.get("paths") or []) if len(p) > 1]
            if len(paths) == 1:
                geom = {"type": "LineString", "coordinates": paths[0]}
            elif paths:
                geom = {"type": "MultiLineString", "coordinates": paths}
        elif gtype == "esriGeometryPolygon":
            rings = [r for r in (g.get("rings") or []) if len(r) > 3]
            if rings:
                # Esri packs outer rings and holes into one list, clockwise for
                # outer. Splitting on winding keeps courtyards as holes rather
                # than as separate buildings.
                polys, current = [], None
                for ring in rings:
                    if _clockwise(ring):
                        if current:
                            polys.append(current)
                        current = [ring]
                    elif current:
                        current.append(ring)
                    else:
                        current = [ring]
                if current:
                    polys.append(current)
                geom = ({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                        else {"type": "MultiPolygon", "coordinates": polys})
        if geom is None:
            continue
        out.append({"type": "Feature", "geometry": geom,
                    "properties": f.get("attributes") or {}})
    return out


def _clockwise(ring: list) -> bool:
    """Shoelace sign. Esri draws outer rings clockwise."""
    total = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        total += (x2 - x1) * (y2 + y1)
    return total > 0
