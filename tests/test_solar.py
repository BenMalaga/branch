"""Solar geometry, allometry, and shadow-shape tests (no network)."""
import math

from branch import solar


def test_sun_high_and_southish_at_noon():
    when = solar.to_utc("2026-07-15 12:00", "America/New_York")
    alt, az = solar.sun_position(40.67, -73.98, when)
    assert alt > 40            # a summer midday sun is high in the sky
    assert 120 < az < 240      # and roughly to the south


def test_sun_below_horizon_at_night():
    when = solar.to_utc("2026-07-15 02:00", "America/New_York")
    alt, _ = solar.sun_position(40.67, -73.98, when)
    assert alt < 0


def test_shadow_points_away_from_sun_and_has_right_length():
    # Sun due south (az=180) at 45 deg altitude: a 10 m object casts a 10 m
    # shadow pointing due north (+y, east component ~0).
    dx, dy = solar.shadow_offset(10.0, 45.0, 180.0, max_len=100.0)
    assert dy > 0
    assert abs(dx) < 1e-6
    assert abs(math.hypot(dx, dy) - 10.0) < 1e-6


def test_no_shadow_when_sun_is_down():
    dx, dy = solar.shadow_offset(10.0, -5.0, 180.0, max_len=100.0)
    assert dx == 0.0 and dy == 0.0


def test_shadow_length_is_clamped():
    # Very low sun would give a huge shadow; it must clamp to max_len.
    _, dy = solar.shadow_offset(10.0, 1.0, 0.0, max_len=30.0)  # az 0 -> shadow south
    assert abs(dy) <= 30.0 + 1e-6


def test_allometry_monotonic_and_bounded():
    assert solar.crown_radius_m(3) < solar.crown_radius_m(24)
    assert solar.crown_radius_m(0) >= solar.CROWN_MIN_M
    assert solar.crown_radius_m(10_000) <= solar.CROWN_MAX_M
    assert solar.tree_height_m(3) < solar.tree_height_m(24)
    assert solar.tree_height_m(10_000) <= solar.HEIGHT_MAX_M


def test_shadow_polygon_is_larger_than_the_crown_disc():
    poly = solar.tree_shadow_polygon(0.0, 0.0, crown_r=2.0, height_m=10.0,
                                     altitude_deg=30.0, azimuth_deg=180.0,
                                     max_len=100.0)
    assert poly.area > math.pi * 2.0 ** 2  # crown disc plus the swept shadow
