-- old-writers: safe
-- goh.db 0028 (GOH.SUITETOTAL): the commit a suite run tested (`head`, `git rev-parse HEAD` of the
-- tree it ran, NULL when the tree was not its commit), so each file's CPU is known per landed
-- change: a land's pre-step runs the tip that lands (lib/test_work.py `per_file`, `slow_files`).
ALTER TABLE test_work_runs ADD COLUMN head TEXT;
CREATE INDEX test_work_runs_head ON test_work_runs(head);
