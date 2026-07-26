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
        moved = None
        for authority in ("EPSG", "ESRI"):     # 102711 is an Esri number, not an EPSG one
            try:
                from pyproj import Transformer
                tf = Transformer.from_crs(f"{authority}:{int(wkid)}", "EPSG:4326",
                                          always_xy=True)
                # Sample the edges, not just two corners: a conic or State Plane
                # extent bows, so the corners alone understate its real reach.
                xs, ys = [], []
                for fx in (0.0, 0.5, 1.0):
                    for fy in (0.0, 0.5, 1.0):
                        px, py = tf.transform(xmin + (xmax - xmin) * fx,
                                              ymin + (ymax - ymin) * fy)
                        if _finite(px) and _finite(py):
                            xs.append(px)
                            ys.append(py)
                if xs:
                    moved = (min(xs), min(ys), max(xs), max(ys))
                    break
            except Exception:
                continue
        if moved is None:
            return None                        # unknown projection: do not guess
        xmin, ymin, xmax, ymax = moved
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
        counted = _get(f"{url}/query",
                       {**common, "returnCountOnly": "true", "f": "json"})
        # An old server that does not understand returnCountOnly replies with a
        # normal feature payload. Reading a missing "count" as 0 would report
        # "nothing here" for a layer that is full.
        count = int(counted["count"]) if "count" in counted else -1
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

    page = max(1, min(limit, int(info["max_record_count"] or 1000)))
    params = {**common, "outFields": "*", "returnGeometry": "true",
              "geometryPrecision": 6}
    feats, offset = [], 0
    while len(feats) < limit:
        want = min(page, limit - len(feats))
        query = {**params, "resultRecordCount": want}
        if offset:
            query["resultOffset"] = offset
        if info["supports_geojson"]:
            batch = (_get(f"{url}/query", {**query, "f": "geojson"},
                          timeout=90).get("features") or [])
        else:
            batch = _esri_to_geojson(_get(f"{url}/query", {**query, "f": "json"},
                                          timeout=90))
        feats.extend(batch)
        if len(batch) < want:
            break                      # the server had nothing more to give
        offset += len(batch)
        if offset and count >= 0 and offset >= count:
            break
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


# ---------------------------------------------------------------------------
# Finding a service in the first place.
#
# The connector is useless if you do not know your town's URL, and nobody does.
# ArcGIS Hub indexes what agencies have published and answers without a key, so
# a search is possible. What a search cannot tell you is whether a result is
# actually readable: plenty of published layers need a token, and plenty cover
# a different county. Both of those only show up when you ask the service. So
# every candidate is probed before it is offered, and results are labelled with
# what was actually found rather than with what the catalogue claimed.
# ---------------------------------------------------------------------------

HUB_SEARCH = "https://hub.arcgis.com/api/v3/datasets"
PROBE_LIMIT = 8


def search(query: str, bbox: tuple | None = None, limit: int = 8) -> list[dict]:
    """Published ArcGIS layers matching ``query``, each probed for real access."""
    query = (query or "").strip()
    if not query:
        raise EsriError("Say what you are looking for, for example "
                        "'parcels Hunterdon County' or 'zoning Trenton'.")
    limit = max(1, min(int(limit), PROBE_LIMIT))

    try:
        r = requests.get(HUB_SEARCH, timeout=25,
                         params={"q": query, "filter[type]": "Feature Layer",
                                 "page[size]": max(limit * 3, 12)},
                         headers={"User-Agent": "branch (planwithbranch.com)"})
        r.raise_for_status()
        payload = r.json()
    except requests.exceptions.RequestException as exc:
        raise EsriError(f"Could not reach the ArcGIS Hub catalogue: {exc}") from None
    except ValueError:
        raise EsriError("The ArcGIS Hub catalogue returned something unreadable.") from None

    seen, candidates = set(), []
    for item in payload.get("data") or []:
        attrs = item.get("attributes") or {}
        url = (attrs.get("url") or "").strip()
        if not url or url in seen:
            continue
        try:
            url = check_url(url)
        except EsriError:
            continue                      # a web map or a folder, not a layer
        seen.add(url)
        candidates.append({
            "name": attrs.get("name") or "Layer",
            "org": attrs.get("orgName") or attrs.get("owner") or "",
            "summary": (attrs.get("snippet") or "").strip()[:200],
            "url": url,
        })
        if len(candidates) >= limit:
            break

    if not candidates:
        raise EsriError(
            f"Nothing published matches '{query}'. Try the county or town name "
            f"with the word parcels, zoning or boundaries, for example "
            f"'parcels Mercer County NJ'.")

    # Probe in parallel: the catalogue lies about access often enough that
    # offering an unchecked list would waste more time than this costs.
    from concurrent.futures import ThreadPoolExecutor

    def probe(c):
        out = dict(c, open=False, reason="", covers=None,
                   geometry_type=None, fields=[])
        try:
            info = describe(c["url"])
            out.update(open=True, geometry_type=info["geometry_type"],
                       extent=info["extent"],
                       fields=[f["name"] for f in info["fields"]][:30])
            if bbox and info["extent"]:
                out["covers"] = _overlaps(info["extent"], tuple(bbox))
        except EsriError as exc:
            msg = str(exc)
            out["reason"] = ("it needs a login" if "Token Required" in msg
                             else msg[:140])
        except Exception:
            out["reason"] = "it did not answer"
        return out

    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        results = list(pool.map(probe, candidates))

    # Readable first, then the ones that reach where you are looking.
    results.sort(key=lambda r: (not r["open"], r.get("covers") is not True))
    return results
