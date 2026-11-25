-- lib/state_backfill.py: each statement is one transaction, run again until it changes no row.
-- The first two are repairs (a row they change is not picked again); the rest fill 500 rows each.
-- 1: an enqueue that died in its pre-step (lib/landq_inflight.py until GOH.LANDCOLS) wrote `recorded`
-- as text and `at` as a number. rest = NULL: lands_refill reads the row's columns again.
UPDATE lands SET rest = NULL, body = (SELECT CASE WHEN json_type(d, '$.at') IN ('real', 'integer')
  THEN json_set(d, '$.at', strftime('%Y-%m-%dT%H:%M:%SZ', lands.at, 'unixepoch')) ELSE d END
  FROM (SELECT CASE WHEN json_type(lands.body, '$.recorded') = 'text'
    THEN json_set(lands.body, '$.recorded', json('true')) ELSE lands.body END AS d))
WHERE CASE WHEN json_valid(body) THEN json_type(body, '$.recorded') = 'text'
  OR json_type(body, '$.at') IN ('real', 'integer') END;
-- 2: the fold: a red land with no class of its own takes red_causes' (lib/land_records.py set_causes
-- writes that table until it is gone, so every backfill folds again).
UPDATE lands SET rest = NULL, body = json_set(body, '$.red_cause', json((
    SELECT r.body FROM red_causes r WHERE r.queue = lands.queue AND r.run = lands.run)))
WHERE body <> '{}' AND red_class IS NULL AND CASE WHEN json_valid(body) THEN coalesce(json_type(body, '$.red_cause'), 'null') = 'null' END AND EXISTS (
    SELECT 1 FROM red_causes r WHERE r.queue = lands.queue AND r.run = lands.run AND CASE WHEN json_valid(r.body) THEN json_type(r.body) = 'object' END);
INSERT OR REPLACE INTO lands (queue, run, item, sha, enqueued, repo, mode, state, attempt, requested, started, finished, k, at, body) SELECT queue, run, item, sha, enqueued, repo, mode, state, attempt, requested, started, finished, k, at, body FROM lands WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO ship_pins (result, run, body, at) SELECT result, run, body, at FROM ship_pins WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO ships (result, run, repo, item, sha, status, started, finished, version, body, at) SELECT result, run, repo, item, sha, status, started, finished, version, body, at FROM ships WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO agent_spawns (n, at, event, agent, item, body) SELECT n, at, event, agent, item, body FROM agent_spawns WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO lane_starts (n, at, body) SELECT n, at, body FROM lane_starts WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO flakes (n, epoch, test, body) SELECT n, epoch, test, body FROM flakes WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO selection_misses (n, queue, day, at, run, body) SELECT n, queue, day, at, run, body FROM selection_misses WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO reuse_shadow (n, epoch, path, body) SELECT n, epoch, path, body FROM reuse_shadow WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO reuse_audit (path, body, at) SELECT path, body, at FROM reuse_audit WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO requests (queue, run, item, repo, sha, mode, state, pid, pid_stamp, until, filed_at, body) SELECT queue, run, item, repo, sha, mode, state, pid, pid_stamp, until, filed_at, body FROM requests WHERE rest IS NULL AND json_valid(body) LIMIT 500;
INSERT OR REPLACE INTO early (queue, run, at, body) SELECT queue, run, at, body FROM early WHERE rest IS NULL AND json_valid(body) LIMIT 500;
