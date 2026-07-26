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

Write the negative test too. A check that only proves "it finds the thing"
passes on code that claims everything is a finding.
