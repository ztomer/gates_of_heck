-- old-writers: safe
-- goh.db 0023 (GOH.ONEGATE): what main's code said of a goh land (lib/land_judge_rows.py). A row a
-- land its gate decided: `base` is the main it was gated on, `land_rc` its gate's exit, `status`
-- where its judging is (`same`: the lane changed no gate code, nothing to judge; `waiting` for room;
-- `unjudged`: not run, by `why`), `started` its first step, `ended` its last. `land_judge_reads`: when
-- a sample was last read (a later judged land is the next one's). A step: main's
-- exit and the tip's, main's seconds, what the pair means, and how a would-be red was ruled.
-- bound: land_judgings days=400 rows=5000 stamp=at
-- with: land_judge_steps of land_judgings
-- bound: land_judge_reads rows=100 stamp=at
CREATE TABLE land_judgings(
  repo TEXT NOT NULL,
  tip TEXT NOT NULL,
  base TEXT NOT NULL,
  land_rc INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('same', 'waiting', 'running', 'done', 'unavailable', 'unjudged')),
  why TEXT,
  started REAL,
  ended REAL,
  seconds REAL CHECK (seconds >= 0),
  at REAL NOT NULL,
  PRIMARY KEY (repo, tip, base)
) STRICT;
CREATE INDEX land_judgings_at ON land_judgings(at);
CREATE INDEX land_judgings_status ON land_judgings(status);
CREATE TABLE land_judge_steps(
  repo TEXT NOT NULL,
  tip TEXT NOT NULL,
  base TEXT NOT NULL,
  ord INTEGER NOT NULL,
  step TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('owed', 'same', 'green', 'would_red', 'flaky', 'both_red', 'broken', 'dropped', 'deleted', 'unavailable')),
  reason TEXT,
  main_rc INTEGER,
  tip_rc INTEGER,
  seconds REAL CHECK (seconds >= 0),
  ruled TEXT CHECK (ruled IN ('false', 'true')),
  ruled_why TEXT,
  PRIMARY KEY (repo, tip, base, ord)
) STRICT, WITHOUT ROWID;
CREATE TABLE land_judge_reads(repo TEXT PRIMARY KEY, at REAL NOT NULL) STRICT;
CREATE INDEX land_judge_steps_outcome ON land_judge_steps(outcome);
