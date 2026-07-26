# branch - guide for any agent picking this up cold

Free, open-source city-planning analysis that runs in a browser. Live at
**https://planwithbranch.com**. Public repo: **github.com/BenMalaga/branch** (MIT).

**Arm:** new harmonics forge (arm 4, all software). **Stage:** live (alpha), verified
2026-07-25 (planwithbranch.com returns 200, repo returns 200). Renamed from coolwalk;
CoolWalk survives as tool #1 (the shade-routing engine), not a separate product.

Read this file first, then `docs/VERIFICATION.md` (what the product claims and
why), then `branch/registry.py` (every capability lives there).

---

## The one thing that matters

**A confident wrong number is the only failure mode that reaches production.**

This product's entire pitch is verified, auditable analysis. A crash gets fixed;
a plausible wrong answer gets used in a hearing. Bugs of exactly this shape have
already shipped and been fixed here:

| Bug | What it did | Fix |
|---|---|---|
| Hardcoded `METRIC_CRS = EPSG:32618` | Every area on earth measured in New York's UTM zone. **+47.6% area error in Los Angeles, +56.2% in London.** | `Area.metric_crs` derives the local UTM zone (`config.py`) |
| NYC tree census queried anywhere | Outside New York it returned zero trees, `shade.py` read that as "no shade anywhere", and CoolWalk returned a confident route built on nothing | `sources.require()` refuses outside a source's real extent |
| CSV coordinates stripped then parsed | `"notalat"` became `0`, silently placing rows at 0,0 in the Gulf of Guinea | `asCoord()` in `web/index.html` requires an actual number |
| An Esri extent compared without converting | A service published in Web Mercator or State Plane would pass or fail every coverage check, since metres were compared against degrees | `esri._wgs84_extent()` converts first, and returns `None` rather than guessing at an unknown projection |
| The web test harness exiting 0 on a crash | A suite that threw reported the passes it managed first, so wiring tests passed vacuously | `tests/web/run.sh` prints `CRASH` and fails; stub selector matches are cached so wiring and clicking touch one object |

The habit that catches these: **test both directions.** A test that only checks
"it finds the cluster" passes on a tool that calls everything significant. A
check that only catches out-of-range coordinates misses a wrong EPSG that lands
in the ocean. Write the negative test too.

## Non-negotiable constraints

- **Zero paid APIs or paid data.** Free, key-free public sources only. A user's
  own AI key is optional, BYO, browser-only, and the product must be fully usable
  without it. This is a founder rule, not a preference.
- **Render free tier**: ~512MB RAM, ephemeral disk (caches vanish on redeploy or
  sleep), cold starts, no persistent DB, no workers, no websockets.
- **The frontend is ONE vanilla-JS file**, no framework and no build step.
  MapLibre GL is the only heavy dependency. Keep it that way. `web/index.html`
  is the product. `web/classic.html` is the previous shell, kept at
  `/classic.html` as a fallback while the redesign settles; it is frozen, so do
  not add features to it.
- **No em-dashes and no emojis anywhere** (founder-locked, applies to code
  comments, copy, and commit messages).
- **Never invent data.** If a source lacks a field or does not cover the ground,
  say so and name what is missing. Refusing is a feature.

## Layout

```
branch/
  registry.py    EVERY tool (21). Typed JSON-Schema contract + run(). Start here.
  esri.py        the ArcGIS connector: URL safety, extents, Esri JSON, Hub search
  server.py      Flask: /api/tools, /api/tools/<id>/run, /api/agent, /api/geocode, /api/boundary
  agent.py       tool-calling loop, conversation history, BYO key
  receipts.py    traces every number in an answer back to a tool run
  stats.py       Getis-Ord Gi* hot spots, Benjamini-Hochberg correction
  terrain.py     USGS 3DEP elevation, Tobler hiking function
  sources.py     each dataset's REAL extent; require() refuses outside it
  data.py        OSM download, disk cache, in-memory prepared-graph cache
  geoutil.py     CRS transforms, per-request metric CRS via ContextVar
  config.py      Area, and Area.metric_crs (the local UTM zone)
  shade/solar/routing/pipeline.py   the CoolWalk shade-routing engine
  raster/ml/hazard/sqldb/export/arcgis_export.py   analytics + interop
web/index.html   the whole frontend
web/classic.html the previous shell, frozen, served at /classic.html
tests/           pytest, 65 tests
tests/web/       106 frontend checks, run with ./tests/web/run.sh
```

## How to add a tool

One `register(Tool(...))` in `registry.py` is the whole job. The UI is
registry-driven, so a new tool appears in the Tools panel and becomes callable by
the agent with **no frontend work**.

```python
register(Tool(
    id="thing",                      # stable: share links and recipes depend on it
    title="What does this do?",      # plain English, phrased as the user's question
    noun="Thing",                    # short label for the layer it creates
    category="money",                # money | getting around | map data | shaping layers | safety
    returns="layer",                 # layer | value
    description="Plain English, read by BOTH the user and the model. "
                "End with 'Also called a <GIS term>.' so professionals and the "
                "model can still find it by its technical name.",
    params={"type": "object", "required": ["layer"], "properties": {...}},
    run=_run_thing))
```

Every `run` returns `{"result": ..., "recipe": {...}}`. The recipe is provenance:
the tool id plus resolved params, enough to re-run the analysis exactly.

## Verification, when the browser will not cooperate

