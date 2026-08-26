# gates_of_heck

Quality gates for polyglot repositories, written once and installed everywhere.

Rust, Python, Swift, C++ and Kotlin projects each need the same handful of
things checked, and each ends up growing its own copy: eleven near-identical
emoji checkers, three different names for the same file-length cap, three local
CI scripts with three different output conventions. A fix to one never reaches
the other ten.

This is that set, factored out. It runs **locally** — pre-commit and pre-push —
and assumes no CI server exists.

## Design

Three layers. A check belongs in the highest one that can express it.

| Layer | Scope | Contents |
|---|---|---|
| **1 · structural** | every repo, any language | emoji policy, file-length cap, conflict markers, disk hygiene |
| **2 · per-language** | one runner per toolchain | fmt, lint, tests, coverage |
| **3 · per-repo** | genuinely local only | magic-literal ratchets, golden/pixel contracts, layering rules |

Layer 3 stays small on purpose: when a check appears in a third repo, it
graduates to layer 1.

## The contract every runner keeps

`gates/_common.sh` gives each runner the same five properties. They are not
stylistic:

1. **Exit non-zero on the first failure.** Continuing past a failure buries the
   one line you needed.
2. **Print the failing output.** This is the whole reason `_common.sh` exists.
   The pattern it replaces piped every step to `/dev/null` and printed
   `✗ clippy` — a gate that tells you something is wrong and withholds what is
   a gate you cannot act on.
3. **Kare icons only** (`→ · ✓ ✗ ⚠`). Enforced by `checks/check_no_emoji.py`,
   whose failure message is generated from its own allow-list constant so
   message and policy cannot drift.
4. **`NO_COLOR` and non-tty aware**, via `tui/lib.sh`.
5. **Staged checks measure the index, not the worktree** (`checks/_gitutil.py`
   is the one implementation). A file staged clean then dirtied in an editor
   buffer must not block a commit; a file staged dirty then cleaned must not
   slip through.

Every helper has a fallback definition. A runner whose `die` is undefined does
not abort — under a `while` loop without `set -e` it *continues*, which is how
a lock in one of these repos span forever instead of timing out.

## Use

```bash
# from inside any git repo:
~/Projects/gates_of_heck/install.sh
```

That copies the hooks into `.githooks/`, sets `core.hooksPath`, and writes
starter `tools/gate.sh` + `.gatesrc` if absent (never overwrites). Hooks
delegate to this checkout at runtime (`GOH_DIR` overrides the location), so a
fix here reaches every installed repo with zero re-install steps. There is
exactly ONE copy of every checker.

Each repo's `tools/gate.sh` declares which toolchains the repo contains and
delegates; it holds no gate logic of its own:

```bash
#!/usr/bin/env bash
# --staged : pre-commit, fast, staged files only
# --full   : pre-push, everything
set -euo pipefail
GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"

"$GOH/gates/structural.sh" "$@"

case "$1" in
  --full)
    "$GOH/gates/rust_gate.sh" . crates/
    "$GOH/gates/py_gate.sh"   .
    ./tools/repo_gates.sh          # layer 3
    ;;
esac
```

Then `pre-commit` runs `--staged` and `pre-push` runs `--full`. The hooks are
the enforcement; there is no server backing them up. The documented escape
hatch for a commit that must skip them is `git commit --no-verify`.

## Configuration

Per-repo, in `.gatesrc` at the repo root:

```bash
GOH_MAX_LINES=500              # file-length cap; unset disables it
GOH_EXCLUDE='vendor/|\.generated\.'  # shared vendor/generated exemption (regex)
GOH_LINE_EXCLUDE='third_party/'      # length-only alias for existing repos
GOH_ALLOW='<glyph>'                        # extra permitted glyphs (keep minimal)
GOH_PY_COV_MIN=95
GOH_PY_RUNNER="uv run"         # toolchain prefix (uv, poetry, hatch...)
GOH_SWIFT_MODE=xcode           # or spm
GOH_SWIFT_SCHEME=MyAppTests
GOH_SWIFT_COV_MIN=95
GOH_SWIFT_COLD=0               # default 1: wipe build products before testing
GOH_MAX_SCRATCH_GB=25          # disk-hygiene ceiling
```

## Status

| Component | State |
|---|---|
| `gates/_common.sh` | shared contract (+ `goh_step_in`, argv-safe in-dir steps) |
| `gates/structural.sh` | layer 1 runner |
| `gates/rust_gate.sh` | fmt, clippy `-D warnings`, no `#[allow]` |
| `gates/py_gate.sh` | ruff, pytest, coverage floor |
| `gates/swift_gate.sh` | swiftlint, cold warnings-as-errors build, coverage (`checks/check_swift_coverage.py`) |
| `checks/_gitutil.py` | file listing + index/worktree content truth for all checkers |
| `hooks/` + `install.sh` | delegation hooks; one-command install into any repo |
| `tests/` | pytest harness incl. wiring meta-gate and real-`git commit` hook tests |
| `gates/cpp_gate.sh` | not yet written |
| `gates/kotlin_gate.sh` | not yet written |

## A note on the disk check

`checks/check_disk_hygiene.py` looks out of place next to linters. It is there
because on 2026-08-23 a machine reached 100% of a 926GB disk and a Rust
pre-push gate failed for no reason but lack of space — then passed, unchanged,
once there was room. A gate that fails for a reason unrelated to the diff is the
most expensive kind, because the obvious next move is to debug the diff.

The two causes were a test fixture writing a real 15GiB file per run (26
retained pytest roots, 246GB) and 75 abandoned Xcode `-derivedDataPath` trees
(30GB). Neither lived in any project directory, so every repo looked clean.
Nothing was watching where the bytes actually went.

It reports a ceiling and never deletes anything. Clearing someone's scratch
mid-session is worse than telling them about it, and the writer matters more
than the bytes: scratch you delete without finding the writer is back tomorrow.

## License

MIT — see [LICENSE](LICENSE).
