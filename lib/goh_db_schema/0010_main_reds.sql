-- goh.db 0010 (GOH.MAINREDWAIVE): readings of main's tests at one sha, the only evidence a land
-- may waive a red test by. A reading is a run on MAIN's tree at SHA: the idle full suite
-- (lib/idle_suite.py, ran '*': every test) or a probe of named tests on a clean checkout of main
-- (lib/main_reading.py `probe`, ran: a JSON list of node ids). main_red_tests holds the nodes the
-- reading saw red; a node it ran and does not hold was green. lib/main_reading.py is the door.
--   repo   the main checkout's realpath     outcome  green|red (the run's own exit)
-- bound: main_readings days=60 rows=20000 bytes=16MB stamp=at
CREATE TABLE main_readings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo TEXT NOT NULL,
  sha TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('idle-suite', 'probe')),
  ran TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN ('green', 'red')),
  at REAL NOT NULL
);
CREATE INDEX main_readings_sha ON main_readings(repo, sha, id);
CREATE INDEX main_readings_at ON main_readings(at);
-- bound: main_red_tests days=60 rows=200000 bytes=32MB stamp=at
CREATE TABLE main_red_tests(
  reading INTEGER NOT NULL REFERENCES main_readings(id),
  node TEXT NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY (reading, node)
);
CREATE INDEX main_red_tests_at ON main_red_tests(at);
