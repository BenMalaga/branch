"""Counting things inside areas.

The two failures that matter are both invisible on the finished map: an area
with nothing in it quietly disappearing, so the choropleth implies coverage
everywhere, and one item being counted in two areas, so the totals exceed the
whole.
"""
import math

import pytest

from branch import registry


def _run(params):
    return registry.get("summarize_within").run(params)


LAT, LON = 40.35, -74.65


def _box(w_deg, s_deg, e_deg, n_deg, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [[
                [w_deg, s_deg], [e_deg, s_deg], [e_deg, n_deg],
                [w_deg, n_deg], [w_deg, s_deg]]]}}


def _pt(lon, lat, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def _fc(*f):
    return {"type": "FeatureCollection", "features": list(f)}


# two side by side areas, each 0.01 degrees square
WEST = _box(LON, LAT, LON + 0.01, LAT + 0.01, NAME="West")
EAST = _box(LON + 0.01, LAT, LON + 0.02, LAT + 0.01, NAME="East")
AREAS = _fc(WEST, EAST)


def _props(out, name):
    for f in out["result"]["features"]:
        if f["properties"]["NAME"] == name:
            return f["properties"]
    raise AssertionError(f"{name} is missing from the result")


def test_things_are_counted_into_the_right_area():
    items = _fc(_pt(LON + 0.002, LAT + 0.005), _pt(LON + 0.004, LAT + 0.005),
                _pt(LON + 0.015, LAT + 0.005))
    out = _run({"areas": AREAS, "items": items})
    assert _props(out, "West")["count"] == 2
    assert _props(out, "East")["count"] == 1


def test_an_empty_area_is_kept_at_zero_not_dropped():
    """Dropping it would make the map claim every area has something in it."""
    items = _fc(_pt(LON + 0.002, LAT + 0.005))
    out = _run({"areas": AREAS, "items": items})
    assert len(out["result"]["features"]) == 2
    assert _props(out, "East")["count"] == 0
    assert out["recipe"]["empty_areas"] == 1
    assert "a finding" in out["recipe"]["note"]


def test_nothing_is_counted_twice():
    """A polygon straddling the boundary belongs to one area, not both."""
    straddler = _box(LON + 0.008, LAT + 0.004, LON + 0.012, LAT + 0.006)
    out = _run({"areas": AREAS, "items": _fc(straddler)})
    total = sum(f["properties"]["count"] for f in out["result"]["features"])
    assert total == 1
    assert out["recipe"]["items_counted"] == 1


def test_items_outside_every_area_are_reported_not_silently_lost():
    items = _fc(_pt(LON + 0.002, LAT + 0.005), _pt(LON + 5, LAT + 5))
    out = _run({"areas": AREAS, "items": items})
    assert out["recipe"]["items_outside"] == 1
    assert "left out" in out["recipe"]["note"]


def test_totals_and_averages_of_a_column():
    items = _fc(_pt(LON + 0.002, LAT + 0.005, DBH=10),
                _pt(LON + 0.004, LAT + 0.005, DBH=20),
                _pt(LON + 0.015, LAT + 0.005, DBH=7))
    out = _run({"areas": AREAS, "items": items, "field": "DBH"})
    west = _props(out, "West")
    assert west["total"] == 30
    assert west["average"] == 15
    assert _props(out, "East")["total"] == 7


def test_an_empty_area_has_no_average_rather_than_an_average_of_zero():
    """Zero is a measurement. No trees means there is nothing to average."""
    items = _fc(_pt(LON + 0.002, LAT + 0.005, DBH=10))
    out = _run({"areas": AREAS, "items": items, "field": "DBH"})
    east = _props(out, "East")
    assert east["count"] == 0
    assert east["total"] is None
    assert east["average"] is None


def test_text_that_looks_numeric_is_still_totalled():
    items = _fc(_pt(LON + 0.002, LAT + 0.005, DBH="10"),
                _pt(LON + 0.004, LAT + 0.005, DBH="20"))
    out = _run({"areas": AREAS, "items": items, "field": "DBH"})
    assert _props(out, "West")["total"] == 30


def test_acres_are_measured_on_the_ground():
    """0.01 degrees square near 40N is roughly 1.1 km by 0.85 km, about 235 acres."""
    out = _run({"areas": AREAS, "items": _fc(_pt(LON + 0.002, LAT + 0.005))})
    acres = _props(out, "West")["acres"]
    assert 180 < acres < 300, acres


def test_the_per_acre_rate_matches_the_count_and_the_area():
    items = _fc(*[_pt(LON + 0.002 + i * 0.0005, LAT + 0.005) for i in range(10)])
    out = _run({"areas": AREAS, "items": items})
    p = _props(out, "West")
    assert p["count"] == 10
    assert abs(p["per_acre"] - 10 / p["acres"]) < 1e-4


def test_a_bigger_area_has_a_lower_rate_for_the_same_count():
    big = _fc(_box(LON, LAT, LON + 0.04, LAT + 0.04, NAME="West"))
    items = _fc(_pt(LON + 0.002, LAT + 0.005), _pt(LON + 0.004, LAT + 0.005))
    small = _run({"areas": _fc(WEST), "items": items})
    large = _run({"areas": big, "items": items})
    assert _props(small, "West")["per_acre"] > _props(large, "West")["per_acre"]


def test_the_result_comes_back_in_degrees():
    out = _run({"areas": AREAS, "items": _fc(_pt(LON + 0.002, LAT + 0.005))})
    c = out["result"]["features"][0]["geometry"]["coordinates"][0][0]
    assert abs(c[0]) <= 180 and abs(c[1]) <= 90


# ---------- refusing rather than producing a plausible empty map ----------

def test_an_empty_items_layer_is_refused():
    with pytest.raises(ValueError) as e:
        _run({"areas": AREAS, "items": _fc()})
    # the message must name WHICH input was empty, not talk about geometry kwargs
    assert "layer being counted" in str(e.value)
    assert "geometry=" not in str(e.value)


def test_an_empty_areas_layer_is_refused():
    with pytest.raises(ValueError):
        _run({"areas": _fc(), "items": _fc(_pt(LON, LAT))})


def test_a_column_that_does_not_exist_lists_the_real_ones():
    items = _fc(_pt(LON + 0.002, LAT + 0.005, DBH=10))
    with pytest.raises(ValueError) as e:
        _run({"areas": AREAS, "items": items, "field": "DIAMETER"})
    assert "DIAMETER" in str(e.value) and "DBH" in str(e.value)


def test_a_column_with_no_numbers_says_so():
    items = _fc(_pt(LON + 0.002, LAT + 0.005, SPECIES="oak"))
    with pytest.raises(ValueError) as e:
        _run({"areas": AREAS, "items": items, "field": "SPECIES"})
    assert "holds no numbers" in str(e.value)
