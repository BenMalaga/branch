"""The ArcGIS connector: URL safety, extent maths, and Esri JSON conversion.

These are all offline. The failure that matters is not a crash, it is a layer
that comes back looking right and is in the wrong place or the wrong shape.
"""
import pytest

from branch import esri


# ---------- what we are allowed to fetch ----------

def test_layer_url_must_end_in_a_layer_number():
    with pytest.raises(esri.EsriError) as e:
        esri.check_url("https://example.gov/arcgis/rest/services/Parcels/FeatureServer")
    assert "layer number" in str(e.value)


def test_plain_http_is_refused():
    with pytest.raises(esri.EsriError) as e:
        esri.check_url("http://example.gov/arcgis/rest/services/P/FeatureServer/0")
    assert "not encrypted" in str(e.value)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "169.254.169.254"])
def test_private_and_metadata_hosts_are_refused(host):
    """A public service must not become a way to read a private network."""
    with pytest.raises(esri.EsriError):
        esri.check_url(f"https://{host}/arcgis/rest/services/X/FeatureServer/0")


def test_empty_url_says_what_to_do():
    with pytest.raises(esri.EsriError) as e:
        esri.check_url("   ")
    assert "Paste the URL" in str(e.value)


# ---------- extents, which are usually not in degrees ----------

def test_extent_already_in_degrees_passes_through():
    got = esri._wgs84_extent({"xmin": -75, "ymin": 39, "xmax": -74, "ymax": 40,
                              "spatialReference": {"wkid": 4326}})
    assert got == (-75, 39, -74, 40)


def test_web_mercator_extent_is_converted_not_compared_raw():
    """Trenton in metres must not read as a point off the coast of Africa."""
    got = esri._wgs84_extent({"xmin": -8367000, "ymin": 4858000,
                              "xmax": -8330000, "ymax": 4885000,
                              "spatialReference": {"latestWkid": 3857}})
    assert got is not None
    w, s, e, n = got
    assert -75.2 < w < -74.7 and 39.9 < s < 40.3
    assert -74.9 < e < -74.6 and 40.0 < n < 40.4


def test_state_plane_extent_is_converted():
    """NJ State Plane feet, the projection most NJ towns actually publish in."""
    got = esri._wgs84_extent({"xmin": 390000, "ymin": 530000,
                              "xmax": 420000, "ymax": 560000,
                              "spatialReference": {"wkid": 3424}})
    assert got is not None
    w, s, e, n = got
    assert -75.5 < w < -74.0 and 40.0 < s < 41.5


def test_unknown_projection_returns_none_rather_than_guessing():
    assert esri._wgs84_extent({"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4,
                               "spatialReference": {"wkid": 999999}}) is None


def test_nonsense_extent_is_rejected():
    assert esri._wgs84_extent({}) is None
    assert esri._wgs84_extent({"xmin": "a", "ymin": 0, "xmax": 1, "ymax": 1}) is None


def test_overlap_is_intersection_not_containment():
    """A parcel service that covers part of the view is still useful."""
    town = (-74.8, 40.2, -74.6, 40.4)
    assert esri._overlaps(town, (-74.7, 40.3, -74.5, 40.5))    # partial
    assert esri._overlaps(town, (-74.75, 40.25, -74.65, 40.35))  # inside
    assert not esri._overlaps(town, (2.2, 48.8, 2.4, 48.9))     # Paris


# ---------- Esri JSON, for servers older than 10.4 ----------

def _ring(pts):
    return [list(p) for p in pts]


OUTER = _ring([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)])          # clockwise
HOLE = _ring([(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)])               # counter


def test_point_conversion():
    feats = esri._esri_to_geojson({"geometryType": "esriGeometryPoint",
                                   "features": [{"geometry": {"x": -74.0, "y": 40.7},
                                                 "attributes": {"id": 1}}]})
    assert feats[0]["geometry"] == {"type": "Point", "coordinates": [-74.0, 40.7]}
    assert feats[0]["properties"] == {"id": 1}


