#!/usr/bin/env bash
# install.sh — wire gates_of_heck into a repository.
#
#   install.sh              # target = $PWD (must be a git repo)
#   install.sh ~/proj/x     # target = that repo
#   GOH_DIR=~/goh install.sh   # shared checkout lives somewhere else
#
# What it does, idempotently:
#   1. copies hooks/pre-commit + hooks/pre-push into <repo>/.githooks/,
#      PROTECTED: the sha256 of each installed hook is recorded in
#      .githooks/.goh-installed/<name>.sha256, and a reinstall overwrites a
#      hook only if its current bytes equal the stock copy OR our recorded
#      install-time hash. A locally extended/replaced hook is refused BY NAME
#      unless --force is passed.
#   2. git config core.hooksPath .githooks
#   3. writes starter tools/gate.sh and .gatesrc if absent (never overwrites)
#
# It does NOT copy the checkers: hooks delegate to this checkout at runtime,
# so a fix here reaches every installed repo with zero re-install steps.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/tui/lib.sh"

FORCE=0
if [ "${1:-}" = "--force" ]; then
    FORCE=1
    shift
fi

target="${1:-$PWD}"
mkdir -p "$target"
target="$(cd "$target" && pwd)"

git -C "$target" rev-parse --show-toplevel >/dev/null 2>&1 \
    || die "$target is not inside a git repo"

# sha256 digest of a file's contents, via whichever tool exists. All three
# produce the same SHA-256 digest, so records stay comparable across the chain.
hash_hex() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif cksum -a sha256 /dev/null >/dev/null 2>&1; then
        cksum -a sha256 "$1" | cut -d' ' -f1
    else
        return 1
    fi
}

hooks_dir="$target/.githooks"
rec_dir="$hooks_dir/.goh-installed"
mkdir -p "$hooks_dir" "$rec_dir"
for h in pre-commit pre-push; do
    dst="$hooks_dir/$h"
    stock_hash="$(hash_hex "$HERE/hooks/$h")" \
        || die "no sha256 tool found (need shasum, sha256sum, or cksum -a sha256)"
    if [ -f "$dst" ]; then
        cur_hash="$(hash_hex "$dst")" || die "no sha256 tool found for hashing $dst"
        recorded_hash="$(cat "$rec_dir/$h.sha256" 2>/dev/null || true)"
        # Overwrite ONLY when the target is pristine: identical to the stock
        # hook, or identical to what WE last installed (stock bumped since).
        # Anything else was edited locally (repos append stages to pre-push,
        # replace pre-commit outright) and is not ours to clobber silently.
        if [ "$cur_hash" != "$stock_hash" ] && [ "$cur_hash" != "$recorded_hash" ] \
           && [ "$FORCE" -ne 1 ]; then
            err "install: $dst differs from both the stock hook and our install record"
            err "  it looks locally modified — refusing to overwrite it"
            err "  pass --force to replace it with the stock hook anyway"
            exit 1
        fi
    fi
    cp "$HERE/hooks/$h" "$dst"
    chmod +x "$dst"
    hash_hex "$dst" >"$rec_dir/$h.sha256"
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
