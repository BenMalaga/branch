"""Spatial machine learning on branch's real Park Slope data.

Three self-contained scikit-learn demos, each answering a question the core
shade engine raises:

1. ``shade_surrogate``  -- can we predict a street's sun exposure *without* a
   full tree census? A ``RandomForestRegressor`` learns per-edge ``sun_frac``
   from three cheap features that exist in any city (segment bearing, segment
   length, a coarse count of nearby trees), so the analysis can generalize to
   neighborhoods where NYC's tree inventory does not reach.
2. ``cluster_priorities`` -- ``KMeans`` groups planting-gap sites into a handful
   of geographic priority zones, so an urban-forestry team can plan block by
   block instead of segment by segment.
3. ``interpolate_heat`` -- inverse-distance-weighted (IDW) interpolation turns
   the per-edge sun samples into a continuous coarse heat surface over the
   bounding box. The raster module reuses this idea; here it stays pure numpy.

Run ``run_demo()`` to print the real metrics on the cached Park Slope data.
"""
from __future__ import annotations

import numpy as np
from shapely import STRtree
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

from . import data, geoutil, routing
from .config import AREAS, Area
from .pipeline import analyze

# Defaults for the standalone demos: a hot summer afternoon, default shade knob.
_DEFAULT_WHEN = "2026-07-15 15:00"
_DEFAULT_ALPHA = 4.0

# Radius (meters) around an edge midpoint used as the surrogate's canopy proxy.
# Using the tree inventory here is intentional: it stands in for the coarse
# canopy signal (e.g. a satellite greenness raster) a city without a street-tree
# census would supply instead.
SURROGATE_RADIUS_M = 30.0

# One seed so every reported number is reproducible run to run.
RANDOM_STATE = 42


# --- Shared setup ------------------------------------------------------------
def _default_analysis(area: Area | None = None) -> dict:
    """Run the full pipeline once for a demo (defaults to Park Slope)."""
    area = area or AREAS["park_slope"]
    return analyze(area, _DEFAULT_WHEN, alpha=_DEFAULT_ALPHA,
                   from_latlon=area.demo_from, to_latlon=area.demo_to)


def _edge_bearing_deg(geom) -> float:
    """Compass bearing (0 = N, 90 = E) from an edge's first to last vertex.

    Geometry is in the metric CRS, where x is easting and y is northing, so the
    bearing is measured clockwise from grid north (a good stand-in for true
    north over a single UTM zone).
    """
    (x0, y0), (x1, y1) = geom.coords[0], geom.coords[-1]
    return float(np.degrees(np.arctan2(x1 - x0, y1 - y0)) % 360.0)