The Claude Code browser pane frequently goes hidden, and **a hidden pane never
composites frames, so MapLibre never finishes loading**: `isStyleLoaded()` stays
false, the canvas screenshots black, and everything looks broken when it is fine.
Do not chase that ghost. Instead:

```bash
# Execute the REAL frontend under a DOM/MapLibre stub and unit-test its logic.
node -e "require('/tmp/stub.js'); (0,eval)(require('fs').readFileSync('/tmp/app.js','utf8') + testCode)"
```

This is now a committed harness rather than something to rebuild each time:

```bash
./tests/web/run.sh        # 106 checks against web/index.html
```

That includes `style.mjs`, which asserts the design system itself (every colour
in `:root`, no em-dashes or emojis, unbreakable strings wrap, MapLibre's light
controls are themed). Each rule in it is there because that exact violation
shipped once.

Read `tests/web/README.md` before adding to it. The stub lives in
`tests/web/stub.js`; when a check fails, **first ask whether the stub is lying.**
Real examples of harness artifacts, not product bugs: `map={...map}` in a test
strips prototype methods off a class instance, and a `LngLatBounds` stub whose
`isEmpty()` returned a hardcoded `true` made every `fitTo` assertion silently
vacuous. Fix the stub, then re-run, before touching product code.

This approach caught bugs the browser never showed: layer lineage corrupted by
reordering, silently swallowed overlay failures, and an empty tools panel. Pair it
with `curl` against the API (proves the backend) and the server access log (proves
a click actually fired). Always run `node --check` on the extracted script.

## Deploying

Push to `main` on the public repo and **Render redeploys automatically** (~60-100s).
There is no separate deploy step.

**The repo is a curated mirror, not the working tree.** Work happens in this
folder; the public repo lives in the session scratchpad and deliberately excludes
strategy docs, research notes, and generated data. Copy changed files across, then
commit there. Founder rule: the public repo is "just the website and code, nothing
that looks AI-made", and commits are authored as Ben with no AI co-author trailer.

Verify live with an actual behavioral check, not a string grep. `grep '"boundary"'`
on `/api/tools` gave a **false positive** once because "boundary" is also a
parameter name; test for the real tool id.

## Local data, which is where the real value is

OpenStreetMap does not have parcels, zoning, or capital projects. US agencies
publish those on ArcGIS REST servers, free and key-free, and three tools cover
that ground:

- `find_data` searches the ArcGIS Hub catalogue and then **probes every result**,
  because published does not mean open (many need a token) and a "Trenton" layer
  is often the Trenton in another state. Results are labelled with what was
  actually found, not what the catalogue claimed.
- `arcgis` fetches one layer. It counts before downloading, refuses outside the
  layer's real extent, and converts Esri JSON for servers older than 10.4.
- `notice_list` builds the abutter list a zoning hearing needs.

`esri.py` fetches a **user-supplied URL from the server**, which is a
request-forgery hole. It is closed by checking the host resolves to a public
address AND by not following redirects blindly. Residual risk, documented in the
module: DNS is resolved twice, so rebinding is not fully closed.

## Two invariants that are easy to break

**Lineage is by id, never by position.** A layer records what made it in
`L.src` (`{t:toolId, p:{param:{ref:layerId}}}`). The panel can be reordered by
the user, so anything that reads lineage must resolve refs by id and emit
dependencies before dependents (`shareState()` topologically sorts). A share
link stores positional `{$:index}` only after that sort. Getting this wrong
corrupts an analysis silently, which is the failure mode that matters here.

**An empty result is a claim, and usually a false one.** `summarize_within` keeps
areas that contain nothing, at zero, because dropping them makes a choropleth
that implies every neighbourhood has trees; and it leaves `average` null rather
than 0, because no trees means there is nothing to average. `_read_fc(fc, what)`
refuses an empty input by naming which one, before geopandas can fail with a
message about the `geometry=` keyword.

**A share link re-runs the analysis; it does not carry the answer.** `replayState()`
calls the tools again. That is the difference between "here is what I got" and
"here is something you can check", and it is the product thesis. A layer brought
in by hand has no recipe, so it is reported as skipped rather than faked.

## Performance, and where the time actually goes

- Preparing a street graph (parse graphml, reproject, rebuild edge geometry) cost
  **21s and was paid on every request**. Now cached in memory (`data.py`,
  bounded to 2 entries for the 512MB ceiling). Repeat walksheds: **1.5s**.
- The shared graph is **read-only**. Anything that writes per-request state onto
  edges must pass `copy=True` (shade does) or compute weights in a function
  (walkshed does). Poisoning it silently corrupts the next user's answer.
- First request in a brand-new area still pays the Overpass download (~40-100s on
  the free tier). That is inherent, and the free tier wipes the disk cache on
  sleep. An always-on instance with a persistent disk would remove it.

## Founder context

Ben is a developer, not a GIS professional, and is using this as his entry into
geospatial work. He wants planners to genuinely be able to use it. Recurring
direction, in his words: tools should not be "slow and kinda esoteric", the UI
should be friendly and responsive and not "look like AI made it", and users should
have "maximum freedom and control". He asks for brainstorming often; the
`Workflow` tool is authorized for that and has produced real findings, including
two of the correctness bugs above. Keep workflows lean (about 17 agents); a
46-agent run once hung for two hours.

He reviews the live site and reports real bugs. Take them seriously and reproduce
before theorizing.
