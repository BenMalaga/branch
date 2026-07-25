"""Small CRS transform helpers shared by routing and visualization.

Geometry math runs in the metric CRS; web maps and GeoJSON need WGS84. These
wrap pyproj so the rest of the code never touches transformer boilerplate.
"""
from __future__ import annotations

import contextvars
from functools import lru_cache

from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from . import config

# The metric CRS is a property of the ground being measured, not of the process,
# so it travels with the request. A ContextVar keeps that per-thread, which the
# threaded server needs: two users in different UTM zones must not share one.
_ACTIVE_CRS = contextvars.ContextVar("metric_crs", default=config.METRIC_CRS)


@lru_cache(maxsize=128)
def _transformers(crs: str) -> tuple[Transformer, Transformer]:
    return (Transformer.from_crs(config.WGS84, crs, always_xy=True),
            Transformer.from_crs(crs, config.WGS84, always_xy=True))


def use_metric_crs(crs: str) -> None:
    """Make ``crs`` the metric CRS for work on the current thread."""
    _ACTIVE_CRS.set(crs)


def active_metric_crs() -> str:
    """The metric CRS currently in force."""
    return _ACTIVE_CRS.get()


def latlon_to_xy(lat: float, lon: float, crs: str | None = None) -> tuple[float, float]:
    """(lat, lon) degrees -> (x, y) meters in the metric CRS."""
    to_metric, _ = _transformers(crs or _ACTIVE_CRS.get())
    x, y = to_metric.transform(lon, lat)
    return x, y


def geom_to_wgs(geom: BaseGeometry, crs: str | None = None) -> BaseGeometry:
    """Reproject a metric-CRS shapely geometry to WGS84 (lon/lat)."""
    _, to_wgs = _transformers(crs or _ACTIVE_CRS.get())
    return transform(to_wgs.transform, geom)


def xy_to_latlon(x: float, y: float, crs: str | None = None) -> tuple[float, float]:
    """(x, y) meters -> (lat, lon) degrees."""
    _, to_wgs = _transformers(crs or _ACTIVE_CRS.get())
    lon, lat = to_wgs.transform(x, y)
    return lat, lon
