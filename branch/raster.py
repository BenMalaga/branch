"""Continuous heat-exposure hazard surface as a georeferenced GeoTIFF.

branch models sun exposure per street segment. This module turns those
discrete, line-based measurements into a continuous raster "hazard surface" over
the whole neighborhood, the way a remote-sensing Land-Surface-Temperature (LST)
product would look:

1. Sample each annotated edge at its midpoint to get a (x, y, sun_fraction)
   observation in the metric CRS (EPSG:32618, meters).
2. Interpolate those scattered observations onto a regular grid with inverse
   distance weighting (IDW), implemented in plain numpy.
3. Write the grid as a georeferenced ``float32`` GeoTIFF via rasterio (correct
   CRS, an affine transform from the grid origin + resolution, and a nodata
   value for cells with no nearby street).
4. Write a small warm-colormap PNG preview.

The output is a drop-in stand-in for a real thermal raster: the rest of a heat
pipeline (zonal stats, map overlays, routing costs) does not care whether the
per-cell value came from modeled shade or a measured satellite scene. See the
"Swapping in a REAL remote-sensing raster" note at the bottom of this file for
how to ingest Landsat/NLCD LST GeoTIFFs instead.
"""
from __future__ import annotations

import math
import os

import numpy as np
import rasterio
from rasterio.transform import from_origin

from . import config, geoutil, pipeline
from .config import Area


def _edge_samples(G) -> tuple[np.ndarray, np.ndarray]:
    """Sample every edge midpoint from the annotated graph.

    Returns ``(pts, vals)`` where ``pts`` is an (N, 2) array of metric-CRS
    (x, y) coordinates and ``vals`` is the (N,) array of ``sun_frac`` in [0, 1]
    (1 = full sun / high heat exposure, 0 = fully shaded).
    """
    xs, ys, vals = [], [], []
    for _, _, data in G.edges(data=True):
        mid = data["geometry"].interpolate(0.5, normalized=True)
        xs.append(mid.x)
        ys.append(mid.y)
        vals.append(data.get("sun_frac", 1.0))
    return np.column_stack([xs, ys]), np.asarray(vals, dtype="float64")


def _bbox_metric(area: Area) -> tuple[float, float, float, float]:
    """Axis-aligned metric bounds (x_min, y_min, x_max, y_max) of the bbox.

    The WGS84 bbox is not a perfect rectangle once projected to UTM, so we
    transform all four corners and take their bounding box.
    """
    w, s, e, n = area.bbox
    corners = [geoutil.latlon_to_xy(lat, lon)
               for lat, lon in ((s, w), (s, e), (n, w), (n, e))]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _idw_grid(pts: np.ndarray, vals: np.ndarray,
              x_min: float, y_max: float, ncols: int, nrows: int,
              resolution_m: float, nodata: float,
              power: float = 2.0, max_search_m: float = 150.0,
              chunk: int = 2048) -> np.ndarray:
    """Interpolate scattered (pts, vals) onto a north-up grid via IDW.

    Grid cell centers are at ``x_min + (col + 0.5) * res`` and
    ``y_max - (row + 0.5) * res`` so row 0 is the northern edge (matching the
    rasterio north-up affine transform). A cell whose nearest observation is
    farther than ``max_search_m`` is left as ``nodata`` (e.g. park interiors and
    other street-free areas), which keeps the surface honest instead of
    extrapolating shade across gaps. The grid is processed in row-flattened
    chunks so the (cells x points) distance matrix never blows up memory.
    """
    col_centers = x_min + (np.arange(ncols) + 0.5) * resolution_m
    row_centers = y_max - (np.arange(nrows) + 0.5) * resolution_m
    gx, gy = np.meshgrid(col_centers, row_centers)
    flat = np.column_stack([gx.ravel(), gy.ravel()])  # (M, 2)

    out = np.full(flat.shape[0], nodata, dtype="float64")
    eps = 1e-9  # avoids divide-by-zero when a cell coincides with an observation
    px, py = pts[:, 0], pts[:, 1]

    for start in range(0, flat.shape[0], chunk):
        block = flat[start:start + chunk]
        d2 = (block[:, 0:1] - px) ** 2 + (block[:, 1:2] - py) ** 2  # (b, N)
        nearest = np.sqrt(d2.min(axis=1))
        weights = 1.0 / (d2 ** (power / 2.0) + eps)
        est = (weights * vals).sum(axis=1) / weights.sum(axis=1)
        est[nearest > max_search_m] = nodata
        out[start:start + chunk] = est

    return out.reshape(nrows, ncols)


