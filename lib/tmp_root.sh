# shellcheck shell=bash
# Sourced. goh_tmp: the ONE parent of every temp root goh makes, `$TMPDIR/goh.noindex` (GOH.TMPNOINDEX).
#
# THE CASE (2026-11-17, EST.MDSCRAWL). 30 s of fs_usage during gates: 94% (337/359) of the paths
# Spotlight's mds resolved were goh pytest temp trees under $TMPDIR, half of them fixture
# .git/objects; mds read ~730 fsevents/s and mds_stores walked them (2833 fchdir in 30 s). Spotlight
# never indexes a directory whose name ends in `.noindex`, so every root lives under one that does.
# A TMPDIR that IS a goh.noindex is used as it is; one below it nests a second, which costs nothing.
# `tests/tmp_root_lint.py` refuses a mktemp in gates/, lib/ or tools/ that bypasses this helper.
# NOT NEEDED UNDER ~/.cache (GOH.CACHENOINDEX, measured 2026-11-23): the push worktrees
# (~/.cache/goh/push) and HEAD exports (~/.cache/goh/head) live in a dot directory, which Spotlight
# does not index. A probe file written there and a control in ~/Downloads at the same second
# (04:05:53): at 04:18:26 `mdfind -name` found the control and not the ~/.cache one (an earlier
# pair, checked after 3 min, read the same). Re-measure that way before
# moving either root; an XDG_CACHE_HOME outside a dot directory would be indexed.
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/tmp_root.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
goh_tmp() {
    local base="${TMPDIR:-/tmp}"
    base="${base%/}"
    [ "${base##*/}" = goh.noindex ] || base="$base/goh.noindex"
    [ -d "$base" ] || mkdir -p "$base" || return 1
    printf '%s\n' "$base"
}
