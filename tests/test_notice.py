"""The abutter notice list.

A wrong list here is not a crash. It is a hearing that gets challenged because
somebody who should have been served was not. So the tests are about the three
ways it can be quietly wrong: the wrong units, the wrong reference geometry, and
the applicant appearing in their own list.
"""
import math

import pytest

from branch import registry

FT = 3.280839895


def _run(params):
    return registry.get("notice_list").run(params)


def _square(lon, lat, side_m, **props):
    """A square parcel of roughly ``side_m`` metres, at this latitude."""
    dlat = side_m / 111_320.0
    dlon = side_m / (111_320.0 * math.cos(math.radians(lat)))
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat],
                [lon, lat + dlat], [lon, lat]]]}}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


LAT, LON = 40.35, -74.65          # central New Jersey


def _at(east_m, north_m=0.0, side=30.0, **props):
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(LAT)))
    return _square(LON + dlon, LAT + dlat, side, **props)


SUBJECT = _fc(_at(0, 0, side=30, OWNER="Applicant LLC"))


def test_only_parcels_inside_the_radius_are_served():
    """A 200 foot radius is about 61 metres. The parcel 400 m away is not in it."""
    parcels = _fc(
        _at(0, 0, OWNER="Applicant LLC"),          # the subject itself
        _at(40, 0, OWNER="Close Neighbour"),       # well inside
        _at(400, 0, OWNER="Far Away"),             # well outside
    )
    out = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    names = [n["owner"] for n in out["recipe"]["notice_list"]]
    assert "Close Neighbour" in names
    assert "Far Away" not in names


def test_the_applicant_is_not_an_abutter_of_their_own_application():
    parcels = _fc(_at(0, 0, OWNER="Applicant LLC"), _at(40, 0, OWNER="Neighbour"))
    out = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    names = [n["owner"] for n in out["recipe"]["notice_list"]]
    assert names == ["Neighbour"]
    assert out["recipe"]["subject_parcels_excluded"] == 1


def test_distance_is_measured_in_feet_on_the_ground():
    """If this were computed in degrees the radius would be meaningless."""
    parcels = _fc(_at(0, 0, OWNER="Applicant LLC"), _at(100, 0, OWNER="Neighbour"))
    out = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 400})
    row = out["recipe"]["notice_list"][0]
    # 100 m east, minus the 30 m subject width, is about 70 m, near 230 feet.
    assert 200 < row["distance_ft"] < 260


def test_a_wider_radius_serves_strictly_more_people():
    parcels = _fc(_at(0, 0, OWNER="Applicant LLC"),
                  _at(40, 0, OWNER="A"), _at(120, 0, OWNER="B"),
                  _at(250, 0, OWNER="C"))
    near = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    wide = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 800})
    n = {x["owner"] for x in near["recipe"]["notice_list"]}
    w = {x["owner"] for x in wide["recipe"]["notice_list"]}
    assert n < w


def test_measured_from_the_property_line_not_the_centre():
    """On a deep lot the centroid is far from the boundary. A neighbour just past
    the end of a long parcel is within 200 feet of the LINE and must be served."""
    deep = _fc(_square(LON, LAT, 30))
    # stretch the subject 300 m north
    deep["features"][0]["geometry"]["coordinates"][0] = [
        [LON, LAT], [LON + 0.00035, LAT], [LON + 0.00035, LAT + 0.0027],
        [LON, LAT + 0.0027], [LON, LAT]]
    parcels = _fc(deep["features"][0], _at(0, 320, OWNER="Past The End"))
    out = _run({"parcels": parcels, "subject": deep, "distance_ft": 200})
    assert "Past The End" in [n["owner"] for n in out["recipe"]["notice_list"]]


def test_the_list_is_sorted_by_closeness():
    parcels = _fc(_at(0, 0, OWNER="Applicant LLC"), _at(150, 0, OWNER="Further"),
                  _at(45, 0, OWNER="Nearest"))
    out = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 900})
    d = [n["distance_ft"] for n in out["recipe"]["notice_list"]]
    assert d == sorted(d)


# ---------- refusing, rather than producing a list that looks complete ----------

