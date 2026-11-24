-- goh.db 0009 (GOH.SHIPSV2): ships_v2, ships_v1's columns plus the record `body`, so the loop's
-- ship readers (steps, pid, failed_step, own_s, summary, ...) read rows, not the result files.
-- A view: no `-- bound:` header (db_bounds bounds tables; ships' own bound covers its rows).
-- ships_v1 stays as it is (handover plan (d): a view changes only by adding the next one).
CREATE VIEW ships_v2 AS
SELECT run, result, repo, item, sha, status, started, finished, version, at, body FROM ships;
