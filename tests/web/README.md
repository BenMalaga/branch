# Frontend checks

These execute `web/index.html`'s real script under a DOM and MapLibre stub, in
Node. No browser, no build step.

```bash
./tests/web/run.sh
```

Why this exists: in an agent session the browser pane is often hidden, and a
hidden pane never composites a frame, so MapLibre never finishes loading. The
canvas screenshots black and the whole app looks broken when it is fine. Chasing
that ghost wastes hours. Running the shipped code directly does not lie.

| File | What it protects |
|---|---|
| `shell.js` | the assistant answers, shows its steps, and can arrange the workspace |
| `lineage.js` | a layer remembers what made it, and survives the panel being reordered |
| `replay.js` | a shared link re-runs the tools rather than restoring a snapshot |
| `interrogate.js` | geodesic area and length, overlay popups, the wrong-EPSG guard |
| `table.js` | selecting a table row highlights the right feature, even when sorted |
| `connector.js` | the ArcGIS form, and checking a layer before downloading it |
| `notice.js` | the abutter list renders and exports with correct CSV quoting |
| `finder.js` | search results are labelled honestly and land as a real layer |
| `example.js` | the first thing a visitor clicks survives a public service being down |
| `shade.js` | the colour means a measure, never an id or the source's bookkeeping |
| `style.mjs` | design rules that need no browser: tokens, wrapping, themed controls |

`style.mjs` is not a unit test, it is the design system as an assertion. Every
rule in it exists because that exact violation shipped: a hard-coded hex, a
component style scoped to one parent, a MapLibre control left at its light
default on a dark map, and a URL clipped at a panel edge in the one panel whose
whole job is showing provenance.

## About the stub

`stub.js` has a deliberately small selector engine: `querySelectorAll` scans the
element's own HTML for `[data-x]` and `.cls` and returns stub elements carrying
that dataset. Matches are **cached per element and selector**, so wiring a
handler and then clicking touch the same object. Before that caching existed,
every "the button is wired" test passed vacuously.

A suite that throws now prints `CRASH` and fails the run. It used to report the
passes it managed before the throw and exit 0, which is the same silent-success
bug this project exists to avoid, aimed at its own tooling.

When a check fails, **first ask whether the stub is lying.** Real examples:
spreading a class instance (`map={...map}`) strips its prototype methods, and a
`LngLatBounds` whose `isEmpty()` returned a hardcoded `true` made every `fitTo`
assertion vacuous.

Write the negative test too. A check that only proves "it finds the thing"
passes on code that claims everything is a finding.
