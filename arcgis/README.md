# branch for ArcGIS Pro

`branch_toolbox.pyt` is an ArcGIS Pro **Python Toolbox** (arcpy). It runs the
branch shade pipeline for a study area and time, writes standard GIS files, and
loads them onto the active map. It requires **ArcGIS Pro 3.x** (arcpy ships only
inside Pro's conda Python), so it cannot run in a plain interpreter or in CI.

## Files it produces

The tool calls `branch.arcgis_export.export_all`, which writes to your chosen
output folder (all reprojected to WGS84 / EPSG:4326):

- `scored_segments.shp` + `.geojson` - every walk edge as a line, with
  `length_m`, `sun_frac`, and `shade_len`.
- `planting_gaps.shp` + `.geojson` + `.csv` - the top tree-planting sites as
  points (the CSV has a WKT geometry column plus lat/lon).
- `trees.geojson` - the street trees (dbh / species / health).
- `shade.geojson` - the dissolved tree-shadow footprint polygon.

You can also generate all of these outside ArcGIS with plain Python:

```python
from branch.config import AREAS
from branch import arcgis_export
arcgis_export.export_all(AREAS["park_slope"], out_dir="exports")
```

## Make the `branch` package importable from ArcGIS Pro

Pick one:

- **pip install (recommended):** open the Python Command Prompt that ships with
  ArcGIS Pro and run `pip install -e .` from the repo root, or
- **point the tool at the checkout:** leave the package uninstalled and fill in
  the optional **branch repo folder** parameter with the path to this repo; the
  tool prepends it to `sys.path` before importing.

## Add the toolbox to ArcGIS Pro

1. Open your project in ArcGIS Pro 3.x.
2. In the **Catalog** pane, right-click **Toolboxes** and choose
   **Add Toolbox**.
3. Browse to `arcgis/branch_toolbox.pyt` and add it.
4. Expand **branch** and double-click **branch Shade Analysis**.

## Run the tool

Fill in the parameters and click **Run**:

- **Study area** - `park_slope`, `upper_west_side`, or `forest_hills`.
- **Datetime (local)** - e.g. `2026-07-15 15:00`.
- **Shade aversion (alpha)** - default `4.0`.
- **Output folder** - where the shapefiles / GeoJSON / CSV are written.
- **branch repo folder** - optional; set it only if the package is not
  pip-installed into the Pro environment.

When it finishes, `scored_segments` and `planting_gaps` are added to the active
map. Symbolize `scored_segments` on `sun_frac` (0 = fully shaded, 1 = fully
exposed) to see the shade network, and graduate `planting_gaps` on `score` to
highlight the best places to plant.
