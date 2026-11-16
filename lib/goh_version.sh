# shellcheck shell=bash
# goh_version.sh -- the ONE source of goh's version: the release TAG, never a number in a file
# (GOH.SHIPRELREUSE).
#
# Until v0.124.0 the version lived in Cargo.toml's [workspace.package] and every release commit
# bumped it. CARGO_PKG_VERSION is compiled into every crate, so the release commit changed what the
# 578 s Rust coverage step reads, and the land verdict of the tip it was cut from could not stand
# for it. Now Cargo.toml's version is a fixed placeholder nobody reads, the release commit changes
# only CHANGELOG.md and the changelog/ fragments it folds, and the version is what git says:
#
#   a commit carrying the tag vX.Y.Z           -> X.Y.Z                 (a release)
#   N commits past the newest reachable vX.Y.Z -> X.Y.Z-N-g<sha>        (git describe: what it is)
#   no such tag reachable, or no git at all    -> unknown               (never a made-up release)
#
# Readers, each asking here: crates/goh/build.rs (GOH_VERSION, `goh --version`, gohpin's own
# release; the stamp's release field), scripts/build-goh.sh (passes both to a build from an export,
# which has no .git), gates/_goh_bin.sh (goh_stamp_at's release field) and gates/structural.sh
# (goh_require_current). A tagged commit's stamp carries ` vX.Y.Z`, so its build is never the
# untagged build of the same trees. Pinned by tests/test_goh_version.py.
#
# Sourced: goh_version_at ROOT [REV], goh_release_at ROOT [REV]. Run: `bash lib/goh_version.sh
# [--release] ROOT [REV]` prints the one asked for.

_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

# The tags that are releases: v + three numbers. `--match` is a glob, so the shape is checked again.
_goh_version_git() { # <root> <git args...> -- no hook repository variables (contract #12)
    local root="$1"; shift
    (
        # shellcheck disable=SC2086  # word-splitting git's variable list is the point
        unset ${GOH_GIT_LOCAL_VARS:-$(git rev-parse --local-env-vars 2>/dev/null)}
        GIT_OPTIONAL_LOCKS=0 git -C "$root" "$@" 2>/dev/null
    )
}

# goh_release_at ROOT [REV] -- `vX.Y.Z` when REV carries that release tag, else nothing.
goh_release_at() {
    local t
    t="$(_goh_version_git "$1" describe --tags --exact-match --match 'v[0-9]*.[0-9]*.[0-9]*' \
        "${2:-HEAD}")" || return 0
    [[ "$t" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && printf '%s\n' "$t"
    return 0
}

# goh_version_at ROOT [REV] -- the version a build of REV reports (the table above).
goh_version_at() {
    local t
    t="$(goh_release_at "$1" "${2:-HEAD}")"
    if [ -n "$t" ]; then
        printf '%s\n' "${t#v}"
        return 0
    fi
    t="$(_goh_version_git "$1" describe --tags --long --abbrev=10 --match 'v[0-9]*.[0-9]*.[0-9]*' \
        "${2:-HEAD}")" || t=""
    if [[ "$t" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-g[0-9a-f]+$ ]]; then
        printf '%s\n' "${t#v}"
    else
        printf 'unknown\n'
    fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    want=version
    [ "${1:-}" = "--release" ] && { want=release; shift; }
    [ -n "${1:-}" ] || { printf 'usage: goh_version.sh [--release] ROOT [REV]\n' >&2; exit 2; }
    "goh_${want}_at" "$1" "${2:-HEAD}"
fi
