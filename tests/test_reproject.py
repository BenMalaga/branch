"""A file in the wrong coordinate system must not be drawn as if it were right.

Exports from desktop GIS routinely carry State Plane feet or UTM meters even
though GeoJSON is defined as longitude and latitude. Two failure modes matter:
coordinates that are obviously not degrees, and a wrong EPSG code that produces
coordinates which are perfectly valid and completely elsewhere. Read as Web
Mercator, a New York file lands in the Gulf of Guinea, and nothing about the
numbers alone reveals that.
"""
import pytest

from branch import registry

NYC = [-74.05, 40.60, -73.85, 40.85]
STATE_PLANE = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "City Hall"},
     "geometry": {"type": "Point", "coordinates": [982500.0, 195000.0]}}]}
LONLAT = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {},
     "geometry": {"type": "Point", "coordinates": [-73.99, 40.70]}}]}


def _run(**p):
    return registry.get("reproject").run(p)


def test_projected_coordinates_are_detected():
    assert registry.looks_projected(STATE_PLANE)["projected"] is True
    assert registry.looks_projected(LONLAT)["projected"] is False


def test_the_right_epsg_lands_in_the_right_place():
    out = _run(layer=STATE_PLANE, from_crs="EPSG:2263", near=NYC)
    lon, lat = out["result"]["features"][0]["geometry"]["coordinates"]
    assert -74.02 < lon < -73.99 and 40.69 < lat < 40.72     # Lower Manhattan


def test_a_wrong_epsg_is_caught_when_we_know_where_the_data_belongs():
    with pytest.raises(ValueError, match="nowhere near"):
        _run(layer=STATE_PLANE, from_crs="EPSG:3857", near=NYC)


def test_without_a_code_it_asks_and_suggests_real_candidates():
    with pytest.raises(ValueError) as err:
        _run(layer=STATE_PLANE, near=NYC)
    assert "EPSG:2263" in str(err.value)      # the actual NY Long Island system


def test_a_meaningless_code_is_rejected_plainly():
    with pytest.raises(ValueError, match="not a coordinate system"):
        _run(layer=STATE_PLANE, from_crs="EPSG:99999999")


def test_candidates_are_local_to_where_you_are_looking():
    codes = [c["code"] for c in registry.crs_candidates(NYC)]
    assert "EPSG:2263" in codes
    assert not registry.crs_candidates(None)
