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
