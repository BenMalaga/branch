"""The typed input contract is checked at call time, not just published.

Without this, a missing or mistyped input surfaced as whatever the geometry
library raised several frames later. Asking buffer for a "far" distance, or
cost_estimate to price a "unicorn", both reported a confusing error about
assigning a CRS to a frame with no geometry column, which tells the caller
nothing about what they actually got wrong.
"""
import pytest

from branch import registry

EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _bad(tool_id, params):
    with pytest.raises(registry.ParamError) as caught:
        registry.validate_params(registry.get(tool_id), params)
    return str(caught.value)


def test_missing_required_input_names_the_field_and_explains_it():
    msg = _bad("buffer", {"layer": EMPTY_FC})
    assert "distance_m" in msg and "meters" in msg


def test_wrong_type_says_what_was_wanted_and_what_arrived():
    msg = _bad("buffer", {"layer": EMPTY_FC, "distance_m": "far"})
    assert "must be number" in msg and "str" in msg


def test_enum_lists_the_allowed_values():
    msg = _bad("cost_estimate", {"layer": EMPTY_FC, "item": "unicorn"})
    assert "tree" in msg and "unicorn" in msg


def test_array_length_is_enforced():
    assert "at least 4" in _bad("osm", {"bbox": [-74, 40.7, -73.9]})


def test_array_item_types_are_enforced():
    msg = _bad("osm", {"bbox": [-74, 40.7, -73.9, "north"]})
    assert "must be number" in msg


def test_a_feature_collection_without_features_is_rejected():
    msg = _bad("clip", {"layer": {"type": "FeatureCollection"}, "boundary": EMPTY_FC})
    assert "features" in msg


def test_valid_calls_pass_untouched():
    registry.validate_params(registry.get("buffer"),
                             {"layer": EMPTY_FC, "distance_m": 100})
    registry.validate_params(registry.get("osm"),
                             {"bbox": [-74, 40.7, -73.9, 40.8],
                              "tags": {"amenity": "school"}})
    registry.validate_params(registry.get("spatial_join"),
                             {"target": EMPTY_FC, "join": EMPTY_FC,
                              "predicate": "within"})


def test_optional_inputs_may_be_omitted():
    """Only 'required' is required; defaults stay the tool's business."""
    registry.validate_params(registry.get("walkshed"), {"point": [40.7, -73.99]})


def test_every_registered_tool_publishes_a_usable_schema():
    for tool in registry.all_tools():
        assert tool.params.get("type") == "object", tool.id
        assert isinstance(tool.params.get("properties"), dict), tool.id
        for name in tool.params.get("required", []):
            assert name in tool.params["properties"], f"{tool.id}: {name}"
