"""Which source covers which ground, and what to say when it does not.

A tool that quietly returns nothing outside its data's extent is worse than one
that fails: an empty tree set becomes "no shade anywhere", which the shade model
turns into a confident route and a recipe that looks computed. Every dataset
here declares the extent it is actually true for, and asking outside that extent
is an error with a name, not an empty answer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """A public dataset and the ground it actually covers."""
    id: str
    title: str
    extent: tuple[float, float, float, float]   # (west, south, east, north) WGS84
    vintage: str
    attribution: str


SOURCES: dict[str, Source] = {
    "nyc_street_trees": Source(
        id="nyc_street_trees",
        title="NYC Street Tree Census",
        extent=(-74.30, 40.48, -73.65, 40.95),   # the five boroughs
        vintage="2015",
        attribution="City of New York, via the Socrata open data API",
    ),
    "osm": Source(
        id="osm",
        title="OpenStreetMap",
        extent=(-180.0, -90.0, 180.0, 90.0),
        vintage="live",
        attribution="OpenStreetMap contributors, ODbL",
    ),
}


class CoverageError(RuntimeError):
    """Raised when a request falls outside a source's real extent."""


def covers(source_id: str, bbox: tuple[float, float, float, float]) -> bool:
    """Does ``source_id`` have data for every part of ``bbox``?"""
    src = SOURCES.get(source_id)
    if src is None:
        return False
    w, s, e, n = bbox
    ew, es, ee, en = src.extent
    return ew <= w and es <= s and ee >= e and en >= n


def require(source_id: str, bbox: tuple[float, float, float, float]) -> None:
    """Raise a readable CoverageError if the request is outside the source."""
    if covers(source_id, bbox):
        return
    src = SOURCES.get(source_id)
    if src is None:
        raise CoverageError(f"There is no source registered as '{source_id}'.")
    ew, es, ee, en = src.extent
    raise CoverageError(
        f"{src.title} ({src.vintage}) only covers "
        f"{ew}, {es} to {ee}, {en}, and your area is outside that. "
        f"branch will not guess at data it does not have. "
        f"Tools that work anywhere: walkshed, OpenStreetMap features, buffer, "
        f"spatial join, clip, density hotspots, and the cost and ROI tools."
    )