def test_null_geometry_is_dropped_not_placed_at_zero():
    """A parcel with no shape must vanish, not land in the Gulf of Guinea."""
    feats = esri._esri_to_geojson({"geometryType": "esriGeometryPoint",
                                   "features": [{"geometry": {"x": None, "y": None},
                                                 "attributes": {"id": 1}},
                                                {"geometry": {}, "attributes": {"id": 2}}]})
    assert feats == []


def test_single_path_is_a_linestring_not_a_multi():
    feats = esri._esri_to_geojson({"geometryType": "esriGeometryPolyline",
                                   "features": [{"geometry": {"paths": [[[0, 0], [1, 1]]]},
                                                 "attributes": {}}]})
    assert feats[0]["geometry"]["type"] == "LineString"


def test_several_paths_become_a_multilinestring():
    feats = esri._esri_to_geojson({"geometryType": "esriGeometryPolyline",
                                   "features": [{"geometry": {"paths": [[[0, 0], [1, 1]],
                                                                        [[5, 5], [6, 6]]]},
                                                 "attributes": {}}]})
    assert feats[0]["geometry"]["type"] == "MultiLineString"


def test_a_courtyard_stays_a_hole():
    """Esri packs rings flat. Treating a hole as its own polygon would turn one
    building with a courtyard into two buildings, and inflate its footprint."""
    feats = esri._esri_to_geojson({"geometryType": "esriGeometryPolygon",
                                   "features": [{"geometry": {"rings": [OUTER, HOLE]},
                                                 "attributes": {}}]})
    g = feats[0]["geometry"]
    assert g["type"] == "Polygon"
    assert len(g["coordinates"]) == 2          # outer plus one hole


def test_two_separate_shapes_become_a_multipolygon():
    other = _ring([(20, 20), (20, 30), (30, 30), (30, 20), (20, 20)])
    feats = esri._esri_to_geojson({"geometryType": "esriGeometryPolygon",
                                   "features": [{"geometry": {"rings": [OUTER, other]},
                                                 "attributes": {}}]})
    g = feats[0]["geometry"]
    assert g["type"] == "MultiPolygon"
    assert len(g["coordinates"]) == 2


def test_degenerate_rings_are_dropped():
    feats = esri._esri_to_geojson({"geometryType": "esriGeometryPolygon",
                                   "features": [{"geometry": {"rings": [[[0, 0], [1, 1]]]},
                                                 "attributes": {}}]})
    assert feats == []


def test_winding_detection():
    assert esri._clockwise(OUTER)
    assert not esri._clockwise(HOLE)


# ---------- finding a service, and being honest about what was found ----------

class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def _hub(monkeypatch, items):
    """Stand in for the Hub catalogue."""
    monkeypatch.setattr(esri.requests, "get",
                        lambda *a, **k: _Resp({"data": items}))


def _item(name, url, org="Some County"):
    return {"attributes": {"name": name, "url": url, "orgName": org,
                           "snippet": ""}}


OPEN_URL = "https://services.arcgis.com/abc/arcgis/rest/services/Parcels/FeatureServer/0"
SHUT_URL = "https://services.arcgis.com/abc/arcgis/rest/services/Private/FeatureServer/0"


def _describe_stub(url):
    if url == SHUT_URL:
        raise esri.EsriError("The service refused that request: Token Required")
    if "Elsewhere" in url:
        return {"name": "Elsewhere", "geometry_type": "esriGeometryPolygon",
                "extent": (-118.5, 33.9, -118.1, 34.2), "fields": [],
                "max_record_count": 1000, "supports_geojson": True, "description": ""}
    return {"name": "Parcels", "geometry_type": "esriGeometryPolygon",
            "extent": (-75.2, 40.1, -74.7, 40.5),      # central NJ, around Trenton
            "fields": [{"name": "OWNER"}, {"name": "ACRES"}],
            "max_record_count": 1000, "supports_geojson": True, "description": ""}


