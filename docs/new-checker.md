# docs/new-checker.md — add a check, test-first

## 1. Decide where it lives

* New structural policy over file contents or repo state: a NATIVE check,
  `crates/goh/src/<name>.rs` (a `Commands` variant in `cli.rs`, dispatched in
  `main.rs`, a step in `structural.rs` if every repo runs it, and a name in
  `gates/goh.sh`'s list). The Python tier is retired (Phase N3): a new
  structural checker in `checks/` is a second tier nobody runs.
* A gate-side tool a consumer runs with arguments (`check_baseline_ratchet.py`,
  `check_display_seam.py`) may still be Python in `checks/`; say why in its
  `docs/map.md` row.
* New runner/plumbing over toolchains: `gates/<name>.sh` (rare; prefer a
  checker under an existing gate).
* Shared math/transport both sides use: `lib/` (comparison only — see the
  golden rule in `docs/contracts.md`: no bless/update flags in shared code).

## 2. Write the failing test first

Helpers live in `tests/conftest.py`: `repo` fixture (throwaway git repo),
`write` / `stage` / `commit_all`, `goh` (the session's native binary) and
`run_goh(repo, check, *args)`, `run_check` (a Python checker, `cwd=repo`),
`run_gate` (gate script, `cwd=repo`). Rust unit tables go beside the code
(`#[cfg(test)]`), whole-repo behaviour in `tests/test_check_<name>.py`.

* Build disallowed content with `chr(0x...)`, never as a literal — else
  this repo's own emoji gate trips on your test (see the glyph constants
  at the top of `conftest.py`).
* Prove the test red BOTH directions: (a) violating fixture fails with
  the violation named as `file:line:`; (b) clean fixture passes. Break
  the code once and watch it go red before trusting green.
* If the checker has a `--staged` mode, test staged-vs-worktree: stage
  the violation, dirty the worktree differently, assert the checker
  reports the INDEX (`crates/goh/src/blobs.rs`, `git show :path`).
* A check that builds its OWN repo — a skeleton, a fixture — runs every git
  call on it, and every process inside it, with `goh_testkit::git_command()`
  (`_gitutil.foreign_repo_env()` in Python). Inheriting a linked worktree hook's
  `GIT_DIR` re-initialises the real repo (contract #12).

## 3. Implement the checker

* CLI: `--staged` flag when it polices commits; `--exclude RE` when it
  walks trees, as `Option<String>` resolved through `gatesrc::declared_exclude`
  in `main.rs` (unset reads the repo's own `GOH_EXCLUDE`, so a direct call
  judges a repo as its gate does; never hardcoded per-repo policy in shared code) -- UNLESS it is a security
  check. `GOH_EXCLUDE` exempts what is not ours to re-style; a leak is not
  a style, so a credential scanner takes no exclusion at all (`goh secrets`),
  and its one escape is a reasoned per-line marker. Add the checker's name to
  the `GOH_EXCLUDE` row of `docs/config.md` when it honours the key
  (`tests/test_exclude_scope_doc.py` holds the two lists equal).
* Exit codes: `0` pass, `1` violation found, `2` usage/config error
  (missing binary, unparsable payload, zero measurable files — never a
  silent pass; see `docs/contracts.md` 7).
* Output: name every violation as `file:line: reason`; print OK-summary
  on pass (`[name] OK — N files clean`). Kare icons only (`✓ ✗ ⚠ → ·`).
* `cargo clippy --all-targets -- -D warnings` with pedantic + nursery; no
  `#[allow]` / `#[expect]` (`goh no-allow` enforces it on this crate too).
* Keep each file under the 500-line cap; split into a module directory.

## 4. Wire it

* A structural step: add it to `structural.rs` (and `INVENTORY` in
  `tests/test_goh_structural.py`, with a red and a green case in its `EXPECTED`).
  A toolchain gate calls it as `bash "$GOH/gates/goh.sh" <check>` from
  `goh_step` (fail-fast + output).
* `tests/test_wiring.py` parses `gates/*.sh` + `hooks/` for referenced
  scripts: guarded references must still resolve. A typo'd path fails
  the suite — that is the point.
* New `GOH_*` key? Document it in `docs/config.md` (+ `.gatesrc.example`
  when consumer-facing) or `test_config_schema_covers_keys` goes red.
* New invariant? Add a row to `docs/contracts.md` naming the pinning test.
* List the checker in `docs/map.md`.

## 5. Verify

```
cargo clippy --all-targets -q -- -D warnings && cargo test -q -p goh
python3 -m pytest tests/test_<name>.py tests/test_wiring.py tests/test_config_schema.py -q
tools/gate.sh --staged
```
Then the full suite (`python3 -m pytest tests/ -q -n 12 --dist loadgroup`,
~1 min parallel) before push — pre-push runs it anyway.
