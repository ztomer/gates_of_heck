# docs/new-checker.md — add a check, test-first

## 1. Decide where it lives

* New policy over file contents or repo state: `checks/check_<name>.py`
  (or `.sh` for link-table style audits like `check_no_screen_linkage.sh`).
* New runner/plumbing over toolchains: `gates/<name>.sh` (rare; prefer a
  checker under an existing gate).
* Shared math/transport both sides use: `lib/` (comparison only — see the
  golden rule in `docs/contracts.md`: no bless/update flags in shared code).

## 2. Write the failing test first

Helpers live in `tests/conftest.py`: `repo` fixture (throwaway git repo),
`write` / `stage` / `commit_all`, `run_check` (checker, `cwd=repo`),
`run_gate` (gate script, `cwd=repo`).

* Build disallowed content with `chr(0x...)`, never as a literal — else
  this repo's own emoji gate trips on your test (see `conftest.py:1-28`).
* Prove the test red BOTH directions: (a) violating fixture fails with
  the violation named as `file:line:`; (b) clean fixture passes. Break
  the code once and watch it go red before trusting green.
* If the checker has a `--staged` mode, test staged-vs-worktree: stage
  the violation, dirty the worktree differently, assert the checker
  reports the INDEX via `checks/_gitutil.py` (`git show :path`).

## 3. Implement the checker

* CLI: `--staged` flag when it polices commits; `--exclude RE` when it
  walks trees (wired from `GOH_EXCLUDE` by the calling gate, never
  hardcoded per-repo policy in shared code).
* Exit codes: `0` pass, `1` violation found, `2` usage/config error
  (missing binary, unparsable payload, zero measurable files — never a
  silent pass; see `docs/contracts.md` 7).
* Output: name every violation as `file:line: reason`; print OK-summary
  on pass (`[name] OK — N files clean`). Source `tui/lib.sh` in shell
  checkers; use `info/ok/err/warn/die`.
* Keep it under the 500-line cap.

## 4. Wire it

* Call it from the owning gate with `goh_step` (fail-fast + output) or
  `goh_optional_step` when it applies only with a guard file.
* `tests/test_wiring.py` parses `gates/*.sh` + `hooks/` for referenced
  scripts: guarded references must still resolve. A typo'd path fails
  the suite — that is the point.
* New `GOH_*` key? Document it in `docs/config.md` (+ `.gatesrc.example`
  when consumer-facing) or `test_config_schema_covers_keys` goes red.
* New invariant? Add a row to `docs/contracts.md` naming the pinning test.
* List the checker in `docs/map.md`.

## 5. Verify

```
python3 -m pytest tests/test_<name>.py tests/test_wiring.py tests/test_config_schema.py -q
tools/gate.sh --staged
```
Then the full suite (`python3 -m pytest tests/ -q -n 8 --dist loadgroup`,
~1 min parallel) before push — pre-push runs it anyway.
