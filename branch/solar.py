"""Solar geometry and tree-shadow modeling.

Two jobs:

1. Where is the sun? ``sun_position`` wraps pysolar to get the solar altitude
   (angle above the horizon) and azimuth (compass bearing, clockwise from
   north) for a location and instant.

2. What shadow does a tree cast? A street tree is modeled as a crown (a disc of
   radius ``crown_radius``) floating at the crown's center height. The sun
   projects that disc onto the ground, offset horizontally away from the sun.
   ``tree_shadow_polygon`` returns the swept ground footprint of that shadow.

Shadow geometry (standard sun-path trigonometry):
    shadow_length = object_height / tan(solar_altitude)
    shadow_bearing = solar_azimuth + 180   (points directly away from the sun)
See e.g. NOAA solar-position docs and any surveying text.

Tree size comes from trunk diameter (DBH) via a transparent, tunable
first-order allometric model (see ``crown_radius_m`` / ``tree_height_m``). The
coefficients are open-grown-urban-tree approximations, not species-exact; they
are module-level constants so they are easy to refine against USDA Forest
Service / i-Tree growth equations.
"""
from __future__ import annotations

import datetime as dt
import math
import warnings
from zoneinfo import ZoneInfo

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# pysolar emits a leap-second UserWarning for very recent years; its effect is
# sub-arcsecond and irrelevant for shadow modeling, so silence just that noise.
warnings.filterwarnings("ignore", message="Leap seconds")
from pysolar.solar import get_altitude, get_azimuth  # noqa: E402


# --- Time helpers ------------------------------------------------------------
def to_utc(local_str: str, tzname: str) -> dt.datetime:
    """Parse a local ``"YYYY-MM-DD HH:MM"`` string into a tz-aware UTC datetime."""
    naive = dt.datetime.strptime(local_str.strip(), "%Y-%m-%d %H:%M")
    local = naive.replace(tzinfo=ZoneInfo(tzname))
    return local.astimezone(dt.timezone.utc)


# --- Sun position ------------------------------------------------------------
def sun_position(lat: float, lon: float, when_utc: dt.datetime) -> tuple[float, float]:
    """Return (altitude_deg, azimuth_deg) of the sun.

    altitude: degrees above the horizon (negative = below horizon / night).
    azimuth:  degrees clockwise from north (0=N, 90=E, 180=S, 270=W).
    """
    if when_utc.tzinfo is None:
        raise ValueError("when_utc must be timezone-aware (UTC)")
    altitude = get_altitude(lat, lon, when_utc)
    azimuth = get_azimuth(lat, lon, when_utc)
    return altitude, azimuth


# --- Allometry: trunk diameter (DBH, inches) -> crown & height (meters) ------
# Crown radius uses the near-linear crown-to-DBH relationship for urban trees:
# crown *diameter* (m) ~= 0.63 * DBH (in), i.e. the crown:DBH ratio of ~24-27
# reported in Urban Forestry & Urban Greening 44 (2019), art. 126421. Anchored
# check: a mature London plane (DBH ~24 in) -> ~15 m crown spread, matching
# field guides. So crown *radius* ~= 0.315 * DBH_in.
#
# Height uses a sublinear power law capped near species maxima. Both are
# transparent first-order models; refine per species against the authoritative
# USDA Forest Service Urban Tree Database (McPherson, van Doorn & Peper 2016,
# RDS-2016-0005), which provides height/crown/leaf-area equations for 365
# species-region sets. Coefficients are module-level constants so that's a
# one-line change.
CROWN_PER_IN = 0.315              # crown radius, meters per inch of DBH
CROWN_MIN_M, CROWN_MAX_M = 0.6, 9.0
HEIGHT_A, HEIGHT_B = 1.5, 0.72    # total height, meters (a * DBH_in ** b)
HEIGHT_MIN_M, HEIGHT_MAX_M = 2.0, 28.0


def crown_radius_m(dbh_in: float) -> float:
    """Estimate crown (canopy) radius in meters from DBH in inches."""
    r = CROWN_PER_IN * max(dbh_in, 0.0)
    return min(max(r, CROWN_MIN_M), CROWN_MAX_M)


def tree_height_m(dbh_in: float) -> float:
    """Estimate total tree height in meters from DBH in inches."""
    h = HEIGHT_A * max(dbh_in, 0.0) ** HEIGHT_B
    return min(max(h, HEIGHT_MIN_M), HEIGHT_MAX_M)


# --- Shadow geometry ---------------------------------------------------------
def shadow_offset(height_m: float, altitude_deg: float, azimuth_deg: float,
                  max_len: float) -> tuple[float, float]:
    """Ground offset (east_m, north_m) from an object's base to its shadow tip.

    Returns (0, 0) when the sun is at or below the horizon (no shadow cast).
    """
    if altitude_deg <= 0.5:  # sun down or grazing: treat as no directional shadow
        return 0.0, 0.0
    length = height_m / math.tan(math.radians(altitude_deg))
    length = min(length, max_len)
    bearing = math.radians((azimuth_deg + 180.0) % 360.0)  # away from the sun
    east = length * math.sin(bearing)
    north = length * math.cos(bearing)
    return east, north


def tree_shadow_polygon(x: float, y: float, crown_r: float, height_m: float,
                        altitude_deg: float, azimuth_deg: float,
                        max_len: float) -> BaseGeometry | None:
    """Ground footprint of one tree's crown shadow, in the metric CRS.

    (x, y) is the trunk base in projected meters. The crown disc is projected
    from its center height (~70% of tree height) to the ground and swept from
    the trunk to the shadow tip, giving a capsule-shaped footprint (the convex
    hull of the crown at the base and the crown at the tip). Returns ``None``
    at night.
    """
    if altitude_deg <= 0:
        return None
    crown_center_h = max(crown_r, 0.7 * height_m)
    dx, dy = shadow_offset(crown_center_h, altitude_deg, azimuth_deg, max_len)
    base = Point(x, y).buffer(crown_r, quad_segs=8)
    if dx == 0.0 and dy == 0.0:
        return base
    tip = Point(x + dx, y + dy).buffer(crown_r, quad_segs=8)
    return unary_union([base, tip]).convex_hull
