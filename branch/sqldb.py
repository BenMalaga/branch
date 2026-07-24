"""Load branch into PostGIS and run real spatial SQL against it.

The in-memory pipeline (``pipeline.analyze``) produces the same artifacts every
other module consumes: a walk graph whose edges carry a modeled ``sun_frac`` and
a GeoDataFrame of street trees. This module persists those into a PostGIS
database so the analysis is queryable with spatial SQL instead of Python:
attribute filters, ``ST_DWithin`` proximity, KNN nearest-neighbor via the
``<->`` operator, and network-wide aggregates.

Connection defaults match ``docker-compose.yml`` (host localhost, port 55432,
db/user ``branch``/``postgres``, password ``branch``) and can be overridden
with the standard ``PG*`` environment variables. Geometry is stored in WGS84
(EPSG:4326); distances that must be in meters use the geography cast.
"""
from __future__ import annotations

import os

import psycopg2
from psycopg2.extras import execute_values

from . import config, geoutil, pipeline
from .config import Area

# Connection defaults. Every one can be overridden by its PG* environment
# variable so the same code talks to the compose container or any other server.
DEFAULTS = {
    "host": "localhost",
    "port": "55432",
    "dbname": "branch",
    "user": "postgres",
    "password": "branch",
}

# The demo trip origin for the proximity query. Filled from the area at load
# time; falls back to Park Slope's demo origin so the SQL file stays runnable
# standalone.
DEMO_START_LATLON = config.AREAS[config.DEFAULT_AREA].demo_from


def connect():
    """Open a psycopg2 connection, honoring PGHOST/PGPORT/PG* env overrides."""
    return psycopg2.connect(
        host=os.environ.get("PGHOST", DEFAULTS["host"]),
        port=os.environ.get("PGPORT", DEFAULTS["port"]),
        dbname=os.environ.get("PGDATABASE", DEFAULTS["dbname"]),
        user=os.environ.get("PGUSER", DEFAULTS["user"]),
        password=os.environ.get("PGPASSWORD", DEFAULTS["password"]),
    )


# --- Schema ------------------------------------------------------------------
_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS postgis;

DROP TABLE IF EXISTS branch_edges;
DROP TABLE IF EXISTS branch_nodes;
DROP TABLE IF EXISTS branch_trees;

CREATE TABLE branch_nodes (
    node_id  BIGINT PRIMARY KEY,
    geom     geometry(Point, 4326) NOT NULL
);

CREATE TABLE branch_edges (
    edge_id   BIGSERIAL PRIMARY KEY,
    u         BIGINT NOT NULL,
    v         BIGINT NOT NULL,
    length_m  DOUBLE PRECISION NOT NULL,
    sun_frac  DOUBLE PRECISION NOT NULL,
    geom      geometry(LineString, 4326) NOT NULL
);

CREATE TABLE branch_trees (
    tree_id  TEXT,
    dbh      DOUBLE PRECISION,
    species  TEXT,
    health   TEXT,
    geom     geometry(Point, 4326) NOT NULL
);

