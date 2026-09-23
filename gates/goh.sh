#!/usr/bin/env bash
# goh.sh <check> [args...] — the ONE way a consumer runs a house checker.
#
# The native `goh` binary when one resolves (gates/_goh_bin.sh), else the Python checker it ports,
# with the same arguments: every port is parity-pinned (tests/test_goh_*_parity.py), so the two
# give the same verdict. A repo that calls `python3 $GOH/checks/check_x.py` directly never gets
# the native speed, and one that calls `$GOH/bin/goh` directly has no fallback on a machine
# without cargo; this is both, in one place. The fallback SAYS so once, on stderr.
#
#   goh.sh home-paths --staged --exclude '^tests/fixtures/'
#   goh.sh golden a.png b.png --tolerances '{"ssim_min": 0.99}' --json
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
# shellcheck source=gates/_goh_bin.sh
. "$HERE/_goh_bin.sh"

check="${1:-}"
[ -n "$check" ] || { echo "usage: goh.sh <check> [args...]" >&2; exit 2; }
shift

# check -> the Python file it ports (relative to the checkout).
case "$check" in
    emoji)      python_file="checks/check_no_emoji.py" ;;
    markers)    python_file="checks/check_no_conflict_markers.py" ;;
    length)     python_file="checks/check_file_length.py" ;;
    secrets)    python_file="checks/check_no_secrets.py" ;;
    home-paths) python_file="checks/check_no_home_paths.py" ;;
    no-allow)   python_file="checks/check_no_allow.py" ;;
    screen)     python_file="checks/check_no_screen_presentation.py" ;;
    lints)      python_file="checks/check_lints_optin.py" ;;
    skills)     python_file="checks/check_skills_corpus.py" ;;
    golden)     python_file="lib/golden_core.py" ;;
    *) echo "✗ goh.sh: unknown check '$check'" >&2; exit 2 ;;
esac

# Arguments the Python checker takes and the port does not (yet): such a call runs Python.
case "$check" in
    lints) native_lacks="--staged --self-test" ;;
    *)     native_lacks="" ;;
esac
needs_python=""
for arg in "$@"; do
    for lacked in $native_lacks; do
        [ "$arg" = "$lacked" ] && needs_python="$arg"
    done
done

goh_resolve_native
if [ -n "$goh_native" ] && [ -z "$needs_python" ]; then
    exec "$goh_native" "$check" "$@"
fi
if [ -n "$needs_python" ]; then
    echo "· goh.sh: $check $needs_python is Python-only — running $python_file" >&2
elif [ -n "$goh_native_why" ]; then
    echo "· goh.sh: $goh_native_why — running $python_file" >&2
fi
exec python3 "$ROOT/$python_file" "$@"
