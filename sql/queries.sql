-- Example branch spatial queries.
--
-- These are the same queries branch.sqldb.run_demo_queries() runs against the
-- loaded Park Slope tables. They show off the reason to keep this data in
-- PostGIS rather than in memory: attribute + spatial predicates, distance in
-- meters via the geography cast, and index-backed nearest-neighbor search.
--
-- The demo start point below is Park Slope's demo origin near Grand Army Plaza
-- (lat 40.6772, lon -73.9735). PostGIS wants POINT(lon lat).

-- (a) High heat exposure: how many sidewalk segments are more than 70% in sun.
SELECT count(*) AS sunny_segments
FROM branch_edges
WHERE sun_frac > 0.7;

-- (b) Tree cover near the trip origin: street trees within 50 meters of the
--     demo start point. Cast to geography so ST_DWithin is in meters, not
--     degrees, and so it uses the GiST index on geom.
SELECT count(*) AS trees_within_50m
FROM branch_trees
WHERE ST_DWithin(
    geom::geography,
    ST_SetSRID(ST_MakePoint(-73.9735, 40.6772), 4326)::geography,
    50
);

-- (c) Nearest tree to each of the five sunniest segments. The <-> KNN operator
--     orders candidates by index-backed distance; ST_Distance on geography
--     reports the gap in meters. A LATERAL subquery finds one nearest tree per
--     segment.
WITH sunniest AS (
    SELECT edge_id, sun_frac, length_m, geom
    FROM branch_edges
    ORDER BY sun_frac DESC, length_m DESC
    LIMIT 5
)
SELECT s.edge_id,
       round(s.sun_frac::numeric, 3)          AS sun_frac,
       round(s.length_m::numeric, 1)          AS length_m,
       t.species,
       round(ST_Distance(s.geom::geography,
                         t.geom::geography)::numeric, 1) AS tree_dist_m
FROM sunniest s
CROSS JOIN LATERAL (
    SELECT species, geom
    FROM branch_trees
    ORDER BY branch_trees.geom <-> s.geom
    LIMIT 1
) t
ORDER BY s.sun_frac DESC;

-- (d) Network-wide shade budget: total sidewalk length, and how much of it is
--     shaded vs sunlit, from the per-segment sun_frac. shaded = len*(1-sun),
--     sunlit = len*sun.
SELECT round(SUM(length_m)::numeric, 0)                    AS total_m,
       round(SUM(length_m * (1 - sun_frac))::numeric, 0)  AS shaded_m,
       round(SUM(length_m * sun_frac)::numeric, 0)         AS sunlit_m,
       round((100.0 * SUM(length_m * (1 - sun_frac))
                    / NULLIF(SUM(length_m), 0))::numeric, 1) AS shaded_pct
FROM branch_edges;