def test_nobody_in_range_is_an_error_not_an_empty_list():
    # the subject is not itself in this parcel layer, so nothing at all is in range
    parcels = _fc(_at(5000, 0, OWNER="Distant"), _at(6000, 0, OWNER="Also Distant"))
    with pytest.raises(ValueError) as e:
        _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    assert "No parcels fall within" in str(e.value)


def test_only_the_subject_in_range_is_an_error():
    parcels = _fc(_at(0, 0, OWNER="Applicant LLC"))
    with pytest.raises(ValueError) as e:
        _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    assert "nobody to notify" in str(e.value)


def test_an_empty_parcel_layer_is_refused():
    with pytest.raises(ValueError) as e:
        _run({"parcels": _fc(), "subject": SUBJECT})
    assert "parcel layer" in str(e.value) and "geometry=" not in str(e.value)


def test_a_negative_radius_is_refused():
    parcels = _fc(_at(40, 0, OWNER="N"))
    with pytest.raises(ValueError):
        _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": -10})


def test_a_named_column_that_does_not_exist_is_refused_with_the_real_columns():
    parcels = _fc(_at(0, 0, OWNER="Applicant LLC"), _at(40, 0, OWNER="N"))
    with pytest.raises(ValueError) as e:
        _run({"parcels": parcels, "subject": SUBJECT, "owner_field": "OWNR"})
    assert "OWNR" in str(e.value) and "OWNER" in str(e.value)


# ---------- finding the owner column, and admitting when it cannot ----------

@pytest.mark.parametrize("col", ["OWNER", "Owner_Name", "TAXPAYER", "deed_owner"])
def test_common_assessor_column_names_are_found(col):
    parcels = _fc(_at(0, 0, **{col: "Applicant LLC"}),
                  _at(40, 0, **{col: "Neighbour"}))
    out = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    assert out["recipe"]["owner_field"] == col
    assert out["recipe"]["notice_list"][0]["owner"] == "Neighbour"


def test_no_owner_column_says_so_instead_of_guessing():
    """Picking an arbitrary column would put the wrong names on a legal notice."""
    parcels = _fc(_at(0, 0, PIN="1"), _at(40, 0, PIN="2", SHAPE_AREA=1.0))
    out = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    assert out["recipe"]["owner_field"] is None
    assert "looks like an owner name" in out["recipe"]["note"]


def test_the_output_warns_that_local_rules_differ():
    parcels = _fc(_at(0, 0, OWNER="Applicant LLC"), _at(40, 0, OWNER="N"))
    out = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    assert "ordinance" in out["recipe"]["note"]


def test_the_layer_that_comes_back_is_in_degrees():
    parcels = _fc(_at(0, 0, OWNER="Applicant LLC"), _at(40, 0, OWNER="N"))
    out = _run({"parcels": parcels, "subject": SUBJECT, "distance_ft": 200})
    coords = out["result"]["features"][0]["geometry"]["coordinates"][0][0]
    assert abs(coords[0]) <= 180 and abs(coords[1]) <= 90


# ---------- what the adversarial audit turned up ----------

FOOT = [[-74.6500, 40.3500], [-74.6496, 40.3500], [-74.6496, 40.3503],
        [-74.6500, 40.3503], [-74.6500, 40.3500]]
ACROSS = [[-74.6490, 40.3500], [-74.6486, 40.3500], [-74.6486, 40.3503],
          [-74.6490, 40.3503], [-74.6490, 40.3500]]


