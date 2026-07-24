"""Command-line interface.

Core (needs only the base install)::
    branch demo | route | gaps | export-web | build-cache

Analytics modules (need ``pip install -e .[full]`` and, for db, a PostGIS container)::
    branch export-arcgis | export-raster | db | ml | hazard
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import config, data, export, pipeline, routing, viz
from .config import AREAS


def _parse_latlon(s: str) -> tuple[float, float]:
    try:
        lat, lon = (float(x) for x in s.split(","))
        return lat, lon
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected 'lat,lon', got {s!r}")


def _area(key: str):
    if key not in AREAS:
        sys.exit(f"Unknown area {key!r}. Options: {', '.join(AREAS)}")
    return AREAS[key]


def _label(out: dict) -> str:
    return f"{out['when_local']} · sun {out['altitude']:.0f}° alt"


def _print_summary(out: dict) -> None:
    r = out["result"]
    f, c = r["fast"], r["cool"]
    detour = (c["length_m"] / f["length_m"] - 1.0) * 100.0 if f["length_m"] else 0.0
    print(f"\n  Sun altitude {out['altitude']:.1f}°, azimuth {out['azimuth']:.1f}°")
    print(f"  {'':10}{'distance':>10}{'in sun':>10}{'shaded':>10}")
    print(f"  {'fastest':10}{f['length_m']:>9.0f}m{f['sun_frac']*100:>9.0f}%{f['shade_m']:>9.0f}m")
    print(f"  {'coolest':10}{c['length_m']:>9.0f}m{c['sun_frac']*100:>9.0f}%{c['shade_m']:>9.0f}m")
    print(f"  -> coolest route cuts sun exposure by "
          f"{(f['sun_frac']-c['sun_frac'])*100:.0f} points for {detour:.0f}% more distance\n")


def _run(area, when, alpha, frm, to, out_html):
    out = pipeline.analyze(area, when, alpha, frm, to)
    os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
    viz.folium_map(area, out["trees"], out["shade_geom"], out["result"],
                   out_html, when_label=_label(out))
    _print_summary(out)
    print(f"  map -> {out_html}")
    return out


def cmd_demo(args):
    area = _area(args.area)
    os.makedirs("examples", exist_ok=True)
    stamp = when_slug(config.DEFAULT_DATETIME_LOCAL)
    html = os.path.join("examples", f"{area.key}_{stamp}.html")
    out = _run(area, config.DEFAULT_DATETIME_LOCAL, config.DEFAULT_ALPHA,
               area.demo_from, area.demo_to, html)

    # Hero image + machine-readable stats + planting gaps.
    viz.hero_png(area, out["trees"], out["shade_geom"], out["result"],
                 os.path.join("examples", f"{area.key}_{stamp}.png"),
                 when_label=_label(out))
    streets = data.get_named_streets(area)
    gaps = routing.plant_gaps(out["graph"], area.bbox, streets, top_n=15)
    _write_json(os.path.join("examples", f"{area.key}_stats.json"), _stats(out))
    _write_json(os.path.join("examples", f"{area.key}_planting_gaps.json"), gaps)
    print(f"  hero -> examples/{area.key}_{stamp}.png")
    print(f"  top planting site: {gaps[0]['street']} "
          f"({gaps[0]['sun_frac']*100:.0f}% sun) at {gaps[0]['lat']},{gaps[0]['lon']}")


def cmd_route(args):
    area = _area(args.area)
    _run(area, args.datetime, args.alpha, args.frm, args.to, args.out)


def cmd_gaps(args):
    area = _area(args.area)
    out = pipeline.analyze(area, args.datetime, args.alpha,
                           area.demo_from, area.demo_to)
    streets = data.get_named_streets(area)
    gaps = routing.plant_gaps(out["graph"], area.bbox, streets, top_n=args.top)
    _write_json(args.out, gaps)
    print(f"\n  Top {args.top} tree-planting sites in {area.name} "
          f"(busy + sunny + long):\n")
    print(f"  {'#':>2}  {'sun':>4}  {'len':>5}  street")
    for i, g in enumerate(gaps, 1):
        print(f"  {i:>2}  {g['sun_frac']*100:>3.0f}%  {g['length_m']:>4.0f}m  {g['street']}")
    print(f"\n  full list -> {args.out}")


def cmd_export_web(args):
    area = _area(args.area)
    out = os.path.join("web", "data", f"{area.key}.json")
    export.export_web(area, out)
    size_mb = os.path.getsize(out) / 1e6
    print(f"  web bundle -> {out} ({size_mb:.1f} MB)")
    print(f"  serve it:  python -m http.server -d web 8000  ->  "
          f"http://localhost:8000/?area={area.key}")


def cmd_build_cache(args):
    area = _area(args.area)
    print(f"Caching {area.name} ...")
    data.get_graph(area)
    trees = data.get_trees(area)
    print(f"  cached graph + {len(trees)} trees under {config.DATA_DIR}/")


# --- analytics modules (lazy imports keep the core CLI light) ----------------
def cmd_export_arcgis(args):
    from . import arcgis_export
    arcgis_export.export_all(_area(args.area), when_local=args.datetime,
                             alpha=args.alpha, out_dir=args.out_dir)


def cmd_export_raster(args):
    from . import raster
    path = raster.export_heat_raster(_area(args.area), when_local=args.datetime,
                                     alpha=args.alpha, out_tif=args.out,
                                     resolution_m=args.res)
    print(f"  heat-exposure GeoTIFF -> {path}")


def cmd_db(args):
    from . import sqldb
    sqldb.load(_area(args.area))
    sqldb.run_demo_queries()


def cmd_ml(args):
    from . import ml
    ml.run_demo(_area(args.area))


def cmd_hazard(args):
    from . import hazard
    hazard.run_demo(args.area)


def cmd_serve(args):
    from . import server
    server.main(host=args.host, port=args.port)


# --- helpers -----------------------------------------------------------------
def when_slug(when_local: str) -> str:
    return when_local.split(" ")[1].replace(":", "") if " " in when_local else "out"


def _stats(out: dict) -> dict:
    r = out["result"]
    f, c = r["fast"], r["cool"]
    keys = ("length_m", "sun_m", "shade_m", "sun_frac")
    return {
        "area": out["area"].name,
        "when_local": out["when_local"],
        "sun_altitude_deg": round(out["altitude"], 2),
        "sun_azimuth_deg": round(out["azimuth"], 2),
        "alpha": r["alpha"],
        "n_trees": len(out["trees"]),
        "from_latlon": list(r["from_latlon"]),
        "to_latlon": list(r["to_latlon"]),
        "fastest": {k: round(f[k], 3) for k in keys},
        "coolest": {k: round(c[k], 3) for k in keys},
        "sun_reduction_points": round((f["sun_frac"] - c["sun_frac"]) * 100, 1),
        "extra_distance_pct": (round((c["length_m"] / f["length_m"] - 1) * 100, 1)
                               if f["length_m"] else 0.0),
    }


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="branch",
        description="Shade-aware pedestrian routing from street-tree shadows.")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run the canned Park Slope example")
    d.add_argument("--area", default=config.DEFAULT_AREA)
    d.set_defaults(func=cmd_demo)

    r = sub.add_parser("route", help="route between two points")
    r.add_argument("--area", default=config.DEFAULT_AREA)
    r.add_argument("--from", dest="frm", type=_parse_latlon, required=True,
                   metavar="LAT,LON")
    r.add_argument("--to", type=_parse_latlon, required=True, metavar="LAT,LON")
    r.add_argument("--datetime", default=config.DEFAULT_DATETIME_LOCAL,
                   metavar='"YYYY-MM-DD HH:MM"')
    r.add_argument("--alpha", type=float, default=config.DEFAULT_ALPHA)
    r.add_argument("--out", default="route.html")
    r.set_defaults(func=cmd_route)

    g = sub.add_parser("gaps", help="rank the best tree-planting sites")
    g.add_argument("--area", default=config.DEFAULT_AREA)
    g.add_argument("--datetime", default=config.DEFAULT_DATETIME_LOCAL)
    g.add_argument("--alpha", type=float, default=config.DEFAULT_ALPHA)
    g.add_argument("--top", type=int, default=15)
    g.add_argument("--out", default="planting_gaps.json")
    g.set_defaults(func=cmd_gaps)

    w = sub.add_parser("export-web", help="bake the interactive web app data")
    w.add_argument("--area", default=config.DEFAULT_AREA)
    w.set_defaults(func=cmd_export_web)

    b = sub.add_parser("build-cache", help="pre-download a city's data")
    b.add_argument("--area", default=config.DEFAULT_AREA)
    b.set_defaults(func=cmd_build_cache)

    ea = sub.add_parser("export-arcgis",
                        help="write Esri Shapefile/GeoJSON/CSV outputs")
    ea.add_argument("--area", default=config.DEFAULT_AREA)
    ea.add_argument("--datetime", default=config.DEFAULT_DATETIME_LOCAL)
    ea.add_argument("--alpha", type=float, default=config.DEFAULT_ALPHA)
    ea.add_argument("--out-dir", dest="out_dir", default="exports")
    ea.set_defaults(func=cmd_export_arcgis)

    er = sub.add_parser("export-raster",
                        help="write a georeferenced heat-exposure GeoTIFF")
    er.add_argument("--area", default=config.DEFAULT_AREA)
    er.add_argument("--datetime", default=config.DEFAULT_DATETIME_LOCAL)
    er.add_argument("--alpha", type=float, default=config.DEFAULT_ALPHA)
    er.add_argument("--out", default="exports/heat_exposure.tif")
    er.add_argument("--res", type=float, default=20.0)
    er.set_defaults(func=cmd_export_raster)

    db = sub.add_parser("db",
                        help="load into PostGIS and run spatial SQL (needs the container)")
    db.add_argument("--area", default=config.DEFAULT_AREA)
    db.set_defaults(func=cmd_db)

    ml = sub.add_parser("ml",
                        help="spatial ML: shade surrogate, clustering, interpolation")
    ml.add_argument("--area", default=config.DEFAULT_AREA)
    ml.set_defaults(func=cmd_ml)

    hz = sub.add_parser("hazard",
                        help="heat-vulnerable facilities + H3 aggregation")
    hz.add_argument("--area", default=config.DEFAULT_AREA)
    hz.set_defaults(func=cmd_hazard)

    sv = sub.add_parser("serve",
                        help="run the Google-Maps-style web app (geocode + on-demand routing)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    sv.set_defaults(func=cmd_serve)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
