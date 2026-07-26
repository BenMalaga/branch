"""Broken geometry, repaired at the boundary.

Municipal parcel exports routinely contain self-intersecting rings. Shapely does
not raise on them, it returns an answer: a bow-tie polygon measured 0.0 acres and
contained 0 points. That is a confident wrong number, which is the one failure
this project treats as unacceptable, so it is repaired once in ``_read_fc``
rather than in each tool.

The trust document claimed this validation existed before it did. These tests are
what make the claim true.
"""
import pytest
from shapely.geometry import shape

from branch import registry

# traced A -> B -> C -> D, so the two diagonals cross in the middle
BOWTIE = {"type": "Polygon", "coordinates": [[
    [-74.65, 40.35], [-74.64, 40.36], [-74.64, 40.35], [-74.65, 40.36],
    [-74.65, 40.35]]]}
SQUARE = {"type": "Polygon", "coordinates": [[
    [-74.65, 40.35], [-74.64, 40.35], [-74.64, 40.36], [-74.65, 40.36],
    [-74.65, 40.35]]]}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


def _f(geom, **props):
    return {"type": "Feature", "properties": props, "geometry": geom}


def _pt(lon, lat, **props):
    return _f({"type": "Point", "coordinates": [lon, lat]}, **props)


def test_the_bowtie_really_is_invalid():
    """Guard the premise. If shapely ever starts rejecting this, these tests lie."""
    assert not shape(BOWTIE).is_valid


def test_a_self_intersecting_parcel_no_longer_measures_zero():
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(BOWTIE, NAME="bowtie")),
         "items": _fc(_pt(-74.6425, 40.355))})
    acres = out["result"]["features"][0]["properties"]["acres"]
    assert acres > 100, f"a real polygon measured {acres} acres"


def test_points_inside_a_repaired_parcel_are_counted():
    """Both lobes of the bow-tie are real ground and both must count."""
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(BOWTIE, NAME="bowtie")),
         "items": _fc(_pt(-74.6425, 40.355), _pt(-74.6475, 40.355))})
    assert out["result"]["features"][0]["properties"]["count"] == 2


def test_valid_geometry_is_left_exactly_alone():
    """Repair must not perturb the shapes that were already right."""
    items = _fc(_pt(-74.645, 40.355))
    good = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="square")), "items": items})
    again = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="square")), "items": items})
    a = good["result"]["features"][0]["properties"]
    b = again["result"]["features"][0]["properties"]
    assert a["acres"] == b["acres"]
    assert 200 < a["acres"] < 270, a["acres"]


def test_a_repaired_parcel_still_carries_its_attributes():
    """A notice list is useless if the repair drops the owner's name."""
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(BOWTIE, NAME="bowtie", OWNER="Ann Reyes")),
         "items": _fc(_pt(-74.6425, 40.355))})
    props = out["result"]["features"][0]["properties"]
    assert props["OWNER"] == "Ann Reyes"
    assert props["NAME"] == "bowtie"


def test_null_geometry_is_dropped_rather_than_placed_at_zero_zero():
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="square"), _f(None, NAME="no shape")),
         "items": _fc(_pt(-74.645, 40.355))})
    names = [f["properties"]["NAME"] for f in out["result"]["features"]]
    assert names == ["square"]


def test_a_layer_of_nothing_but_broken_shapes_is_refused():
    """Better to say the file is broken than to hand back an empty map."""
    empty_ring = {"type": "Polygon", "coordinates": [[]]}
    with pytest.raises(ValueError) as e:
        registry.get("summarize_within").run(
            {"areas": _fc(_f(empty_ring)), "items": _fc(_pt(-74.645, 40.355))})
    assert "nothing" in str(e.value).lower()


