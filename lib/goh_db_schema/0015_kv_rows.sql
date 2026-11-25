-- old-writers: safe
-- with: fault_candidate_lists of fault_candidates
-- bound: pin_pushes rows=1000 stamp=at
-- bound: pin_sweep_repos rows=5000 stamp=at
-- bound: wf_pins rows=1000 stamp=at
-- bound: linux_unsafe rows=1000 stamp=at
-- bound: spend_first_done rows=5000 stamp=at
CREATE TABLE pin_pushes(repo TEXT PRIMARY KEY, status TEXT NOT NULL, said TEXT, sweep TEXT, place TEXT, log TEXT, at REAL NOT NULL) STRICT;
CREATE TABLE pin_sweep_repos(sweep TEXT NOT NULL, ord INTEGER NOT NULL, repo TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (sweep, ord)) STRICT, WITHOUT ROWID;
CREATE TABLE wf_pins(repo TEXT PRIMARY KEY, status TEXT NOT NULL, said TEXT, tag TEXT, ruff TEXT, sweep TEXT, at REAL NOT NULL) STRICT;
CREATE TABLE linux_unsafe(module TEXT PRIMARY KEY, why TEXT NOT NULL, at REAL NOT NULL) STRICT;
CREATE TABLE spend_first_done(item TEXT PRIMARY KEY, first_done TEXT NOT NULL, at REAL NOT NULL) STRICT;
ALTER TABLE fault_candidates ADD COLUMN id TEXT CHECK (typeof(id) IN ('text','null'));
ALTER TABLE fault_candidates ADD COLUMN item TEXT CHECK (typeof(item) IN ('text','null'));
ALTER TABLE fault_candidates ADD COLUMN phase TEXT CHECK (typeof(phase) IN ('text','null'));
ALTER TABLE fault_candidates ADD COLUMN repo TEXT CHECK (typeof(repo) IN ('text','null'));
ALTER TABLE fault_candidates ADD COLUMN status TEXT CHECK (typeof(status) IN ('text','null'));
ALTER TABLE fault_candidates ADD COLUMN done_state TEXT CHECK (typeof(done_state) IN ('text','null'));
ALTER TABLE fault_candidates ADD COLUMN rest TEXT CHECK (rest IS NULL OR (json_valid(rest) AND NOT coalesce(json_type(rest, '$.id') IN ('text'), 0) AND NOT coalesce(json_type(rest, '$.item') IN ('text'), 0) AND NOT coalesce(json_type(rest, '$.phase') IN ('text'), 0) AND NOT coalesce(json_type(rest, '$.repo') IN ('text'), 0) AND NOT coalesce(json_type(rest, '$.status') IN ('text'), 0) AND NOT coalesce(json_type(rest, '$.done_state') IN ('text'), 0)));
CREATE TABLE fault_candidate_lists(n INTEGER NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('fault', 'depend')), ord INTEGER NOT NULL, value TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (n, kind, ord)) STRICT, WITHOUT ROWID;
CREATE INDEX fault_candidate_lists_value ON fault_candidate_lists(kind, value);
CREATE INDEX fault_candidate_lists_at ON fault_candidate_lists(at);
CREATE TRIGGER fault_candidates_fill AFTER INSERT ON fault_candidates WHEN NEW.rest IS NULL AND json_valid(NEW.body) BEGIN
  DELETE FROM fault_candidate_lists WHERE n = NEW.n;
  UPDATE fault_candidates SET id = CASE WHEN json_type(NEW.body, '$.id') IN ('text') THEN NEW.body ->> '$.id' END, item = CASE WHEN json_type(NEW.body, '$.item') IN ('text') THEN NEW.body ->> '$.item' END, phase = CASE WHEN json_type(NEW.body, '$.phase') IN ('text') THEN NEW.body ->> '$.phase' END, repo = CASE WHEN json_type(NEW.body, '$.repo') IN ('text') THEN NEW.body ->> '$.repo' END, status = CASE WHEN json_type(NEW.body, '$.status') IN ('text') THEN NEW.body ->> '$.status' END, done_state = CASE WHEN json_type(NEW.body, '$.done_state') IN ('text') THEN NEW.body ->> '$.done_state' END, rest = json_remove(NEW.body, CASE WHEN json_type(NEW.body, '$.id') IN ('text') OR json_type(NEW.body, '$.id') = 'null' THEN '$.id' ELSE '$."__goh_noop__"' END, CASE WHEN json_type(NEW.body, '$.item') IN ('text') OR json_type(NEW.body, '$.item') = 'null' THEN '$.item' ELSE '$."__goh_noop__"' END, CASE WHEN json_type(NEW.body, '$.phase') IN ('text') OR json_type(NEW.body, '$.phase') = 'null' THEN '$.phase' ELSE '$."__goh_noop__"' END, CASE WHEN json_type(NEW.body, '$.repo') IN ('text') OR json_type(NEW.body, '$.repo') = 'null' THEN '$.repo' ELSE '$."__goh_noop__"' END, CASE WHEN json_type(NEW.body, '$.status') IN ('text') OR json_type(NEW.body, '$.status') = 'null' THEN '$.status' ELSE '$."__goh_noop__"' END, CASE WHEN json_type(NEW.body, '$.done_state') IN ('text') OR json_type(NEW.body, '$.done_state') = 'null' THEN '$.done_state' ELSE '$."__goh_noop__"' END, CASE WHEN (json_type(NEW.body, '$.faults') = 'array' AND NOT EXISTS (SELECT 1 FROM json_each(NEW.body, '$.faults') e WHERE e.type <> 'text')) OR json_type(NEW.body, '$.faults') = 'null' THEN '$.faults' ELSE '$."__goh_noop__"' END, CASE WHEN (json_type(NEW.body, '$.depends') = 'array' AND NOT EXISTS (SELECT 1 FROM json_each(NEW.body, '$.depends') e WHERE e.type <> 'text')) OR json_type(NEW.body, '$.depends') = 'null' THEN '$.depends' ELSE '$."__goh_noop__"' END) WHERE n = NEW.n;
  INSERT INTO fault_candidate_lists SELECT NEW.n, 'fault', e.key, e.value, NEW.at FROM json_each(NEW.body, '$.faults') e WHERE (json_type(NEW.body, '$.faults') = 'array' AND NOT EXISTS (SELECT 1 FROM json_each(NEW.body, '$.faults') e WHERE e.type <> 'text'));
  INSERT INTO fault_candidate_lists SELECT NEW.n, 'depend', e.key, e.value, NEW.at FROM json_each(NEW.body, '$.depends') e WHERE (json_type(NEW.body, '$.depends') = 'array' AND NOT EXISTS (SELECT 1 FROM json_each(NEW.body, '$.depends') e WHERE e.type <> 'text'));
END;
CREATE TRIGGER fault_candidates_refill AFTER UPDATE OF body ON fault_candidates WHEN NEW.rest IS NULL AND NEW.body <> '{}' AND json_valid(NEW.body) BEGIN
  DELETE FROM fault_candidates WHERE n = NEW.n;
  INSERT INTO fault_candidates(n, at, body) VALUES (NEW.n, NEW.at, NEW.body);
END;
CREATE TRIGGER fault_candidates_bad AFTER UPDATE OF body ON fault_candidates WHEN NEW.rest IS NOT NULL AND NOT json_valid(NEW.body) BEGIN
  UPDATE fault_candidates SET rest = NULL WHERE n = NEW.n;
END;
CREATE TRIGGER fault_candidates_gone AFTER DELETE ON fault_candidates BEGIN
  DELETE FROM fault_candidate_lists WHERE n = OLD.n;
END;
