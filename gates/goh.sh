#!/usr/bin/env bash
# goh.sh <check> [args...] — the ONE way a consumer runs a house checker.
#
# The native `goh` binary (gates/_goh_bin.sh), with the same arguments; with no binary it refuses.
# The Python checkers it once fell back to are retired (Phase N3) -- their behaviour is the frozen
# spec in tests/reference_kit.py. A repo that calls `$GOH/bin/goh` directly skips the one binary
# resolution (GOH_BIN, the HEAD-stamp rebuild); this is that, in one place.
#
#   goh.sh home-paths --staged --exclude '^tests/fixtures/'
#   goh.sh golden a.png b.png --tolerances '{"ssim_min": 0.99}' --json
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
# shellcheck source=gates/_goh_bin.sh
. "$HERE/_goh_bin.sh"

check="${1:-}"
[ -n "$check" ] || { echo "usage: goh.sh <check> [args...]" >&2; exit 2; }
shift

# Every check `goh` runs natively. A name not here is refused rather than handed to the binary,
# so a typo is "unknown check", not clap's usage text for some other subcommand.
case "$check" in
    emoji|markers|length|secrets|home-paths|no-allow|empty-assert|screen|lints|skills|golden|\
    deps|unreaped-spawn|version-provenance|kill-by-name|claim-derivation|md-links|lock-version|\
    tag-version|credential-urls|python-formatted|shell-lint|ceiling|commit-class) ;;
    *) echo "✗ goh.sh: unknown check '$check'" >&2; exit 2 ;;
esac

# The self-proof flags of the RETIRED Python checkers (Phase N3). Each proved that Python could go
# red; the native check is proven by gates_of_heck's own suite (tests/, `cargo test`), which runs
# on every push of the binary. Refused by name: passing them to the binary would read as usage.
for arg in "$@"; do
    case "$arg" in
        --probe|--fresh-derivations)
            echo "✗ goh.sh: '$check $arg' was a self-proof of the retired Python checker (Phase N3);" \
                "the native check is proven by gates_of_heck's own test suite" >&2
            exit 2 ;;
    esac
done

goh_resolve_native
if [ -z "$goh_native" ]; then
    # The binary is the only tier: no fallback, which would be a different gate run unannounced.
    echo "✗ goh.sh: no native goh binary -- ${goh_native_why:-it is not built}; build it:" \
        "$ROOT/scripts/build-goh.sh (needs cargo)" >&2
    exit 2  # cannot judge, not a finding: every check's own exit 2
fi
exec "$goh_native" "$check" "$@"
exit
} # parse-guard
