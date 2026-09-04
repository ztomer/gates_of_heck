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
| **1. Structural** | All repos, any language | Emoji policy, 500-line cap, conflict markers, disk hygiene |
| **2. Language** | Per toolchain | Formatter, linter, tests, coverage floors |
| **3. Repo** | Single project | Magic-literal ratchets, golden/pixel diffs, local rules |

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

case "${1:-}" in
  --full)
    "$GOH/gates/rust_gate.sh" . crates/
    "$GOH/gates/py_gate.sh"   .
    # ./tools/repo_gates.sh   # Layer 3
    ;;
esac
```

## Configuration (`.gatesrc`)

Put repo settings in `.gatesrc`:

```bash
# Structural
GOH_MAX_LINES=500                    # File length cap (unset disables)
GOH_EXCLUDE='vendor/|\.generated\.'  # Regex of paths to ignore
GOH_ALLOW=''                         # Extra allowed glyphs if needed
GOH_MAX_SCRATCH_GB=25                # Scratch directory size ceiling
GOH_MAX_CACHE_GB=50                  # Cargo cache size ceiling

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
- `gates/local_ci.sh`: Runs a list of steps locally and preserves log files on failure.
- `gates/coverage_gate.sh`: Multi-language coverage gate with per-target/per-file floors and marker ceilings.

## Escape Hatch

Skip hooks when needed:

```bash
git commit --no-verify
```

## License

MIT
