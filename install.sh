#!/usr/bin/env bash
# install.sh — wire gates_of_heck into a repository.
#
#   install.sh              # target = $PWD (must be a git repo)
#   install.sh ~/proj/x     # target = that repo
#   GOH_DIR=~/goh install.sh   # shared checkout lives somewhere else
#
# What it does, idempotently:
#   1. copies hooks/pre-commit + hooks/pre-push into <repo>/.githooks/
#   2. git config core.hooksPath .githooks
#   3. writes starter tools/gate.sh and .gatesrc if absent (never overwrites)
#
# It does NOT copy the checkers: hooks delegate to this checkout at runtime,
# so a fix here reaches every installed repo with zero re-install steps.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/tui/lib.sh"

target="${1:-$PWD}"
mkdir -p "$target"
target="$(cd "$target" && pwd)"

git -C "$target" rev-parse --show-toplevel >/dev/null 2>&1 \
    || die "$target is not inside a git repo"

hooks_dir="$target/.githooks"
mkdir -p "$hooks_dir"
for h in pre-commit pre-push; do
    cp "$HERE/hooks/$h" "$hooks_dir/$h"
    chmod +x "$hooks_dir/$h"
done

git -C "$target" config core.hooksPath .githooks

if [ ! -f "$target/tools/gate.sh" ]; then
    mkdir -p "$target/tools"
    cat >"$target/tools/gate.sh" <<'GATE'
#!/usr/bin/env bash
# Per-repo gate entry point. Declares which toolchains this repo contains and
# delegates; it holds no gate logic of its own.
#   --staged : pre-commit scope (fast) — layer 1 only
#   --full   : pre-push scope — every layer
set -euo pipefail
GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"

"$GOH/gates/structural.sh" "$@"

case "${1:-}" in
  --full)
    # Add per-language layers for what this repo actually contains:
    #   "$GOH/gates/rust_gate.sh"  .
    #   "$GOH/gates/py_gate.sh"    .
    #   "$GOH/gates/swift_gate.sh" .
    # Layer 3 (genuinely local checks): create ./tools/repo_gates.sh and
    # uncomment:
    #   ./tools/repo_gates.sh
    ;;
esac
GATE
    chmod +x "$target/tools/gate.sh"
    info "wrote tools/gate.sh (uncomment the layers this repo needs)"
fi

if [ ! -f "$target/.gatesrc" ]; then
    cat >"$target/.gatesrc" <<'SRC'
# gates_of_heck configuration. All keys optional; delete what you don't use.
GOH_MAX_LINES=500                 # file-length cap; unset disables the check
# GOH_EXCLUDE='vendor/|\.generated\.'   # shared vendor/generated exemption
# GOH_ALLOW='<glyph>'                          # extra permitted characters (keep minimal)
# GOH_LINE_EXCLUDE='third_party/'       # length-only alias
# GOH_PY_COV_MIN=95
# GOH_SWIFT_MODE=xcode            # or spm
# GOH_SWIFT_SCHEME=MyAppTests
# GOH_SWIFT_COV_MIN=95
# GOH_MAX_SCRATCH_GB=25           # disk-hygiene ceiling (full runs only)
SRC
    info "wrote .gatesrc"
fi

ok "installed into $target (core.hooksPath → .githooks)"
info "pre-commit runs structural --staged; pre-push runs tools/gate.sh --full"
