"""Grounding tests: the answer must be measured on the right ground, or refused.

These cover two failures that shipped and were silent, which is the dangerous
kind: both produced a confident, re-runnable answer that was wrong.

1. Every area was projected into UTM zone 18N (New York), so a length or an
   area computed anywhere else was inflated the further you got from it.
2. The tree census is a New York dataset, but a request outside New York simply
   returned no trees, which the shade model reads as "no shade anywhere" and
   turns into a coolest-route recommendation built on nothing.
"""
from pyproj import Geod, Transformer

from branch import config, data, sources

GEOD = Geod(ellps="WGS84")


def _area_at(lat, lon, pad=0.01):
    return data.area_from_bbox((lon - pad, lat - pad, lon + pad, lat + pad))


def test_area_picks_its_own_utm_zone():
    """An area's projection follows the ground it covers, not the demo city."""
    assert _area_at(40.71, -73.99).metric_crs == "EPSG:32618"   # New York
    assert _area_at(41.88, -87.63).metric_crs == "EPSG:32616"   # Chicago
    assert _area_at(34.05, -118.24).metric_crs == "EPSG:32611"  # Los Angeles
    assert _area_at(51.51, -0.13).metric_crs == "EPSG:32630"    # London


def test_southern_hemisphere_uses_a_southern_zone():
    """326xx is north, 327xx is south. Sydney must not get a northern zone."""
    assert _area_at(-33.87, 151.21).metric_crs == "EPSG:32756"


def test_one_kilometre_measures_as_one_kilometre_worldwide():
    """The bug in numbers: distance error must stay under a tenth of a percent.

    Before the fix this was +21% in Los Angeles and +25% in London, which is a
    +48% and +56% error once it reaches an area.
    """
    for lat, lon in [(40.71, -73.99), (41.88, -87.63), (39.74, -104.99),
                     (34.05, -118.24), (51.51, -0.13), (-33.87, 151.21)]:
        crs = _area_at(lat, lon).metric_crs
        to_metric = Transformer.from_crs(config.WGS84, crs, always_xy=True)
        lon2, lat2, _ = GEOD.fwd(lon, lat, 90, 1000.0)   # exactly 1 km east
        x1, y1 = to_metric.transform(lon, lat)
        x2, y2 = to_metric.transform(lon2, lat2)
        measured = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        assert abs(measured - 1000.0) < 1.0, f"{crs} measured 1 km as {measured:.1f} m"


def test_tree_census_covers_new_york_only():
    assert sources.covers("nyc_street_trees", (-73.99, 40.70, -73.98, 40.71))
    assert not sources.covers("nyc_street_trees", (-118.3, 34.0, -118.2, 34.1))


def test_asking_outside_the_extent_is_an_error_not_an_empty_answer():
    """Refusing is the feature. An empty tree set reads as 'no shade anywhere'."""
    sources.require("nyc_street_trees", (-73.99, 40.70, -73.98, 40.71))  # inside: fine
    try:
        sources.require("nyc_street_trees", (-118.3, 34.0, -118.2, 34.1))
    except sources.CoverageError as err:
        assert "NYC Street Tree Census" in str(err)
        assert "walkshed" in str(err)      # says what does work instead
    else:
        raise AssertionError("a request outside the extent must raise CoverageError")


def test_a_partly_covered_area_is_not_covered():
    """Half in, half out still cannot be answered honestly."""
    assert not sources.covers("nyc_street_trees", (-74.5, 40.70, -73.98, 40.71))
