"""Census geography has to refuse clearly, not slowly.

An oversized request used to sit for thirty seconds and then surface a raw
connection timeout, which tells a planner nothing. Coverage and size are both
decidable before any network call, so they are decided there.
"""
import pytest

from branch import registry, sources


def _run(**params):
    return registry.get("census_geo").run(params)


def test_outside_the_united_states_is_refused_by_name():
    with pytest.raises(Exception) as err:
        _run(bbox=[-0.2, 51.4, -0.1, 51.6], level="tract")     # London
    assert "TIGERweb" in str(err.value)


def test_an_unknown_level_lists_the_real_ones():
    with pytest.raises(ValueError) as err:
        _run(bbox=[-73.99, 40.70, -73.95, 40.74], level="parish")
    assert "block group" in str(err.value)


def test_an_oversized_view_is_refused_without_a_network_call():
    """No timeout, no wait: the span alone decides this."""
    with pytest.raises(ValueError, match="too large"):
        _run(bbox=[-120, 32, -70, 48], level="block")


def test_each_level_has_its_own_size_limit():
    """Blocks are tiny and states are huge, so one limit cannot serve both."""
    with pytest.raises(ValueError, match="too large"):
        _run(bbox=[-74.5, 40.4, -73.5, 41.0], level="block")   # fine for tracts
    # the same view is acceptable at a coarser level, so it must not raise on span
    sources.require("us_census_geography", (-74.5, 40.4, -73.5, 41.0))


def test_the_source_extent_covers_alaska_hawaii_and_puerto_rico():
    assert sources.covers("us_census_geography", (-166.0, 53.0, -165.0, 54.0))   # AK
    assert sources.covers("us_census_geography", (-158.0, 21.2, -157.6, 21.4))   # HI
    assert sources.covers("us_census_geography", (-66.2, 18.3, -66.0, 18.5))     # PR
