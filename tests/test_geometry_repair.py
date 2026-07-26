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


# ---------- what the adversarial audit turned up ----------

def test_owner_lookup_does_not_put_a_mailing_address_in_the_name_column():
    """OWNER_ADDRESS contains "owner". Matching it would print addresses as
    owner names on a legal notice."""
    cols = ["PIN", "OWNER_ADDRESS", "OWNER_CITY", "OWNERNAME1"]
    assert registry._pick_field(cols, registry.OWNER_HINTS,
                                exclude=registry.NOT_A_NAME) == "OWNERNAME1"
    assert registry._pick_field(cols, registry.ADDR_HINTS) == "OWNER_ADDRESS"


def test_owner_lookup_still_finds_a_plain_column():
    assert registry._pick_field(["PIN", "OWNER"], registry.OWNER_HINTS,
                                exclude=registry.NOT_A_NAME) == "OWNER"


def test_owner_lookup_admits_when_only_address_parts_exist():
    assert registry._pick_field(["PIN", "OWNER_ZIP", "OWNER_CITY"],
                                registry.OWNER_HINTS,
                                exclude=registry.NOT_A_NAME) is None


A = {"type": "Polygon", "coordinates": [[[-74.650, 40.35], [-74.640, 40.35],
     [-74.640, 40.36], [-74.650, 40.36], [-74.650, 40.35]]]}
B = {"type": "Polygon", "coordinates": [[[-74.645, 40.35], [-74.635, 40.35],
     [-74.635, 40.36], [-74.645, 40.36], [-74.645, 40.35]]]}


def test_overlapping_areas_do_not_produce_a_negative_count():
    """It read "2 of 1 counted. -1 fell outside every area", which is nonsense."""
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(A, NAME="A"), _f(B, NAME="B")),
         "items": _fc(_pt(-74.6425, 40.355))})       # inside both
    r = out["recipe"]
    assert r["items_counted"] == 1
    assert r["items_outside"] == 0
    assert r["items_in_several_areas"] == 1
    assert "-1" not in r["note"]


def test_overlap_is_disclosed_so_the_column_is_not_summed():
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(A, NAME="A"), _f(B, NAME="B")),
         "items": _fc(_pt(-74.6425, 40.355))})
    assert "do not sum the column" in out["recipe"]["note"]
    counts = [f["properties"]["count"] for f in out["result"]["features"]]
    assert counts == [1, 1]        # correct per area, and that is the point


def test_a_rate_over_a_county_sized_area_is_not_rounded_to_zero():
    """4 decimal places turned 1.06e-05 per acre into 0.0, which reads as none."""
    import math
    lat, side = 40.35, 48000.0
    dlat = side / 111_320.0
    dlon = side / (111_320.0 * math.cos(math.radians(lat)))
    county = {"type": "Polygon", "coordinates": [[
        [-74.65, lat], [-74.65 + dlon, lat], [-74.65 + dlon, lat + dlat],
        [-74.65, lat + dlat], [-74.65, lat]]]}
    pts = _fc(*[_pt(-74.65 + dlon * 0.1 * i, lat + dlat * 0.1) for i in range(1, 7)])
    p = registry.get("summarize_within").run(
        {"areas": _fc(_f(county, NAME="county")), "items": pts}
    )["result"]["features"][0]["properties"]
    assert p["per_acre"] > 0, "a real rate rounded away to zero"
    assert abs(p["per_acre"] - p["count"] / p["acres"]) < 1e-12


def test_a_city_scale_rate_keeps_readable_precision():
    """per_acre is computed from the full-precision area, while the acres shown
    beside it is rounded to two decimals for reading. So they agree closely but
    not exactly, and the check has to allow for the displayed rounding."""
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="a")), "items": _fc(_pt(-74.645, 40.355))})
    p = out["result"]["features"][0]["properties"]
    from_displayed = 1 / p["acres"]
    assert abs(p["per_acre"] - from_displayed) / from_displayed < 1e-4
    assert p["per_acre"] > 0.004   # and it kept real digits, not 0.0


def test_spreadsheet_numbers_are_read_not_silently_discarded():
    """["1,200","800","2,500","$300","450x"] totalled to 800 instead of 4800."""
    vals = ["1,200", "800", "2,500", "$300", "450x"]
    items = _fc(*[_pt(-74.645 + 0.0001 * i, 40.355, VAL=v)
                  for i, v in enumerate(vals)])
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="a")), "items": items, "field": "VAL"})
    p = out["result"]["features"][0]["properties"]
    assert p["total"] == 4800.0
    assert p["count"] == 5                      # all five are still features


def test_a_value_that_is_not_a_number_is_reported_not_hidden():
    items = _fc(_pt(-74.645, 40.355, VAL="1,200"),
                _pt(-74.6451, 40.3551, VAL="see deed"))
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="a")), "items": items, "field": "VAL"})
    assert out["recipe"]["values_unreadable"] == 1
    note = out["recipe"]["note"]
    assert "could not be read as a number" in note
    assert "was left out" in note               # singular, since there is one


@pytest.mark.parametrize("raw,expect", [
    ("1,200", 1200.0), ("$300", 300.0), ("  42 ", 42.0), ("-3.5", -3.5),
    ("1,234,567", 1234567.0), (7, 7.0), ("7", 7.0),
    ("450x", None), ("see deed", None), ("", None), (None, None), (True, None),
])
def test_the_number_parser_accepts_conventions_and_refuses_guesses(raw, expect):
    assert registry._to_number(raw) == expect


def test_an_items_column_named_area_id_does_not_crash_the_grouping():
    out = registry.get("summarize_within").run(
        {"areas": _fc(_f(SQUARE, NAME="a")),
         "items": _fc(_pt(-74.645, 40.355, _area_id=7))})
    assert out["result"]["features"][0]["properties"]["count"] == 1
