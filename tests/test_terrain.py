"""Hills change what a walk costs, and the tool must say whether it knew.

A walkshed drawn flat claims a quarter mile of San Francisco stairs is the same
five minutes as a quarter mile of flat Brooklyn. Measured against USGS elevation,
the flat assumption overstates a ten minute reach in San Francisco by about 30
percent. The error is invisible in the output, which is what makes it worth a
test.
"""
import numpy as np
import pytest

from branch import terrain


def test_the_speed_curve_peaks_slightly_downhill():
    """Tobler's function is not symmetric: a gentle descent is faster than flat."""
    down = terrain.tobler_speed(-0.05, 4.8)
    flat = terrain.tobler_speed(0.0, 4.8)
    up = terrain.tobler_speed(0.05, 4.8)
    assert down > flat > up
    assert flat == pytest.approx(4.8, rel=1e-9)   # the caller's number still means flat


def test_climbing_is_slower_the_steeper_it_gets():
    speeds = [terrain.tobler_speed(s, 4.8) for s in (0.0, 0.05, 0.0833, 0.2)]
    assert speeds == sorted(speeds, reverse=True)
    assert speeds[-1] < speeds[0] / 1.8          # a 20% grade roughly halves it


def test_slope_is_direction_aware():
    """The same street is uphill one way and downhill the other."""
    assert terrain.edge_slope(10.0, 30.0, 100.0) == pytest.approx(0.2)
    assert terrain.edge_slope(30.0, 10.0, 100.0) == pytest.approx(-0.2)


def test_unknown_elevation_is_treated_as_flat_not_as_a_cliff():
    assert terrain.edge_slope(float("nan"), 30.0, 100.0) == 0.0
    assert terrain.edge_slope(10.0, 30.0, 0.0) == 0.0


def test_grades_are_labelled_the_way_a_reviewer_would():
    assert "level walkway" in terrain.grade_label(0.03)
    assert "ramp" in terrain.grade_label(0.06)
    assert "steeper" in terrain.grade_label(0.12)
    assert terrain.grade_label(-0.12) == terrain.grade_label(0.12)   # down is as steep


def test_missing_elevation_data_is_survivable():
    """Outside 3DEP coverage the tool falls back to flat rather than failing."""
    assert np.isnan(terrain.sample(None, [(0.0, 0.0)])).all()
