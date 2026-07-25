# branch

![branch](web/og.png)

Live at [planwithbranch.com](https://planwithbranch.com).

Free, open-source city-planning analysis that runs in your browser. Ask a
planning question in plain English, or click a tool, and branch computes the
answer from free public data and draws it on the map, along with the exact
inputs it used.

branch reads the same open data a GIS already uses (OpenStreetMap and public
government sources), so it works alongside ArcGIS or QGIS instead of replacing
them.

## What it does

A MapLibre map with street, satellite, and dark basemaps, address search,
drag-and-drop of your own GeoJSON or CSV (which stays in your browser), draw
tools, a layers panel, and a set of analysis tools. Every tool is both a button
and a function the optional assistant can call.

| Category | Tools |
|---|---|
| Money | what will this cost to build, which land pays for itself (value per acre), is it worth it (payback and ROI) |
| Getting around | what is within a short walk (walkshed), find the shadiest walk |
| Safety | what is too close to utility lines |
| Map data | OpenStreetMap features, real jurisdiction borders (county, city, borough, neighborhood) |
| Shaping layers | everything within a distance, trim to an area, combine two layers, keep only what matches, where is it most concentrated, is this cluster real (Getis-Ord Gi\* with a false-discovery correction) |
| Overlays | buildings in 3D, roads, water, green cover, land use, infrastructure, boundaries, addresses and places, streamed worldwide from Overture Maps, plus dated historical satellite imagery back to 2014 |

Each tool reprojects to the metric coordinate system local to the ground it is
measuring (its own UTM zone, so a length is a length anywhere on earth),
validates geometry, and returns its result together with a small recipe (the
tool plus the exact parameters it ran), so any answer can be reproduced.

Where a dataset does not reach, branch refuses instead of guessing. The street
tree census is a New York dataset, so asking for shade routing outside New York
returns an error naming the source and its extent, rather than an empty result
that would read as "no shade anywhere".

## Try it

**[planwithbranch.com](https://planwithbranch.com)** - nothing to install, no
account, no key. Open it and press "Try an example" to see it pull real
OpenStreetMap data and map it.

It works on any area with OpenStreetMap coverage. Address search uses
OpenStreetMap Nominatim, and the fiscal tools ship with editable US public
unit-cost defaults.

## Run your own copy

You only need this if you want to host it yourself, work offline, or develop on
it. Requires Python 3.11 or newer.

```bash
pip install -e ".[full]"
branch serve
```

That serves the same app at `http://localhost:8000`. A `Dockerfile` and a
`render.yaml` are included if you would rather deploy it somewhere.

## The assistant is optional

The "Ask the map" bar is a conversation that calls the same tools listed above,
chains them, and keeps context between turns. You can drop a GeoJSON or CSV
straight into the chat. Bring your own model key (Anthropic or OpenAI), kept only
in your browser and never stored. Without a key every tool still works as a
button, so the assistant is a convenience, not a gate. Ollama is supported when
you run branch on your own machine, where branch can reach it.

Every figure in an answer is traced back to the tool run that produced it, by
plain arithmetic rather than a second model. Anything matching no tool output is
flagged in the answer itself, so a number nobody computed cannot pass quietly.
The check needs no API key, so it holds for a small local model too.

## Use it beside ArcGIS

`arcgis/branch_toolbox.pyt` is an ArcGIS Pro Python Toolbox that runs the
shade-routing pipeline and writes standard GIS files (Shapefile, GeoJSON, CSV)
onto the active map. See [`arcgis/README.md`](arcgis/README.md).

## Development

```bash
pip install -e ".[dev]"
pytest
```

The core router needs only the base dependencies; the analytics modules (ArcGIS
export, PostGIS, raster, machine learning) use the `[full]` extra. A `Dockerfile`
and `fly.toml` are included for container deploys, and `docker-compose.yml`
brings up PostGIS for the spatial-SQL layer. The Python geospatial backend needs
a container host rather than a serverless one.

## Tech

MapLibre GL JS, Flask, GeoPandas / Shapely / rasterio, osmnx / networkx,
scikit-learn, and PostGIS, over free public data (OpenStreetMap and public
government sources).

## License

MIT. Data from OpenStreetMap (ODbL) and public government sources. Not
affiliated with Esri or any city.