def _poly(ring, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def test_condo_units_are_not_silently_dropped_as_the_subject():
    """The old test was "more than half of this parcel is under the subject",
    which is true of every unit stacked on one footprint. Three real owners
    disappeared from a legal notice."""
    parcels = _fc(_poly(FOOT, OWNER="Applicant LLC"), _poly(FOOT, OWNER="Alice Nguyen"),
                  _poly(FOOT, OWNER="Bob Ruiz"), _poly(ACROSS, OWNER="Across The Street"))
    with pytest.raises(ValueError) as e:
        _run({"parcels": parcels, "subject": _fc(_poly(FOOT, OWNER="Applicant LLC")),
              "distance_ft": 200})
    msg = str(e.value)
    assert "different owners" in msg
    assert "Alice Nguyen" in msg and "Bob Ruiz" in msg
    assert "will not guess" in msg


def test_one_parcel_matching_the_subject_is_still_excluded_quietly():
    parcels = _fc(_poly(FOOT, OWNER="Applicant LLC"), _poly(ACROSS, OWNER="Neighbour"))
    out = _run({"parcels": parcels, "subject": _fc(_poly(FOOT, OWNER="Applicant LLC")),
                "distance_ft": 300})
    assert [n["owner"] for n in out["recipe"]["notice_list"]] == ["Neighbour"]
    assert out["recipe"]["subject_parcels_excluded"] == 1


def test_a_point_cannot_be_a_subject_property():
    """A radius runs from a property line, not from a dot."""
    with pytest.raises(ValueError) as e:
        _run({"parcels": _fc(_poly(ACROSS, OWNER="N")),
              "subject": _fc({"type": "Feature", "properties": {},
                              "geometry": {"type": "Point",
                                           "coordinates": [-74.6498, 40.3501]}}),
              "distance_ft": 200})
    assert "no area to measure from" in str(e.value)


def test_a_parcel_with_no_shape_is_reported_not_just_dropped():
    """It is a person who will not be served, so it cannot vanish quietly."""
    out = _run({"parcels": _fc(_poly(FOOT, OWNER="Applicant LLC"),
                               _poly(ACROSS, OWNER="Neighbour"),
                               {"type": "Feature", "properties": {"OWNER": "No Shape"},
                                "geometry": None}),
                "subject": _fc(_poly(FOOT, OWNER="Applicant LLC")), "distance_ft": 300})
    assert out["recipe"]["parcels_unusable"] == 1
    assert "look" in out["recipe"]["note"].lower()
    assert "not the same as a parcel outside the radius" in out["recipe"]["note"]


def test_a_duplicated_parcel_is_served_once():
    out = _run({"parcels": _fc(_poly(FOOT, OWNER="Applicant LLC"),
                               _poly(ACROSS, OWNER="Neighbour"),
                               _poly(ACROSS, OWNER="Neighbour")),
                "subject": _fc(_poly(FOOT, OWNER="Applicant LLC")), "distance_ft": 300})
    assert [n["owner"] for n in out["recipe"]["notice_list"]] == ["Neighbour"]
    assert out["recipe"]["duplicates_removed"] == 1
    assert "was removed" in out["recipe"]["note"]


def test_a_name_split_across_columns_is_served_whole():
    """Serving "Nguyen" is not serving "Alice Nguyen"."""
    out = _run({"parcels": _fc(_poly(FOOT, OWNER_FIRST_NAME="App", OWNER_LAST_NAME="Licant"),
                               _poly(ACROSS, OWNER_FIRST_NAME="Alice", OWNER_LAST_NAME="Nguyen")),
                "subject": _fc(_poly(FOOT, OWNER_FIRST_NAME="App", OWNER_LAST_NAME="Licant")),
                "distance_ft": 300})
    assert [n["owner"] for n in out["recipe"]["notice_list"]] == ["Alice Nguyen"]


@pytest.mark.parametrize("cols,expect", [
    (["OWNER_OCCUPIED", "OWNER_FULL"], "OWNER_FULL"),
    (["NO_OWNER_FLAG", "GRANTEE_NAME"], "GRANTEE_NAME"),
    (["PIN", "OWNER_ADDRESS", "OWNER_CITY", "OWNERNAME1"], "OWNERNAME1"),
])
def test_a_flag_or_an_address_is_never_taken_for_a_name(cols, expect):
    from branch.registry import NOT_A_NAME, OWNER_HINTS, _pick_field
    assert _pick_field(cols, OWNER_HINTS, exclude=NOT_A_NAME) == expect


def test_an_address_key_is_not_taken_for_an_address():
    from branch.registry import ADDR_HINTS, NOT_AN_ADDRESS, _pick_field
    assert _pick_field(["ADDRESS_ID", "SITE_ADDR"], ADDR_HINTS,
                       exclude=NOT_AN_ADDRESS) == "SITE_ADDR"
