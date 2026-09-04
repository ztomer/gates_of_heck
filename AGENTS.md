# AGENTS.md — working in gates_of_heck

One checkout, many consumers. Hooks delegate here at runtime via `GOH_DIR`
(default `~/Projects/gates_of_heck`), so a fix here reaches every installed
repo with zero re-installs. Never vendor copies of checkers or TUI libs.

## Commands

```
tools/gate.sh --staged     # layer 1 only (pre-commit scope, fast)
tools/gate.sh --full       # every layer (pre-push scope; runs pytest suite)
python3 -m pytest tests/ -q -n 8 --dist loadgroup  # parallel; needs pytest-xdist
python3 -m pytest tests/test_X.py -q   # one area
gates/structural.sh --staged | --full  # direct structural run
```

Pre-push runs `tools/gate.sh --full`. Escape hatch: `git commit --no-verify`.

## Layout

* `gates/` — runners (bash). `structural.sh` is layer 1 (every repo);
  `py_gate.sh`, `rust_gate.sh`, `swift_gate.sh`, `coverage_gate.sh`,
  `local_ci.sh` are opt-in per-repo layers. `_common.sh` is sourced, never run.
* `checks/` — checkers (python, one `nm` shell script). Called by gates.
* `lib/` — shared libs: `golden_core.py`, `mcp_scaffold.py`,
  `eval_transport.py`, `headless_env.sh`, `killtree.py`, `desktop_lock/`.
* `tui/` — output style source of truth (`stylerc`, `lib.sh`, `lib.py`).
* `tools/` — `gate.sh` (this repo's own gate entry), `release-kit/`, `profiling/`.
* `hooks/` — stock `pre-commit` / `pre-push` installed by `install.sh`.
* `docs/` — `map.md` (script inventory), `config.md` (every `GOH_*` key),
  `contracts.md` (load-bearing invariants), `new-checker.md` (add a check),
  `harnesses.md` (screen + golden cookbook).
* `tests/` — pytest suite; `conftest.py` has fixture-repo builders.
  `test_wiring.py` is the meta-gate (referenced scripts must exist;
  config keys must be documented).

Details: `docs/map.md`.

## Rules

* Emoji are a failure state. Only `→ · ✓ ✗ ⚠ ↔ ↑ ↓ ← ⌘ ⌥ ⌨ ⇧ ⌃ ⏎ ⎋ ↵ ⇒ ⇄`
  plus `© ® ™` (bare forms; VS16 forms fail). Enforced by
  `checks/check_no_emoji.py`. See `docs/config.md` for `GOH_ALLOW`.
* 500-line cap (`GOH_MAX_LINES`). Keep new docs/code under it.
* Output style: source `tui/lib.sh`, use `info/ok/err/warn/die/section/hr`.
  Never hand-roll color escapes or `[ PASS ]` markers.
* 64-bit only; macOS is Apple silicon only, Linux x86_64 stays supported.
  Unsupported OS/arch is a hard failure, never a warn-and-build fallback.
* Staged checks read the git index (`git show :path` via `checks/_gitutil.py`),
  not the worktree.
* Fail fast, print the failing output (that is what `_common.sh` exists for).
* Config: all `GOH_*` keys documented in `docs/config.md` + `.gatesrc.example`.
  Adding a key without documenting it fails `test_config_schema_covers_keys`.
* Contracts in `docs/contracts.md` each name their pinning test. Do not
  "fix" a contract without updating its test and the doc.
* New checker? Follow `docs/new-checker.md` (test-first, red-proven both
  directions).

## Config

Target-repo settings live in `<repo>/.gatesrc` (this repo's own: `.gatesrc`).
Full key reference: `docs/config.md`. Starter template: `.gatesrc.example`
(consumers get it via `install.sh`; never overwrite an existing file).