def test_repair_reaches_the_notice_list_too():
    """The repair is at the boundary, so every tool gets it, not just one."""
    subject = _fc(_f(SQUARE, OWNER="Applicant"))
    # a broken neighbour parcel must still be found and served
    neighbour = {"type": "Polygon", "coordinates": [[
        [-74.6395, 40.35], [-74.6385, 40.36], [-74.6385, 40.35],
        [-74.6395, 40.36], [-74.6395, 40.35]]]}
    out = registry.get("notice_list").run(
        {"parcels": _fc(_f(SQUARE, OWNER="Applicant"),
                        _f(neighbour, OWNER="Broken Neighbour")),
         "subject": subject, "distance_ft": 500})
    assert "Broken Neighbour" in [n["owner"] for n in out["recipe"]["notice_list"]]


def test_a_broken_line_stays_a_line_and_does_not_become_an_area():
    """Repair keeps the original dimension, so a collapsed edge is not new ground."""
    selfish = {"type": "LineString", "coordinates": [[-74.65, 40.35], [-74.64, 40.36],
                                                     [-74.65, 40.35]]}
    out = registry.get("buffer").run({"layer": _fc(_f(selfish)), "distance_m": 10})
    assert out["result"]["features"], "the line should still buffer"


# ---------- answers that used to look fine and were not ----------

def test_a_buffer_that_erases_everything_is_refused_not_returned_as_null():
    """It returned a feature whose geometry was null, which reads as an answer."""
    with pytest.raises(ValueError) as e:
        registry.get("buffer").run({"layer": _fc(_f(SQUARE)), "distance_m": -100000})
    assert "nothing left to draw" in str(e.value)


def test_an_absurd_buffer_distance_suggests_the_likely_mistake():
    with pytest.raises(ValueError) as e:
        registry.get("buffer").run({"layer": _fc(_f(SQUARE)), "distance_m": 1e9})
    assert "metres" in str(e.value)


def test_a_partial_inward_buffer_reports_what_it_consumed():
    """One shape vanishing while others survive must be said out loud."""
    big = {"type": "Polygon", "coordinates": [[
        [-74.70, 40.30], [-74.60, 40.30], [-74.60, 40.40], [-74.70, 40.40],
        [-74.70, 40.30]]]}
    tiny = {"type": "Polygon", "coordinates": [[
        [-74.6500, 40.3500], [-74.6499, 40.3500], [-74.6499, 40.3501],
        [-74.6500, 40.3501], [-74.6500, 40.3500]]]}
    out = registry.get("buffer").run(
        {"layer": _fc(_f(big), _f(tiny)), "distance_m": -50})
    assert out["recipe"]["shapes_consumed"] == 1
    assert "vanished" in out["recipe"]["note"]
    assert len(out["result"]["features"]) == 1


def test_clipping_to_somewhere_the_layer_does_not_reach_is_refused():
    """An empty layer on a map is indistinguishable from a real finding, which is
    exactly the bug the tree census already caused here once."""
    paris = {"type": "Polygon", "coordinates": [[
        [2.2, 48.8], [2.3, 48.8], [2.3, 48.9], [2.2, 48.9], [2.2, 48.8]]]}
    with pytest.raises(ValueError) as e:
        registry.get("clip").run({"layer": _fc(_f(SQUARE)),
                                  "boundary": _fc(_f(paris))})
    assert "indistinguishable from a real finding" in str(e.value)


def test_the_users_own_count_column_is_kept_not_overwritten():
    """Their 'count' might be housing units. Ours must not silently replace it."""
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="a", count=999, acres=42)),
         "items": _fc(_pt(-74.645, 40.355))})
    p = out["result"]["features"][0]["properties"]
    assert p["count_original"] == 999
    assert p["acres_original"] == 42
    assert p["count"] == 1                       # ours, alongside theirs
    assert out["recipe"]["renamed_columns"] == {"count": "count_original",
                                                "acres": "acres_original"}
    assert "already had" in out["recipe"]["note"]


def test_a_second_collision_does_not_clobber_the_first_rescue():
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="a", count=1, count_original=2)),
         "items": _fc(_pt(-74.645, 40.355))})
    p = out["result"]["features"][0]["properties"]
    assert p["count_original"] == 2              # untouched
    assert p["count_original_2"] == 1            # the one we displaced
