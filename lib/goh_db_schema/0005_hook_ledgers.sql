-- goh.db 0005 (GOH.STATEDBS4): the hook and cache ledgers, behind lib/hook_ledgers.py.
-- The model-choice hook (hooks/claude/agent_guard.py) and its lane admission (lane_room.py) run on
-- every agent spawn in every repo: each write waits at most 5 s for the file (HOOK_BUSY_MS), then
-- lets the spawn through and spills its row beside goh.db, counted (kv `hook-ledgers.busy`).
-- agent_spawns: one row per allowed spawn and one per SubagentStop outcome, in the order written
-- (was $XDG_CACHE_HOME/goh/agent-spawns.jsonl, imported in line order). agent is the outcome's
-- agent_id, item the loop item a spawn's description opens with; body is the line as JSON.
-- bound: agent_spawns days=180 rows=100000 bytes=96MB stamp=at
CREATE TABLE agent_spawns(
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  event TEXT NOT NULL,
  agent TEXT,
  item TEXT,
  body TEXT NOT NULL
);
CREATE INDEX agent_spawns_at ON agent_spawns(at);
CREATE INDEX agent_spawns_agent ON agent_spawns(agent);
CREATE INDEX agent_spawns_item ON agent_spawns(item);
-- lane_starts: lane admission's verdict per write spawn (was $XDG_STATE_HOME/goh/lane-starts.jsonl):
-- the ledger GOH.ADAPTLANES2 calibrated from, kept the 28 days the file was.
-- bound: lane_starts days=28 rows=20000 bytes=16MB stamp=at
CREATE TABLE lane_starts(
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX lane_starts_at ON lane_starts(at);
-- fault_candidates: the weekly model-choice watch's fault candidates (was fault-candidates.jsonl,
-- rewritten whole per run): each run's list under the run's `at`; the newest run is the list.
-- The watch's own record is kv `model-choice-watch` (0004's kv; was model-choice-watch.json).
-- bound: fault_candidates days=56 rows=20000 bytes=16MB stamp=at
CREATE TABLE fault_candidates(
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX fault_candidates_at ON fault_candidates(at);
