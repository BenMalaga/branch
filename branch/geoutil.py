"""Small CRS transform helpers shared by routing and visualization.

Geometry math runs in the metric CRS; web maps and GeoJSON need WGS84. These
wrap pyproj so the rest of the code never touches transformer boilerplate.
"""
from __future__ import annotations

from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from . import config

_TO_METRIC = Transformer.from_crs(config.WGS84, config.METRIC_CRS, always_xy=True)
_TO_WGS = Transformer.from_crs(config.METRIC_CRS, config.WGS84, always_xy=True)


def latlon_to_xy(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) degrees -> (x, y) meters in the metric CRS."""
    x, y = _TO_METRIC.transform(lon, lat)
    return x, y


def geom_to_wgs(geom: BaseGeometry) -> BaseGeometry:
    """Reproject a metric-CRS shapely geometry to WGS84 (lon/lat)."""
    return transform(_TO_WGS.transform, geom)


def xy_to_latlon(x: float, y: float) -> tuple[float, float]:
    """(x, y) meters -> (lat, lon) degrees."""
    lon, lat = _TO_WGS.transform(x, y)
    return lat, lon