def _write_preview_png(grid: np.ndarray, bounds: tuple[float, float, float, float],
                       out_png: str, area: Area, when_local: str,
                       nodata: float) -> None:
    """Render a small warm-colormap PNG of the hazard surface."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x_min, y_min, x_max, y_max = bounds
    masked = np.ma.masked_invalid(grid) if math.isnan(nodata) \
        else np.ma.masked_equal(grid, nodata)

    fig, ax = plt.subplots(figsize=(7, 8), dpi=130)
    cmap = plt.get_cmap("inferno").copy()
    cmap.set_bad("#dfe3e6")  # street-free / nodata cells render as light gray
    im = ax.imshow(masked, extent=(x_min, x_max, y_min, y_max), origin="upper",
                   cmap=cmap, vmin=0.0, vmax=1.0, interpolation="bilinear")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Heat exposure (sun fraction, 1 = full sun)", fontsize=9)
    ax.set_title(f"branch heat exposure: {area.name}"
                 + (f"  ({when_local})" if when_local else ""),
                 fontsize=12, fontweight="bold")
    ax.set_xlabel("Easting (m, EPSG:32618)", fontsize=8)
    ax.set_ylabel("Northing (m, EPSG:32618)", fontsize=8)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)


def export_heat_raster(area: Area, when_local: str = config.DEFAULT_DATETIME_LOCAL,
                       alpha: float = config.DEFAULT_ALPHA,
                       out_tif: str = "exports/heat_exposure.tif",
                       resolution_m: float = 20.0,
                       max_search_m: float = 150.0,
                       tz: str = config.TIMEZONE,
                       data_dir: str = config.DATA_DIR) -> str:
    """Build and write a heat-exposure GeoTIFF for ``area`` and return its path.

    Runs the branch pipeline, interpolates per-edge sun exposure onto a
    ``resolution_m`` grid in EPSG:32618 with IDW, and writes a georeferenced
    ``float32`` GeoTIFF (nodata = NaN) plus a PNG preview alongside it (same stem
    with a ``.png`` suffix).

    Args:
        area: study area to model.
        when_local: local wall-clock time to model shade for.
        alpha: shade-aversion knob passed through to the pipeline (does not
            affect the raster values, only the routes analyze() also computes).
        out_tif: output GeoTIFF path (parent dirs are created).
        resolution_m: grid cell size in meters.
        max_search_m: cells with no observation within this radius stay nodata.
        tz: IANA timezone of ``when_local``.
        data_dir: cache directory for the graph/tree data.
    """
    out = pipeline.analyze(area, when_local, alpha,
                           from_latlon=area.demo_from, to_latlon=area.demo_to,
                           tz=tz, data_dir=data_dir)
    pts, vals = _edge_samples(out["graph"])

    x_min, y_min, x_max, y_max = _bbox_metric(area)
    ncols = max(1, int(math.ceil((x_max - x_min) / resolution_m)))
    nrows = max(1, int(math.ceil((y_max - y_min) / resolution_m)))
    # Snap the top-left origin so the grid fully covers the bbox; the transform
    # maps (col, row) -> (x, y) with rows increasing southward (north-up raster).
    transform = from_origin(x_min, y_max, resolution_m, resolution_m)

    nodata = float("nan")
    grid = _idw_grid(pts, vals, x_min, y_max, ncols, nrows, resolution_m,
                     nodata, max_search_m=max_search_m).astype("float32")

    os.makedirs(os.path.dirname(out_tif) or ".", exist_ok=True)
    with rasterio.open(
        out_tif, "w", driver="GTiff",
        height=nrows, width=ncols, count=1, dtype="float32",
        crs=config.METRIC_CRS, transform=transform, nodata=nodata,
        compress="deflate",
    ) as dst:
        dst.write(grid, 1)
        dst.set_band_description(1, "heat_exposure_sun_fraction")
        dst.update_tags(1, when_local=when_local, area=area.key,
                        source="modeled_tree_shade_sun_fraction")

    out_png = os.path.splitext(out_tif)[0] + ".png"
    _write_preview_png(grid, (x_min, y_min, x_max, y_max), out_png, area,
                       when_local, nodata)
    return out_tif


# --- Swapping in a REAL remote-sensing raster --------------------------------
# The surface above is *modeled* from tree-shadow sun fraction. To drive
# branch from a *measured* Land-Surface-Temperature (LST) product instead
# (e.g. Landsat 8/9 Collection 2 Level-2 band ST_B10, ECOSTRESS, or a summer
# thermal composite), the only thing that changes is the per-cell/per-edge value
# source; the grid, GeoTIFF write, and PNG preview are identical. Sketch:
#
#     import rasterio
#     from rasterio.warp import calculate_default_transform, reproject, Resampling
#     from rasterio.mask import mask
#     from shapely.geometry import box, mapping
#
#     with rasterio.open("LC09_L2SP_..._ST_B10.TIF") as src:
#         # 1. Clip the scene to our study bbox (in the scene's own CRS).
#         w, s, e, n = area.bbox  # reproject to src.crs first if needed
#         clipped, clip_tf = mask(src, [mapping(box(w, s, e, n))], crop=True)
#         # 2. Scale the raw DN to physical units (Landsat C2L2 surface temp):
#         lst_kelvin = clipped[0].astype("float32") * 0.00341802 + 149.0
#         lst_celsius = lst_kelvin - 273.15
#         # 3. Reproject to EPSG:32618 with reproject(..., Resampling.bilinear)
#         #    so it lines up with the walk graph and the grid above.
#
# To attach measured temperature to each street edge (zonal stats), either
# sample the raster at edge midpoints with ``src.sample([(x, y), ...])``, or
# buffer each edge and take the mean with the ``rasterstats.zonal_stats`` helper:
#
#     from rasterstats import zonal_stats
#     edge_buffers = [d["geometry"].buffer(10.0) for _, _, d in G.edges(data=True)]
#     stats = zonal_stats(edge_buffers, "lst_utm18n.tif", stats=["mean"])
#     # then set data["heat"] = stats[i]["mean"] and route on it like sun_frac.
