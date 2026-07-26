// Design rules that can be checked without a browser. These exist because the
// visual bugs that actually shipped here were all rule violations, not layout
// subtleties: a hard-coded hex, a component style scoped to one parent, a
// library default left unthemed, and a URL clipped at a panel edge.
import fs from "fs";
const html = fs.readFileSync(process.argv[2] || "web/index.html", "utf8");
const css = /<style>([\s\S]*?)<\/style>/.exec(html)[1];
const root = /:root\{([\s\S]*?)\}/.exec(css)[1];
const body = css.replace(root, "");
let fail = 0;
const ok = (n, c, detail) => {
  console.log((c ? "PASS  " : "FAIL  ") + n + (c || !detail ? "" : "\n        " + detail));
  if (!c) fail = 1;
};

// every colour lives in :root
const strays = [...body.matchAll(/^.*#[0-9a-fA-F]{6}\b.*$/gm)].map(m => m[0].trim());
ok("no hard-coded hex outside :root", strays.length === 0, strays.slice(0, 3).join(" | "));

// the founder rules
ok("no em-dashes anywhere in the page", !html.includes("\u2014"));
ok("no emojis anywhere in the page", !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(html));

// text that cannot break must still wrap, or provenance gets clipped away
ok(".sub wraps unbreakable strings", /\.sub\{[^}]*overflow-wrap:\s*anywhere/.test(css));

// MapLibre ships light controls; on a dark map each is a hole in the picture
for (const ctrl of ["ctrl-group", "ctrl-scale", "ctrl-attrib"]) {
  ok(`maplibregl ${ctrl} is themed`, new RegExp(`\\.maplibregl-${ctrl}\\{`).test(css));
}
ok("attribution text is readable, not just its links",
   /\.maplibregl-ctrl-attrib\{[^}]*color:/.test(css));

// nothing in a panel scrolls sideways
ok("no panel declares overflow-x:scroll", !/overflow-x:\s*scroll/.test(css));

// focus is never removed without a replacement
const removed = (css.match(/outline:\s*(none|0)/g) || []).length;
const given = (css.match(/:focus-visible[^{]*\{[^}]*outline:/g) || []).length;
ok("focus rings are replaced wherever they are removed", given >= removed,
   `removed ${removed}, focus-visible rules ${given}`);

// one shared component for label/value rows, not per-parent copies
ok(".kv is defined once as a top-level component",
   /(^|\n)\s*\.kv\{/.test(css));

process.exit(fail);
