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
# THE STAMP IS HERE TOO, ONCE (GOH.PROVENTAGHEAD). `goh_stamp_at` is what a build of REV says it was
# built from: the git trees of its build inputs, then the release field. It had three copies (this
# file's readers gates/_goh_bin.sh and scripts/build-goh.sh, and the tests' own trees-only one), and
# the tests' copy left the release field out: on a tagged HEAD every fake binary they stamped read
# as "not built from HEAD's source", and goh's CI went red on every release commit (17 tests,
# v0.126.0). Every shell reader and every test asks this function; build.rs, which cannot source
# it, is pinned to it by tests/test_binary_source_identity.py on a tagged and an untagged HEAD.
#
# Sourced: goh_version_at ROOT [REV], goh_release_at ROOT [REV], goh_stamp_at ROOT REV [RELEASE].
# Sourced: goh_stamp_inputs_at ROOT [REV] too (the inputs, one per line).
# Run: `bash lib/goh_version.sh [--release|--stamp|--inputs] ROOT [REV]` prints the one asked for.

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

# goh_stamp_inputs_at ROOT [REV] -- the build inputs the stamp names, one per line (GOH.STAMPINPUTS):
# the four roots every build reads, then each file outside crates/ that a crate source embeds --
# include_str!/include_bytes!/include! with a literal path, build.rs included -- read from REV's
# own sources, so a new embedded file is an input in the commit that adds it. The goh binary embeds
# lib/busyloop_shapes.json, gates/required_tools.tsv and (goh-tui's build.rs) tui/stylerc; a hand
# list of four left them out, and an edit to one alone left the store's stale build answering.
# Skipped: a test module's embeds (`tests.rs`, `*_tests.rs`, a crate's tests/), compiled only under
# cfg(test) -- tests/test_stamp_inputs.py holds every such file to that -- and a literal that names
# no file at REV (a string that only mentions the macro: nothing is read).
goh_stamp_inputs_at() {
    local root="$1" c="${2:-HEAD}" files found
    printf '%s\n' crates Cargo.toml Cargo.lock rust-toolchain.toml
    local GOH_GIT_LOCAL_VARS="${GOH_GIT_LOCAL_VARS:-$(git rev-parse --local-env-vars 2>/dev/null)}"
    # The sources that name a macro, test modules left out; then each one's text, in one git call.
    files="$(_goh_version_git "$root" grep -l -E 'include(_str|_bytes)?!' "$c" -- 'crates/*.rs' \
        | sed "s|^$c:||" | grep -v -E '(^|/)(tests|[^/]*_tests)\.rs$|/tests/')" || return 0
    found="$(sed "s|.*|$c:& &|" <<<"$files" \
        | _goh_version_git "$root" cat-file --batch='%(objectsize) %(rest)' \
        | env LC_ALL=C awk '
            function emit(file, text,   dir, lit, n, i, p, out, m) {
                dir = file; sub(/\/[^\/]*$/, "", dir)
                while (match(text, /include(_str|_bytes)?![[:space:]]*\([[:space:]]*"[^"]*"/)) {
                    lit = substr(text, RSTART, RLENGTH); text = substr(text, RSTART + RLENGTH)
                    sub(/^[^"]*"/, "", lit); sub(/"$/, "", lit)
                    if (lit ~ /^\//) continue
                    n = split(dir "/" lit, p, "/"); m = 0
                    for (i = 1; i <= n; i++) {
                        if (p[i] == "" || p[i] == ".") continue
                        if (p[i] == "..") { if (m > 0) m--; continue }
                        out[++m] = p[i]
                    }
                    lit = out[1]; for (i = 2; i <= m; i++) lit = lit "/" out[i]
                    if (m > 0 && lit !~ /^crates\//) print lit
                }
            }
            {
                size = $1; file = $2; text = ""; left = size
                while (left > 0 && (getline line) > 0) { text = text " " line; left -= length(line) + 1 }
                if (left == 0) getline line
                emit(file, text)
            }')" || return 0
    [ -n "$found" ] || return 0
    # Only a file REV has is read; a literal naming none is a string that mentions the macro.
    env LC_ALL=C sort -u <<<"$found" | sed "s|.*|$c:& &|" \
        | _goh_version_git "$root" cat-file --batch-check='%(objecttype) %(rest)' \
        | sed -n 's/^blob //p'
}

# goh_stamp_at ROOT REV [RELEASE] -- the git objects of the build inputs at REV (goh_stamp_inputs_at,
# in its order), which crates/goh/build.rs asks this script for, then the release field: REV's own release tag, or RELEASE when given (a build of REV
# as the release it WILL be, scripts/build-goh.sh --as-release). Empty when REV has no such trees (no
# git, no commit yet, a missing input): nothing to compare. A tagged commit's build says its tag, so
# the untagged build of the same trees is not it (GOH.SHIPRELREUSE).
goh_stamp_at() {
    # Captured FIRST, then joined: a revision git cannot resolve is ECHOED to stdout, and under
    # pipefail a failing git inside a pipe killed the gate silently.
    local out f c="${2:-}" rel="${3:-}"
    [ -n "$c" ] || return 0
    local -a specs=()
    while IFS= read -r f; do specs+=("$c:$f"); done < <(goh_stamp_inputs_at "$1" "$c")
    out="$(_goh_version_git "$1" rev-parse "${specs[@]}")" || return 0
    [ -n "$rel" ] || rel="$(goh_release_at "$1" "$c")"
    printf '%s %s' "$out" "$rel" | tr '\n' ' ' | sed 's/ *$//'
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    want=version
    case "${1:-}" in
        --release) want=release; shift ;;
        --stamp) want=stamp; shift ;;
        --inputs) want=stamp_inputs; shift ;;
    esac
    [ -n "${1:-}" ] || { printf 'usage: goh_version.sh [--release|--stamp|--inputs] ROOT [REV]\n' >&2; exit 2; }
    "goh_${want}_at" "$1" "${2:-HEAD}"
fi
