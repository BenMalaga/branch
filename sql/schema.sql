-- branch PostGIS schema.
--
-- Three tables mirror the in-memory pipeline artifacts:
--   branch_nodes   walk-graph intersections (POINT, WGS84)
--   branch_edges   sidewalk segments carrying the modeled sun_frac (LINESTRING)
--   branch_trees   NYC street-tree census points (POINT)
--
-- All geometry is stored in EPSG:4326 (WGS84 lon/lat) so it lines up with the
-- web map and GeoJSON exports. Length is kept in meters (precomputed upstream in
-- the UTM 18N metric CRS) so distance math never depends on the storage SRID.
-- GiST indexes on every geometry column make ST_DWithin / KNN (<->) queries fast.

CREATE EXTENSION IF NOT EXISTS postgis;

DROP TABLE IF EXISTS branch_edges;
DROP TABLE IF EXISTS branch_nodes;
DROP TABLE IF EXISTS branch_trees;

-- Walk-graph nodes (street intersections and dead-ends).
CREATE TABLE branch_nodes (
    node_id  BIGINT PRIMARY KEY,
    geom     geometry(Point, 4326) NOT NULL
);

-- Walk-graph edges (sidewalk segments) with the modeled sun exposure.
-- sun_frac in [0, 1]: 0 = fully shaded, 1 = fully in sun.
CREATE TABLE branch_edges (
    edge_id   BIGSERIAL PRIMARY KEY,
    u         BIGINT NOT NULL,
    v         BIGINT NOT NULL,
    length_m  DOUBLE PRECISION NOT NULL,
    sun_frac  DOUBLE PRECISION NOT NULL,
    geom      geometry(LineString, 4326) NOT NULL
);

-- Street trees (NYC 2015 census subset for the area).
CREATE TABLE branch_trees (
    tree_id  TEXT,
    dbh      DOUBLE PRECISION,   -- trunk diameter at breast height, inches
    species  TEXT,
    health   TEXT,
    geom     geometry(Point, 4326) NOT NULL
);

-- Spatial indexes for ST_DWithin, ST_Intersects, and KNN (<->) operators.
CREATE INDEX branch_nodes_geom_gix ON branch_nodes USING GIST (geom);
CREATE INDEX branch_edges_geom_gix ON branch_edges USING GIST (geom);
CREATE INDEX branch_trees_geom_gix ON branch_trees USING GIST (geom);

-- Attribute index for the common "high heat exposure" filter.
CREATE INDEX branch_edges_sun_frac_ix ON branch_edges (sun_frac);