def test_a_layer_needing_a_login_is_reported_not_hidden(monkeypatch):
    """Published is not the same as open. Saying so saves a wasted download."""
    _hub(monkeypatch, [_item("Open", OPEN_URL), _item("Private", SHUT_URL)])
    monkeypatch.setattr(esri, "describe", _describe_stub)
    rows = esri.search("parcels")
    by = {r["url"]: r for r in rows}
    assert by[OPEN_URL]["open"] is True
    assert by[SHUT_URL]["open"] is False
    assert "needs a login" in by[SHUT_URL]["reason"]


def test_readable_results_are_offered_first(monkeypatch):
    _hub(monkeypatch, [_item("Private", SHUT_URL), _item("Open", OPEN_URL)])
    monkeypatch.setattr(esri, "describe", _describe_stub)
    assert [r["open"] for r in esri.search("parcels")] == [True, False]


def test_a_service_for_another_state_is_marked_as_elsewhere(monkeypatch):
    """A 'Trenton' layer can easily be the Trenton in another state."""
    other = "https://services.arcgis.com/a/arcgis/rest/services/Elsewhere/FeatureServer/0"
    _hub(monkeypatch, [_item("Trenton Zoning", other), _item("Local", OPEN_URL)])
    monkeypatch.setattr(esri, "describe", _describe_stub)
    rows = esri.search("zoning trenton", bbox=(-74.82, 40.19, -74.70, 40.26))
    by = {r["name"]: r for r in rows}         # the catalogue's name, not the service's
    assert by["Local"]["covers"] is True
    assert by["Trenton Zoning"]["covers"] is False
    assert rows[0]["covers"] is True          # the one that fits comes first


def test_web_maps_and_folders_are_dropped(monkeypatch):
    _hub(monkeypatch, [
        _item("A web map", "https://somewhere.opendata.arcgis.com/"),
        _item("A folder", "https://services.arcgis.com/a/arcgis/rest/services/X/FeatureServer"),
        _item("A real layer", OPEN_URL)])
    monkeypatch.setattr(esri, "describe", _describe_stub)
    rows = esri.search("parcels")
    assert [r["url"] for r in rows] == [OPEN_URL]


def test_duplicate_urls_appear_once(monkeypatch):
    _hub(monkeypatch, [_item("A", OPEN_URL), _item("A again", OPEN_URL)])
    monkeypatch.setattr(esri, "describe", _describe_stub)
    assert len(esri.search("parcels")) == 1


def test_no_matches_suggests_how_to_ask(monkeypatch):
    _hub(monkeypatch, [])
    with pytest.raises(esri.EsriError) as e:
        esri.search("qqqq")
    assert "county or town name" in str(e.value)


def test_an_empty_query_is_refused():
    with pytest.raises(esri.EsriError):
        esri.search("  ")


# ---------- what a planner reads when a free service is down ----------

def test_an_outage_names_the_service_and_says_it_is_not_your_fault():
    import requests as rq

    from branch.server import _readable

    msg = _readable(rq.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='overpass-api.de', port=443): Max retries"))
    assert "Overpass" in msg
    assert "Nothing is wrong with your setup" in msg
    assert "HTTPSConnectionPool" not in msg


def test_a_timeout_suggests_a_smaller_area():
    import requests as rq

    from branch.server import _readable

    msg = _readable(rq.exceptions.Timeout("tigerweb.geo.census.gov timed out"))
    assert "Census" in msg and "smaller area" in msg


def test_an_unknown_service_still_reads_as_a_sentence():
    import requests as rq

    from branch.server import _readable

    msg = _readable(rq.exceptions.ConnectionError("something.example.org refused"))
    assert msg.startswith("a public data service did not answer")


def test_a_real_programming_error_is_not_dressed_up_as_an_outage():
    """A bug in branch must stay visible, not be blamed on someone else's server."""
    from branch.server import _readable

    assert _readable(KeyError("boom")) == "KeyError: 'boom'"