def _edge_features(G, trees, radius_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Build the cheap surrogate feature matrix and ``sun_frac`` target.

    Features per edge (none of which need a per-street shade computation):
      0. compass bearing of the segment (degrees)
      1. segment length (meters)
      2. number of trees within ``radius_m`` of the segment midpoint

    An STRtree over the tree points keeps the neighborhood count near O(log n).
    """
    tree_index = STRtree(list(trees.geometry.values))

    rows: list[list[float]] = []
    target: list[float] = []
    for _, _, d in G.edges(data=True):
        geom = d["geometry"]
        mid = geom.interpolate(0.5, normalized=True)
        # dwithin returns the indices of tree points inside the proxy radius.
        near = tree_index.query(mid, predicate="dwithin", distance=radius_m)
        rows.append([_edge_bearing_deg(geom), d["length"], float(len(near))])
        target.append(d["sun_frac"])
    return np.asarray(rows, dtype=float), np.asarray(target, dtype=float)


# --- Demo 1: supervised shade surrogate --------------------------------------
def shade_surrogate(out: dict | None = None, *,
                    radius_m: float = SURROGATE_RADIUS_M,
                    test_size: float = 0.25) -> dict:
    """Predict per-edge ``sun_frac`` from features that need no tree census.

    Frames the question: in a city without NYC's street-tree inventory, can a
    model trained where we *do* have ground-truth shade predict exposure from
    cheap, universally available features? Reports real held-out R2 and MAE.
    """
    out = out or _default_analysis()
    X, y = _edge_features(out["graph"], out["trees"], radius_m)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=RANDOM_STATE)

    model = RandomForestRegressor(n_estimators=200, random_state=RANDOM_STATE,
                                  n_jobs=-1)
    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    names = ["bearing", "length", "tree_count"]
    importances = {n: round(float(v), 3)
                   for n, v in zip(names, model.feature_importances_)}
    return {
        "n_edges": int(len(y)),
        "n_train": int(len(y_train)),
        "n_test": int(len(y_test)),
        "radius_m": radius_m,
        "test_r2": float(r2_score(y_test, pred)),
        "test_mae": float(mean_absolute_error(y_test, pred)),
        "feature_importances": importances,
    }


# --- Demo 2: KMeans planting-priority zones -----------------------------------
def cluster_priorities(out: dict | None = None, *, k: int = 5,
                       top_n: int = 80) -> dict:
    """Group planting-gap sites into ``k`` geographic priority zones.

    Features are (lat, lon, sun_frac). Latitude/longitude span a fraction of a
    degree while sun_frac spans 0..1, so the features are standardized before
    KMeans; otherwise sun_frac would swamp geography. Reported means are in the
    original units. Returns cluster sizes and each cluster's mean sun exposure.
    """
    out = out or _default_analysis()
    area = out["area"]
    streets = data.get_named_streets(area)
    gaps = routing.plant_gaps(out["graph"], area.bbox, streets, top_n=top_n)

    feats = np.array([[g["lat"], g["lon"], g["sun_frac"]] for g in gaps],
                     dtype=float)
    # Standardize each column so degrees and sun_frac contribute comparably.
    mean, std = feats.mean(axis=0), feats.std(axis=0)
    std[std == 0.0] = 1.0
    scaled = (feats - mean) / std

    k = min(k, len(gaps))
    km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE)
    labels = km.fit_predict(scaled)

    clusters = []
    for c in range(k):
        member = labels == c
        clusters.append({
            "cluster": int(c),
            "n_sites": int(member.sum()),
            "mean_sun_frac": round(float(feats[member, 2].mean()), 3),
            "centroid_latlon": [round(float(feats[member, 0].mean()), 5),
                                round(float(feats[member, 1].mean()), 5)],
        })
    # Hottest zones first: those are where planting buys the most shade.
    clusters.sort(key=lambda c: c["mean_sun_frac"], reverse=True)
    return {"n_sites": int(len(gaps)), "k": int(k), "clusters": clusters}


# --- Demo 3: IDW heat-surface interpolation ----------------------------------
def interpolate_heat(out: dict | None = None, *, grid_size: int = 40,
                     power: float = 2.0) -> dict:
    """Interpolate per-edge sun exposure onto a coarse grid over the bbox (IDW).

    Samples each edge's sun_frac at its midpoint, then fills a ``grid_size`` x
    ``grid_size`` grid spanning the area's bounding box using inverse-distance
    weighting (weight = 1 / distance**power). Pure numpy so it is dependency
    free and portable to the raster module. Returns the grid plus its shape and
    value range.
    """
    out = out or _default_analysis()
    G = out["graph"]
    area = out["area"]

    # Sample points: edge midpoints (metric x, y) carrying their sun_frac.
    xs, ys, vals = [], [], []
    for _, _, d in G.edges(data=True):
        mid = d["geometry"].interpolate(0.5, normalized=True)
        xs.append(mid.x)
        ys.append(mid.y)
        vals.append(d["sun_frac"])
    sx = np.asarray(xs, dtype=float)
    sy = np.asarray(ys, dtype=float)
    sv = np.asarray(vals, dtype=float)

    # Build the target grid in metric coordinates from the WGS84 bbox corners.
    w, s, e, n = area.bbox
    x0, y0 = geoutil.latlon_to_xy(s, w)
    x1, y1 = geoutil.latlon_to_xy(n, e)
    grid_x = np.linspace(min(x0, x1), max(x0, x1), grid_size)
    grid_y = np.linspace(min(y0, y1), max(y0, y1), grid_size)
    mesh_x, mesh_y = np.meshgrid(grid_x, grid_y)

    # Pairwise distances (grid cells x samples), then IDW weights.
    dx = mesh_x.ravel()[:, None] - sx[None, :]
    dy = mesh_y.ravel()[:, None] - sy[None, :]
    dist = np.sqrt(dx * dx + dy * dy)
    dist = np.maximum(dist, 1e-6)  # guard against a coincident sample point
    weights = 1.0 / dist ** power
    grid = (weights * sv[None, :]).sum(axis=1) / weights.sum(axis=1)
    grid = grid.reshape(grid_size, grid_size)

    return {
        "grid": grid,
        "shape": tuple(grid.shape),
        "value_min": float(grid.min()),
        "value_max": float(grid.max()),
        "n_samples": int(len(sv)),
        "grid_extent_m": [float(grid_x[0]), float(grid_x[-1]),
                          float(grid_y[0]), float(grid_y[-1])],
    }


# --- Demo driver -------------------------------------------------------------
def run_demo(area: Area | None = None) -> None:
    """Run all three spatial-ML demos on real data and print the metrics."""
    area = area or AREAS["park_slope"]
    print(f"branch spatial ML demos  |  {area.name}")
    print(f"modeled for {_DEFAULT_WHEN} local, alpha={_DEFAULT_ALPHA}")
    print("=" * 64)

    out = _default_analysis(area)

    print("\n[1] Shade surrogate (RandomForestRegressor)")
    print("    target: per-edge sun_frac  |  features: bearing, length, "
          "nearby tree count")
    s = shade_surrogate(out)
    print(f"    edges: {s['n_edges']}  (train {s['n_train']} / "
          f"test {s['n_test']})")
    print(f"    canopy-proxy radius: {s['radius_m']:.0f} m")
    print(f"    test R2 : {s['test_r2']:.3f}")
    print(f"    test MAE: {s['test_mae']:.3f}  (sun_frac units, 0..1)")
    print(f"    feature importances: {s['feature_importances']}")

    print("\n[2] Planting-priority zones (KMeans, k=5)")
    print("    features: lat, lon, sun_frac (standardized)")
    c = cluster_priorities(out)
    print(f"    {c['n_sites']} planting-gap sites -> {c['k']} zones")
    for cl in c["clusters"]:
        print(f"    zone {cl['cluster']}: {cl['n_sites']:2d} sites  "
              f"mean sun_frac {cl['mean_sun_frac']:.3f}  "
              f"centroid {cl['centroid_latlon']}")

    print("\n[3] Heat surface (IDW interpolation, numpy)")
    h = interpolate_heat(out)
    print(f"    sampled {h['n_samples']} edge midpoints")
    print(f"    grid shape: {h['shape']}")
    print(f"    sun-exposure range: {h['value_min']:.3f} .. "
          f"{h['value_max']:.3f}")


if __name__ == "__main__":
    run_demo()
