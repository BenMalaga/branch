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
| Getting around | what is within a short walk (walkshed, with hills measured from USGS elevation), find the shadiest walk |
| Safety | what is too close to utility lines |
| Map data | OpenStreetMap features, real jurisdiction borders (county, city, borough, neighborhood), official US census geography (tracts, block groups, blocks, places) with GEOID, and the ArcGIS services your own town publishes |
| Shaping layers | everything within a distance, trim to an area, combine two layers, keep only what matches, count what is inside each area, where is it most concentrated, is this cluster real (Getis-Ord Gi\* with a false-discovery correction) |
| Hearings and notices | who has to be notified about an application (the abutter list), measured from the property line |
| Overlays | buildings in 3D, roads, water, green cover, land use, infrastructure, boundaries, addresses and places, streamed worldwide from Overture Maps, plus dated historical satellite imagery back to 2014 |

Each tool reprojects to the metric coordinate system local to the ground it is
measuring (its own UTM zone, so a length is a length anywhere on earth),
validates geometry, and returns its result together with a small recipe (the
tool plus the exact parameters it ran), so any answer can be reproduced.

A file that arrives in the wrong coordinate system is not drawn as if it were
right. Coordinates in the hundreds of thousands are recognised as a projected
grid, branch asks which one and suggests the systems actually used where you are
looking, and it checks the converted result lands there before accepting it.

Where a dataset does not reach, branch refuses instead of guessing. The street
tree census is a New York dataset, so asking for shade routing outside New York
returns an error naming the source and its extent, rather than an empty result
that would read as "no shade anywhere".

### Your town's own data

OpenStreetMap does not have parcels, zoning, or capital projects. US agencies do,
and most already publish them on ArcGIS REST servers that need no key.

- **Find data** searches the public ArcGIS Hub catalogue, then opens every result
  to check it. Published is not the same as open, so results say which layers
  actually read without a login, and which ones reach the area you are looking
  at. A layer called "Trenton Zoning" is often the Trenton in a different state,
  and branch says so rather than drawing it.
- **Bring in a layer** fetches one, counting first so it refuses rather than
  silently truncating, and converting the older Esri format for servers that do
  not speak GeoJSON.
- **Who has to be notified** turns a parcel layer and a subject property into the
  list a zoning board asks for: everyone within a set distance in feet, measured
  from the property line rather than the centre, with the applicant left out of
  their own list, ready to download as a spreadsheet. Confirm the radius against
  your own ordinance, because the rules differ by town.

### Counting things inside areas

"How many per neighbourhood" is the shape of most council maps, and it has two
quiet failure modes. branch keeps areas that contain nothing, at zero, because
dropping them makes a map that implies every neighbourhood has trees. And it
leaves the average empty rather than zero, because nothing there is not the same
as an average of nothing. Acres and per-acre rates are measured on the ground.

## Try it

**[planwithbranch.com](https://planwithbranch.com)** - nothing to install, no
account, no key. Open it and press "Try an example" to see it pull real
OpenStreetMap data and map it. The free services branch depends on do
occasionally go down; when that happens the example falls back to another one
and says which service was unavailable, rather than looking broken.

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
pytest              # 129 tests
./tests/web/run.sh  # 95 checks against the frontend, no browser needed
```

The frontend checks execute `web/index.html`'s real script under a small DOM and
MapLibre stub. Read `tests/web/README.md` before adding to them; the short
version is that most of these tests exist to catch answers that come back
looking right, so write the negative case too.

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
