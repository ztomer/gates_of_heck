```text
  _   _ _________________________________________________________________ _   _
 / \ / \                                                                / \ / \
|   X   |                  G A T E S   O F   H E C K                  |   X   |
 \_/ \_/________________________________________________________________\_/ \_/
  :   :                                                                   :   :
  :   : ____    _  _____ _____ ____    ___  _____  _   _ _____ ____ _  __ :   :
  :   :/ ___|  / \|_   _| ____/ ___|  / _ \|  ___|| | | | ____/ ___| |/ / :   :
  :   :| |  _  / _ \ | | |  _| \___ \ | | | | |_   | |_| |  _|| |   | ' / :   :
  :   :| |_| |/ ___ \| | | |___ ___) || |_| |  _|  |  _  | |__| |___| . \ :   :
  :   : \____/_/   \_\_| |_____|____/  \___/|_|    |_| |_|_____\____|_|\_\:   :
  :   :                                                                   :   :
  :   :                [ PRE-COMMIT * PRE-PUSH * ZERO CI ]                :   :
  :   :                                                                   :   :
 _.___._ ________________________________________________________________.___._
 /   \                                                                   /   \ 
| NFO |              polyglot quality gates - local & fast              | NFO |
 \___/___________________________________________________________________\___/ 
```

# gates_of_heck

Shared quality gates for Rust, Python, Swift, C++, and Kotlin projects. Runs locally via git hooks (`pre-commit` and `pre-push`) so you don't need a CI server.

> Working here as an agent? Start at `AGENTS.md` (commands, layout, rules).
> Script inventory: `docs/map.md`. Every `GOH_*` key: `docs/config.md`.
> Load-bearing invariants: `docs/contracts.md`. Adding a check:
> `docs/new-checker.md`.

## Install

Run from inside any git repository:

```bash
~/Projects/gates_of_heck/install.sh
```

This sets up `.githooks/` to delegate to this checkout. If `tools/gate.sh` or `.gatesrc` don't exist in the target repo, it creates starter templates.

## Structure

Checks run in three layers:

| Layer | Scope | Checks |
|---|---|---|
| **1. Structural** | All repos, any language | Emoji policy, 500-line cap, conflict markers, shell lint, no committed secrets |
| **2. Language** | Per toolchain | Formatter, linter, tests, coverage floors |
| **3. Repo** | Single project | Magic-literal ratchets, golden/pixel diffs, local rules |

## The native binary (`goh`)

Layer 1 is a static Rust binary, `crates/goh`, with the emoji, conflict-marker,
file-length and secrets scanners native and the remaining checkers delegated
to the same Python files. `install.sh` builds it to `bin/goh` (gitignored) when
`cargo` is present; `gates/structural.sh` execs it when it is there and runs
the Python checkers — saying so once — when it is not, so a machine without a
toolchain still gets every gate. `tests/test_goh_*_parity.py` pin the two
paths to identical verdicts, step for step. Resolution: `GOH_BIN` (an explicit
pointer at nothing is reported, never silently replaced), then `bin/goh`, then
`goh` on `PATH`; `GOH_NO_NATIVE=1` forces the Python path.

Platform gate (`scripts/build-goh.sh`, also inside the binary): 64-bit only,
macOS is Apple silicon only, Linux keeps x86_64 and aarch64. Unsupported
combinations are a hard failure with the reason, never a warn-and-build.

Scope, both paths: `--staged` polices the index; full scope polices the
**worktree** — tracked plus untracked-but-not-ignored files — so a brand-new
oversized or emoji-bearing file fails `--full` before it is staged, not only
at pre-commit.

## Rules

- **Fail fast**: Stops on the first failure.
- **Show output**: Prints the actual error output instead of hiding it.
- **Clean output**: Uses standard icons (`→ · ✓ ✗ ⚠`) and respects `NO_COLOR`.
- **Index truth**: Staged checks inspect what's in the git index (`git show :path`), not dirty editor buffers.

## Repo Setup (`tools/gate.sh`)

Each repository wires its own toolchains:

```bash
#!/usr/bin/env bash
# --staged : pre-commit (fast, staged files only)
# --full   : pre-push (all checks, tests, coverage)
set -euo pipefail
GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"

"$GOH/gates/structural.sh" "$@"
"$GOH/gates/py_staged.sh" .      # ruff on the staged .py at every commit

case "${1:-}" in
  --full)
    "$GOH/gates/rust_gate.sh" . crates/
    "$GOH/gates/py_gate.sh"   .
    # ./tools/repo_gates.sh   # Layer 3
    ;;
esac
```

## Configuration (`.gatesrc`)

Put repo settings in `.gatesrc`. Full schema: `docs/config.md`;
commented starter with every key: `.gatesrc.example`.

```bash
# Structural
GOH_MAX_LINES=500                    # File length cap (unset disables)
GOH_EXCLUDE='vendor/|\.generated\.'  # Regex of paths to ignore
GOH_ALLOW=''                         # Extra allowed glyphs if needed

Machine disk watch lives outside the gates since v0.8.0:
`~/Projects/scripts/bin/disk_hygiene.sh` (watch) +
`reclaim_build_space.sh` (fix). Its ceilings (`GOH_MAX_SCRATCH_GB`,
`GOH_MAX_CACHE_GB`, `GOH_MIN_FREE_GB`, `GOH_WATCH_PATHS`,
`GOH_SCRATCH_ROOTS`) are documented in `docs/config.md`.

# Python
GOH_PY_COV_MIN=95                    # Coverage minimum (%)
GOH_PY_RUNNER="uv run"               # Test runner command prefix

# Swift
GOH_SWIFT_MODE=xcode                 # 'xcode' or 'spm'
GOH_SWIFT_SCHEME=MyAppTests          # Scheme name for Xcode
GOH_SWIFT_COV_MIN=95                 # Coverage minimum (%)
GOH_SWIFT_COLD=1                     # Wipe build artifacts before testing
```

## Extra Tools

- `tools/release-kit/release.sh`: Checks gate and changelog, creates an annotated tag, pushes, and creates a GitHub release.
- `gates/local_ci.sh`: Runs a list of steps locally and preserves log files on failure. A step already proven on the same clean tree is skipped (see below).
- `gates/proven.sh`: Runs one step unless it already passed on this exact tree.

## Proven steps

A commit used to pay for its expensive steps twice before CI saw it: once in the repo's
pre-commit hook and again at push, over the same tree. `local_ci.sh` now records every step
that passes on a clean tree (working tree == index) and skips it on a later run over the same
tree, printing who proved it and how long ago. A repo hook opts its own copy of a step in by
running it through `proven.sh` with the step string spelled exactly as in `GOH_CI_STEPS`:

```bash
"$GOH/gates/proven.sh" --label pre-commit --log "$log" -- 'bash tools/coverage_check.sh'
```

The key is the tree, the step string, the gates checkout (HEAD, diff, untracked files), the
toolchain versions and a few environment variables; a dirty tree has no key, so nothing is
recorded or skipped. Records live in the repo's common git dir, so `push_gate.sh`'s clean
worktree sees them. CI never does -- it stays the independent check. `GOH_PROVEN=0` disables
the cache; `GOH_PROVEN_TTL_S` (default 24 h) bounds a record's life. The full rationale is the
header of `gates/proven.sh`.
- `gates/coverage_gate.sh`: Multi-language coverage gate with per-target/per-file floors and marker ceilings.
- `gates/doctor.sh`: Diagnoses gate wiring for a repo (also via `tools/gate.sh --doctor`).

## Escape Hatch

Skip hooks when needed:

```bash
git commit --no-verify
```

## License

MIT
