"""Rendering: an interactive Folium web map and a static hero image.

Both draw the same four layers: the tree-shadow footprint, the trees, the
fastest route (red), and the coolest route (blue), with a start/end marker and a
stats readout.
"""
from __future__ import annotations

import folium
from shapely.geometry import mapping

from . import geoutil
from .config import Area


def _latlon_coords(metric_geom) -> list[tuple[float, float]]:
    """Metric LineString -> [(lat, lon), ...] for Folium."""
    wgs = geoutil.geom_to_wgs(metric_geom)
    return [(lat, lon) for lon, lat in wgs.coords]


def _stats_html(result: dict) -> str:
    f, c = result["fast"], result["cool"]
    detour = (c["length_m"] / f["length_m"] - 1.0) * 100.0 if f["length_m"] else 0.0
    sun_cut = (f["sun_frac"] - c["sun_frac"]) * 100.0
    return f"""
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
        background: rgba(255,255,255,0.94); padding: 12px 14px; border-radius: 8px;
        box-shadow: 0 1px 6px rgba(0,0,0,0.25); font-family: -apple-system, Segoe UI, Roboto, sans-serif;
        font-size: 12px; color: #1a1a1a; max-width: 240px;">
      <div style="font-weight:700; font-size:13px; margin-bottom:6px;">branch</div>
      <div style="display:flex; align-items:center; margin:3px 0;">
        <span style="display:inline-block;width:22px;height:3px;background:#e4572e;margin-right:6px;"></span>
        Fastest: {f['length_m']:.0f} m, {f['sun_frac']*100:.0f}% in sun</div>
      <div style="display:flex; align-items:center; margin:3px 0;">
        <span style="display:inline-block;width:22px;height:3px;background:#2e6fe4;margin-right:6px;"></span>
        Coolest: {c['length_m']:.0f} m, {c['sun_frac']*100:.0f}% in sun</div>
      <hr style="border:none;border-top:1px solid #ddd;margin:7px 0;">
      <div>Sun exposure cut <b>{sun_cut:.0f} points</b><br>
      for <b>{detour:.0f}%</b> extra distance</div>
    </div>"""


def folium_map(area: Area, trees, shade_geom, result: dict, out_html: str,
               when_label: str = "") -> str:
    """Write the interactive map to ``out_html`` and return the path."""
    m = folium.Map(location=list(area.center), zoom_start=15,
                   tiles="cartodbpositron", control_scale=True)

    # Shade footprint (drawn first, underneath).
    if shade_geom is not None and not shade_geom.is_empty:
        folium.GeoJson(
            mapping(geoutil.geom_to_wgs(shade_geom)),
            name="Tree shade",
            style_function=lambda _: {"fillColor": "#6b8e6b", "color": "#6b8e6b",
                                      "weight": 0, "fillOpacity": 0.35},
        ).add_to(m)

    # Trees.
    tree_group = folium.FeatureGroup(name=f"Street trees ({len(trees)})", show=True)
    for pt in trees.geometry.values:
        lat, lon = geoutil.xy_to_latlon(pt.x, pt.y)
        folium.CircleMarker([lat, lon], radius=1.4, color="#2e7d32",
                            fill=True, fill_opacity=0.55, weight=0).add_to(tree_group)
    tree_group.add_to(m)

    # Routes.
    folium.PolyLine(_latlon_coords(result["fast"]["geometry"]), color="#e4572e",
                    weight=5, opacity=0.9, name="Fastest route",
                    tooltip="Fastest route").add_to(m)
    folium.PolyLine(_latlon_coords(result["cool"]["geometry"]), color="#2e6fe4",
                    weight=5, opacity=0.9, name="Coolest route",
                    tooltip="Coolest route").add_to(m)

    # Endpoints.
    folium.Marker(list(result["from_latlon"]), tooltip="Start",
                  icon=folium.Icon(color="green", icon="play", prefix="fa")).add_to(m)
    folium.Marker(list(result["to_latlon"]), tooltip="Destination",
                  icon=folium.Icon(color="red", icon="flag-checkered", prefix="fa")).add_to(m)

    folium.LayerControl(collapsed=True).add_to(m)
    m.get_root().html.add_child(folium.Element(_stats_html(result)))
    if when_label:
        m.get_root().html.add_child(folium.Element(
            f'<div style="position:fixed;bottom:12px;left:12px;z-index:9999;'
            f'background:rgba(255,255,255,0.9);padding:5px 9px;border-radius:6px;'
            f'font-family:sans-serif;font-size:11px;color:#333;">{area.name} &middot; {when_label}</div>'))
    m.save(out_html)
    return out_html


def hero_png(area: Area, trees, shade_geom, result: dict, out_png: str,
             when_label: str = "") -> str:
    """Write a static matplotlib figure to ``out_png`` and return the path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    fig, ax = plt.subplots(figsize=(9, 9), dpi=140)

    # Shade footprint.
    if shade_geom is not None and not shade_geom.is_empty:
        geoms = getattr(shade_geom, "geoms", [shade_geom])
        for poly in geoms:
            xs, ys = poly.exterior.xy
            ax.fill(xs, ys, color="#9ab89a", alpha=0.45, linewidth=0, zorder=1)

    # Trees.
    txs = [p.x for p in trees.geometry.values]
    tys = [p.y for p in trees.geometry.values]
    ax.scatter(txs, tys, s=3, color="#2e7d32", alpha=0.5, zorder=2)

    # Routes.
    fx, fy = result["fast"]["geometry"].xy
    cx, cy = result["cool"]["geometry"].xy
    ax.plot(fx, fy, color="#e4572e", linewidth=3.2, zorder=4, solid_capstyle="round")
    ax.plot(cx, cy, color="#2e6fe4", linewidth=3.2, zorder=5, solid_capstyle="round")

    # Endpoints.
    for latlon, mk in ((result["from_latlon"], "o"), (result["to_latlon"], "s")):
        x, y = geoutil.latlon_to_xy(*latlon)
        ax.scatter([x], [y], s=90, marker=mk, color="black", zorder=6)

    f, c = result["fast"], result["cool"]
    ax.set_title(f"branch: {area.name}"
                 + (f"  ({when_label})" if when_label else ""),
                 fontsize=14, fontweight="bold")
    legend = [
        Line2D([0], [0], color="#e4572e", lw=3,
               label=f"Fastest: {f['length_m']:.0f} m, {f['sun_frac']*100:.0f}% sun"),
        Line2D([0], [0], color="#2e6fe4", lw=3,
               label=f"Coolest: {c['length_m']:.0f} m, {c['sun_frac']*100:.0f}% sun"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#2e7d32",
               markersize=8, label=f"Street trees ({len(trees)})"),
    ]
    ax.legend(handles=legend, loc="lower right", fontsize=10, framealpha=0.9)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    return out_png
