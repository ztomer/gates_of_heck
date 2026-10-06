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

# check -> the Python file it ports (relative to the checkout).
case "$check" in
    emoji)      python_file="checks/check_no_emoji.py" ;;
    markers)    python_file="checks/check_no_conflict_markers.py" ;;
    length)     python_file="checks/check_file_length.py" ;;
    secrets)    python_file="checks/check_no_secrets.py" ;;
    home-paths) python_file="checks/check_no_home_paths.py" ;;
    no-allow)   python_file="checks/check_no_allow.py" ;;
    empty-assert) python_file="checks/check_no_empty_assert.py" ;;
    screen)     python_file="checks/check_no_screen_presentation.py" ;;
    lints)      python_file="checks/check_lints_optin.py" ;;
    skills)     python_file="checks/check_skills_corpus.py" ;;
    golden)     python_file="lib/golden_core.py" ;;
    deps)       python_file="checks/check_dep_currency.py" ;;
    unreaped-spawn) python_file="checks/check_no_unreaped_spawn.py" ;;
    version-provenance) python_file="checks/check_version_provenance.py" ;;
    kill-by-name) python_file="checks/check_no_kill_by_name.py" ;;
    claim-derivation) python_file="checks/check_claim_derivation.py" ;;
    md-links) python_file="checks/check_md_links.py" ;;
    lock-version) python_file="checks/check_lock_version.py" ;;
    tag-version) python_file="checks/check_tag_version.py" ;;
    credential-urls) python_file="checks/check_no_credential_urls.py" ;;
    python-formatted) python_file="checks/check_python_formatted.py" ;;
    shell-lint) python_file="checks/check_shell_lint.sh" ;;
    *) echo "✗ goh.sh: unknown check '$check'" >&2; exit 2 ;;
esac

# Arguments the Python checker takes and the port does not (yet): such a call runs Python.
case "$check" in
    unreaped-spawn) native_lacks="--probe --fresh-derivations" ;;
    deps) native_lacks="--probe" ;;
    empty-assert) native_lacks="--probe" ;;
    version-provenance) native_lacks="--probe" ;;
    claim-derivation) native_lacks="--probe" ;;
    md-links) native_lacks="--probe" ;;
    lock-version) native_lacks="--probe" ;;
    tag-version) native_lacks="--probe" ;;
    credential-urls) native_lacks="--probe" ;;
    python-formatted) native_lacks="--selftest" ;;
    *)     native_lacks="" ;;
esac
# Every check has a native port (Phase N1). A check the binary cannot run would name its reason
# here and exec the reference instead.
python_only=""
needs_python=""
for arg in "$@"; do
    for lacked in $native_lacks; do
        [ "$arg" = "$lacked" ] && needs_python="$arg"
    done
done

goh_resolve_native
if [ -n "$python_only" ]; then
    echo "· goh.sh: $check is Python-only — $python_only" >&2
    exec python3 "$ROOT/$python_file" "$@"
fi
if [ -z "$needs_python" ]; then
    if [ -z "$goh_native" ]; then
        # The Python tier is retired (Phase N3): no fallback to the reference, which would be a
        # different gate run without saying so.
        echo "✗ goh.sh: no native goh binary -- ${goh_native_why:-it is not built}; build it:" \
            "$ROOT/scripts/build-goh.sh (needs cargo)" >&2
        exit 2  # cannot judge, not a finding: every check's own exit 2
    fi
    exec "$goh_native" "$check" "$@"
fi
# A flag only the reference implements (a self-proof): the reference runs it, and says so.
echo "· goh.sh: $check $needs_python is Python-only — running $python_file" >&2
# The reference's own interpreter: a `.sh` port's reference is a bash script (`shell-lint`).
case "$python_file" in
    *.sh) exec bash "$ROOT/$python_file" "$@" ;;
esac
exec python3 "$ROOT/$python_file" "$@"
exit
} # parse-guard
