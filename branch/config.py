"""Configuration: study areas, coordinate systems, and model defaults.

Everything a user might want to retarget lives here. Point branch at a new
neighborhood by adding an ``Area`` to ``AREAS`` (a bbox is all you need); the
rest of the pipeline is area-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Coordinate reference systems -------------------------------------------
# Inputs (OSM, tree census) are WGS84 lon/lat. All geometry math (buffers,
# shadow offsets, intersection lengths) happens in a *metric* projection so
# distances are in real meters, then results are reprojected back to WGS84 for
# web maps. UTM zone 18N covers the entire NYC metro area.
WGS84 = "EPSG:4326"
# Fallback only. Every Area derives its own UTM zone (see Area.metric_crs);
# projecting the whole world through one zone silently inflates distances and
# areas the further you get from its central meridian (about +21% on a length
# and +48% on an area by the time you reach Los Angeles).
METRIC_CRS = "EPSG:32618"  # UTM 18N, units = meters


@dataclass(frozen=True)
class Area:
    """A named study area: a bounding box plus a demo origin/destination."""
    key: str
    name: str
    bbox: tuple[float, float, float, float]  # (west, south, east, north), WGS84 deg
    demo_from: tuple[float, float]           # (lat, lon)
    demo_to: tuple[float, float]             # (lat, lon)

    @property
    def center(self) -> tuple[float, float]:
        w, s, e, n = self.bbox
        return ((s + n) / 2.0, (w + e) / 2.0)  # (lat, lon)

    @property
    def metric_crs(self) -> str:
        """The UTM zone this area actually sits in, as an EPSG code.

        Distances and areas are only trustworthy in a projection local to the
        ground being measured, so each area picks its own zone rather than
        inheriting one. 326xx is northern hemisphere, 327xx southern.
        """
        lat, lon = self.center
        zone = int((lon + 180.0) / 6.0) + 1
        zone = min(max(zone, 1), 60)
        return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


# Study areas. Park Slope is the default demo: a dense, tree-lined walkable
# grid next to Prospect Park, so shade-vs-speed tradeoffs are vivid. The others
# prove the pipeline is city-agnostic.
AREAS: dict[str, Area] = {
    "park_slope": Area(
        key="park_slope",
        name="Park Slope, Brooklyn",
        bbox=(-73.9900, 40.6600, -73.9700, 40.6800),
        demo_from=(40.6772, -73.9735),   # near Grand Army Plaza
        demo_to=(40.6675, -73.9855),     # near 4th Ave & 9th St
    ),
    "upper_west_side": Area(
        key="upper_west_side",
        name="Upper West Side, Manhattan",
        bbox=(-73.9900, 40.7800, -73.9650, 40.8000),
        demo_from=(40.7960, -73.9720),
        demo_to=(40.7830, -73.9840),
    ),
    "forest_hills": Area(
        key="forest_hills",
        name="Forest Hills, Queens",
        bbox=(-73.8550, 40.7150, -73.8300, 40.7300),
        demo_from=(40.7270, -73.8460),
        demo_to=(40.7185, -73.8360),
    ),
}

DEFAULT_AREA = "park_slope"

# --- Public data sources (all free, no API key required) --------------------
# NYC 2015 Street Tree Census (Socrata Open Data API). ~683k trees.
TREES_ENDPOINT = "https://data.cityofnewyork.us/resource/uvpi-gqnh.json"

# OSM street network is pulled by osmnx via the public Overpass API.
NETWORK_TYPE = "walk"

# --- Model defaults ----------------------------------------------------------
# Local wall-clock time to model shade for, and the IANA timezone of the areas.
DEFAULT_DATETIME_LOCAL = "2026-07-15 15:00"  # a hot summer afternoon
TIMEZONE = "America/New_York"

# Shade aversion (alpha): how strongly the "coolest" route avoids sun. The cost
# of a segment is length * (1 + alpha * sun_fraction). alpha=0 reproduces the
# shortest path; higher alpha accepts longer detours to stay in shade.
DEFAULT_ALPHA = 4.0

# Cap on a single tree's shadow length (meters). At very low sun angles the
# geometric shadow length diverges; real shadows are broken up by buildings and
# terrain long before that, so we clamp.
MAX_SHADOW_M = 60.0

# Cache directory for downloaded OSM graphs and tree data.
DATA_DIR = "data"
