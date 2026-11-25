-- old-writers: safe
-- bound: spans days=400 rows=200000 bytes=32MB stamp=at
-- with: span_steps of spans
CREATE TABLE spans(
  kind TEXT NOT NULL, repo TEXT NOT NULL, at REAL NOT NULL, run TEXT NOT NULL DEFAULT '',
  wall_s REAL NOT NULL CHECK (wall_s >= 0), ok INTEGER NOT NULL CHECK (ok IN (0, 1)), item TEXT, sha TEXT,
  subject TEXT, cpu_s REAL, load1 REAL, load_end REAL, control_drift REAL, wait_lock_s REAL,
  exit INTEGER, pre_s REAL, version TEXT, slot_wait_s REAL, slot_asked_at REAL, slot_class TEXT,
  pre_run_s REAL, pre_cpu_s REAL, pre_workers INTEGER, pre_reused TEXT, pre_fast INTEGER, pre_why TEXT,
  suite_reused TEXT, suite_why TEXT, before_s REAL, at_day INTEGER, backfill TEXT, fs_events INTEGER,
  fs_dirs INTEGER, fs_tests INTEGER, fs_whole INTEGER, shadow_f INTEGER, shadow_ran INTEGER, shadow_red_out INTEGER,
  shadow_red TEXT,
  PRIMARY KEY (kind, repo, at, run)
) STRICT;
CREATE INDEX spans_run ON spans(run);
CREATE INDEX spans_at ON spans(at);
CREATE TABLE span_steps(kind TEXT NOT NULL, repo TEXT NOT NULL, at REAL NOT NULL, run TEXT NOT NULL, ord INTEGER NOT NULL,
  label TEXT NOT NULL, seconds REAL NOT NULL, PRIMARY KEY (kind, repo, at, run, ord)) STRICT, WITHOUT ROWID;
CREATE INDEX span_steps_at ON span_steps(at);
CREATE VIEW lands_v2 AS SELECT queue, run, item, sha, mode, state, repo, attempt, k, at, enqueued,
  unixepoch(requested, 'subsec') AS requested_at, unixepoch(started, 'subsec') AS started_at, unixepoch(finished, 'subsec') AS finished_at,
  landed, log, why, stage, cause, cause_detail, k_why, pre_affected_ran, pre_affected_took, pre_affected_by, batch, pre_run_skipped, remote_outcome, remote_cause, remote_where, remote_host, red_class, red_rule, diffcov_item, diffcov_said, cov_said, cov_head, cov_base, pre_s, slot_s, slot_wait_s, load_start, load_end, pre_affected_lane_s, pre_run_wait_s, pre_run_run_s, pre_run_limit_s, pre_run_cpu_s, remote_worker_s, remote_mac_s, remote_mac_est_s, remote_rerun_s, remote_saved_s, remote_t0, slot_wait_short_s, slot_wait_land_s, slot_wait_pre_s, slot_wait_work_s, batch_items, batch_gates, pre_affected_tests, pre_run_workers, remote_tests, cov_lines, early, recorded, remote_fresh FROM lands;
CREATE VIEW land_lists_v1 AS SELECT queue, run, kind, ord, value, at FROM land_lists;
CREATE VIEW ships_v3 AS SELECT result, run, item, sha, status, version, repo, at, unixepoch(started, 'subsec') AS started_at, unixepoch(finished, 'subsec') AS finished_at,
  before_clock_name, before_clock_status, before_clock_log, name, summary, remote, branch, handoff_sha, base_tag, release, install, loopctl, failed_step, bump, verdicts, allow_diffcov, pid_stamp, grace_over_from, grace_canary_sha, grace_why, bisect_base, bisect_culprit, bisect_since, bisect_subject, bisect_fault, bisect_candidate, bisect_step_sha, quiet_off, quiet_unmeasured, diffcov_base, diffcov_said, before_clock_seconds, land_wait_s, own_s, load1, quiet_min_idle_pct, quiet_max_s, quiet_idle_pct_first, quiet_idle_pct, quiet_waited_s, quiet_load1, timeout_s, pid, quiet_readings, grace_count, bisect_lands, bisect_step_n, bisect_step_lands, release_pushes, progress, quiet_ok, bisect_flake, bisect_step_recheck FROM ships;
CREATE VIEW ship_steps_v1 AS SELECT result, kind, ord, name, status, code, seconds, log, why FROM ship_steps;
CREATE VIEW spawns_v1 AS SELECT n, at, event, agent, agent_type, asked_type, description, effort, first_line, model, model_id, prompt_sha, session, tool_use_id, declared, route, agent_type_escape, status, report, profile, profile_why, lane, meter_read_at, no_item_why, pool_said, pool_why, meter_5h, meter_7d, meter_age_h, tokens_cache_creation, tokens_cache_read, tokens_input, tokens_output, tokens_total, cores, exempt, explored, tokens_settled, meter_ok, load1, load5, load15 FROM agent_spawns;
CREATE VIEW spawn_lists_v1 AS SELECT n, kind, ord, value FROM spawn_lists;
CREATE VIEW spend_items_v1 AS
WITH o AS MATERIALIZED (SELECT tool_use_id AS tool, max(tokens_total) AS tokens FROM agent_spawns
  WHERE event = 'outcome' AND tool_use_id IS NOT NULL GROUP BY 1),
s AS MATERIALIZED (SELECT min(n) AS n, tool_use_id AS tool FROM agent_spawns WHERE event = 'spawn' AND tool_use_id IS NOT NULL
  AND n IN (SELECT n FROM spawn_lists WHERE kind = 'item') GROUP BY tool_use_id),
w AS MATERIALIZED (SELECT n, count(*) AS ways FROM spawn_lists WHERE kind = 'item' GROUP BY n)
SELECT l.value AS item, sum(o.tokens * 1.0 / w.ways) AS tokens
FROM s CROSS JOIN o ON o.tool = s.tool CROSS JOIN w ON w.n = s.n CROSS JOIN spawn_lists l ON l.n = s.n AND l.kind = 'item' GROUP BY 1;
CREATE VIEW spans_v1 AS SELECT kind, repo, at, run, wall_s, ok, item, sha, subject, cpu_s, load1, load_end, exit, wait_lock_s FROM spans;
