"""Elevation, so a walk uphill costs what it actually costs.

A walkshed drawn on flat-earth assumptions is quietly wrong in any city with
hills: it claims a quarter mile of San Francisco stairs is the same five minutes
as a quarter mile of flat Brooklyn. The error is invisible, because the output
looks exactly as confident either way.

Elevation comes from USGS 3DEP, which is free, key-free and covers the United
States. Outside that coverage the flat assumption is kept, but it is recorded in
the recipe rather than passed off as terrain-aware.
"""
from __future__ import annotations

import io
import math

import numpy as np
import requests

DEM_ENDPOINT = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
                "3DEPElevation/ImageServer/exportImage")

# Tobler's hiking function peaks slightly downhill, at a slope of -0.05, which is
# why the offset is there. Normalising by that peak lets a caller keep saying
# "4.8 km/h" and have it mean their speed on the flat.
_TOBLER_FLAT = math.exp(-3.5 * abs(0.0 + 0.05))


def tobler_speed(slope: float, flat_kmh: float) -> float:
    """Walking speed on a given slope (rise over run), in km/h."""
    return flat_kmh * math.exp(-3.5 * abs(slope + 0.05)) / _TOBLER_FLAT


def fetch_dem(bbox: tuple[float, float, float, float], size: int = 384):
    """A small elevation raster for ``bbox``, or None where 3DEP has no data."""
    w, s, e, n = bbox
    try:
        r = requests.get(DEM_ENDPOINT, params={
            "bbox": f"{w},{s},{e},{n}", "bboxSR": 4326, "imageSR": 4326,
            "size": f"{size},{size}", "format": "tiff", "pixelType": "F32",
            "noData": -9999, "interpolation": "RSP_BilinearInterpolation",
            "f": "image"}, timeout=45)
        r.raise_for_status()
        if not r.content[:2] in (b"II", b"MM"):
            return None                      # an error page, not a GeoTIFF
        import rasterio
        with rasterio.open(io.BytesIO(r.content)) as src:
            band = src.read(1).astype("float64")
            transform = src.transform
        if not np.isfinite(band).any() or (band <= -9990).all():
            return None
        band[band <= -9990] = np.nan
        return {"values": band, "transform": transform, "bbox": bbox}
    except Exception:
        return None                          # terrain is an improvement, not a gate


def sample(dem: dict, lonlats) -> np.ndarray:
    """Elevation at each (lon, lat), NaN where the raster has no value."""
    if dem is None:
        return np.full(len(lonlats), np.nan)
    band = dem["values"]
    inv = ~dem["transform"]
    h, w = band.shape
    out = np.full(len(lonlats), np.nan)
    for i, (lon, lat) in enumerate(lonlats):
        col, row = inv * (lon, lat)
        c, r = int(col), int(row)
        if 0 <= r < h and 0 <= c < w:
            out[i] = band[r, c]
    return out


def edge_slope(z_from: float, z_to: float, length_m: float) -> float:
    """Rise over run along an edge, treating unknown elevation as flat."""
    if not length_m or not np.isfinite(z_from) or not np.isfinite(z_to):
        return 0.0
    return (z_to - z_from) / length_m


def grade_label(slope: float) -> str:
    """How a US accessibility reviewer would describe this grade.

    The thresholds are the ones written into practice: 5 percent is where a
    running slope stops being a plain walkway, and 8.33 percent (1:12) is the
    steepest a ramp may be.
    """
    g = abs(slope)
    if g >= 0.0833:
        return "steeper than a ramp may be (over 8.33%)"
    if g >= 0.05:
        return "steep enough to count as a ramp (5% to 8.33%)"
    return "within a level walkway (under 5%)"