CREATE INDEX branch_nodes_geom_gix ON branch_nodes USING GIST (geom);
CREATE INDEX branch_edges_geom_gix ON branch_edges USING GIST (geom);
CREATE INDEX branch_trees_geom_gix ON branch_trees USING GIST (geom);
CREATE INDEX branch_edges_sun_frac_ix ON branch_edges (sun_frac);
"""


def load(area: Area, when_local: str = config.DEFAULT_DATETIME_LOCAL,
         alpha: float = config.DEFAULT_ALPHA) -> dict:
    """Run the pipeline for ``area`` and load nodes, edges, and trees into PostGIS.

    Rebuilds the schema, then inserts every walk-graph node and edge (with its
    modeled ``sun_frac``) and every street tree, converting each shapely
    geometry to WKT and storing it via ``ST_GeomFromText(wkt, 4326)``. Returns a
    dict of inserted row counts.
    """
    global DEMO_START_LATLON
    DEMO_START_LATLON = area.demo_from

    out = pipeline.analyze(area, when_local, alpha,
                           from_latlon=area.demo_from, to_latlon=area.demo_to)
    G, trees = out["graph"], out["trees"]

    node_rows = _node_rows(G)
    edge_rows = _edge_rows(G)
    tree_rows = _tree_rows(trees)

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
                execute_values(
                    cur,
                    "INSERT INTO branch_nodes (node_id, geom) VALUES %s",
                    node_rows,
                    template="(%s, ST_GeomFromText(%s, 4326))",
                )
                execute_values(
                    cur,
                    "INSERT INTO branch_edges (u, v, length_m, sun_frac, geom) "
                    "VALUES %s",
                    edge_rows,
                    template="(%s, %s, %s, %s, ST_GeomFromText(%s, 4326))",
                )
                execute_values(
                    cur,
                    "INSERT INTO branch_trees (tree_id, dbh, species, health, "
                    "geom) VALUES %s",
                    tree_rows,
                    template="(%s, %s, %s, %s, ST_GeomFromText(%s, 4326))",
                )
    finally:
        conn.close()

    counts = {"nodes": len(node_rows), "edges": len(edge_rows),
              "trees": len(tree_rows)}
    print(f"Loaded {area.name} into PostGIS at "
          f"{os.environ.get('PGHOST', DEFAULTS['host'])}:"
          f"{os.environ.get('PGPORT', DEFAULTS['port'])}/"
          f"{os.environ.get('PGDATABASE', DEFAULTS['dbname'])}")
    print(f"  nodes={counts['nodes']}  edges={counts['edges']}  "
          f"trees={counts['trees']}")
    return counts


# --- Row builders ------------------------------------------------------------
def _node_rows(G) -> list[tuple]:
    """(node_id, POINT wkt) for every graph node, reprojected to WGS84."""
    rows = []
    for nid, nd in G.nodes(data=True):
        lat, lon = geoutil.xy_to_latlon(nd["x"], nd["y"])
        rows.append((int(nid), f"POINT({lon} {lat})"))
    return rows


def _edge_rows(G) -> list[tuple]:
    """(u, v, length_m, sun_frac, LINESTRING wkt) for every graph edge."""
    rows = []
    for u, v, d in G.edges(data=True):
        wgs = geoutil.geom_to_wgs(d["geometry"])
        coords = ", ".join(f"{lon} {lat}" for lon, lat in wgs.coords)
        rows.append((int(u), int(v), float(d["length"]),
                     float(d.get("sun_frac", 1.0)), f"LINESTRING({coords})"))
    return rows


def _tree_rows(trees) -> list[tuple]:
    """(tree_id, dbh, species, health, POINT wkt) for every tree, in WGS84."""
    rows = []
    wgs = trees.to_crs(config.WGS84)
    for rec, geom in zip(wgs.to_dict("records"), wgs.geometry.values):
        rows.append((
            str(rec.get("tree_id")) if rec.get("tree_id") is not None else None,
            float(rec.get("dbh") or 0.0),
            rec.get("species"),
            rec.get("health"),
            f"POINT({geom.x} {geom.y})",
        ))
    return rows


# --- Demo queries ------------------------------------------------------------
def run_demo_queries() -> None:
    """Run and print four real spatial SQL queries against the loaded tables."""
    lat, lon = DEMO_START_LATLON
    conn = connect()
    try:
        with conn.cursor() as cur:
            print("\nbranch PostGIS spatial queries\n")

            # (a) High heat exposure: segments more than 70% in sun.
            cur.execute(
                "SELECT count(*) FROM branch_edges WHERE sun_frac > 0.7;")
            sunny = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM branch_edges;")
            total = cur.fetchone()[0]
            print(f"(a) segments >70% sun (high heat exposure): "
                  f"{sunny} of {total}")

            # (b) Trees within 50 m of the demo trip origin (meters via geography).
            cur.execute(
                "SELECT count(*) FROM branch_trees "
                "WHERE ST_DWithin(geom::geography, "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 50);",
                (lon, lat))
            near = cur.fetchone()[0]
            print(f"(b) trees within 50 m of start ({lat}, {lon}): {near}")

            # (c) Nearest tree to each of the five sunniest segments (KNN <->).
            cur.execute(
                """
                WITH sunniest AS (
                    SELECT edge_id, sun_frac, length_m, geom
                    FROM branch_edges
                    ORDER BY sun_frac DESC, length_m DESC
                    LIMIT 5
                )
                SELECT s.edge_id,
                       round(s.sun_frac::numeric, 3),
                       round(s.length_m::numeric, 1),
                       t.species,
                       round(ST_Distance(s.geom::geography,
                                         t.geom::geography)::numeric, 1)
                FROM sunniest s
                CROSS JOIN LATERAL (
                    SELECT species, geom
                    FROM branch_trees
                    ORDER BY branch_trees.geom <-> s.geom
                    LIMIT 1
                ) t
                ORDER BY s.sun_frac DESC;
                """)
            print("(c) nearest tree to the 5 sunniest segments (KNN <->):")
            print(f"      {'edge':>7}  {'sun':>5}  {'len_m':>6}  "
                  f"{'tree_m':>6}  species")
            for edge_id, sun, length_m, species, dist_m in cur.fetchall():
                print(f"      {edge_id:>7}  {float(sun):>5.2f}  "
                      f"{float(length_m):>6.1f}  {float(dist_m):>6.1f}  "
                      f"{species or 'unknown'}")

            # (d) Network-wide shade budget.
            cur.execute(
                "SELECT round(SUM(length_m)::numeric, 0), "
                "round(SUM(length_m * (1 - sun_frac))::numeric, 0), "
                "round(SUM(length_m * sun_frac)::numeric, 0), "
                "round((100.0 * SUM(length_m * (1 - sun_frac)) "
                "/ NULLIF(SUM(length_m), 0))::numeric, 1) "
                "FROM branch_edges;")
            total_m, shaded_m, sunlit_m, shaded_pct = cur.fetchone()
            print("(d) sidewalk shade budget:")
            print(f"      total {float(total_m):.0f} m  |  shaded "
                  f"{float(shaded_m):.0f} m ({float(shaded_pct):.1f}%)  |  "
                  f"sunlit {float(sunlit_m):.0f} m")
    finally:
        conn.close()
