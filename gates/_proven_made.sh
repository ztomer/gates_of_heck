# shellcheck shell=bash
# _proven_made.sh -- what a proven step MADE, measured, and the hits that owe it (GOH.PROVENBUILD).
# Sourced by gates/_proven_artifacts.sh (gates/_proven.sh sits at the 500-line cap); its callers are
# gates/local_ci.sh and gates/proven.sh. Pinned by tests/test_proven_made.py.
#
# THE CLASS (CadGoose2, 2026-11-11, CG2.PUSHGATE). An earlier push of the same commit proved
# `bash scripts/build.sh Release` and failed later, so the next push's fresh export SKIPPED the
# build, had no build/ or libMLXBridge.a, and 10 of 54 later steps failed. A proof says a step
# PASSED on a tree; it does not put the step's outputs into a new export. proven_undeclared_build
# knows a build only by its tool's name, so every repo building through a wrapper script was
# exposed until it hand-declared `produces=`.
#
# THE MEASUREMENT. The ignored entries present after a step's real run and not before it are what
# it made (`git ls-files -o -i --directory`: a wholly ignored directory is one entry). They are kept
# per STEP STRING beside the records (`goh-proven-made/<hash>`; goh-proven/ holds records only),
# not per tree: what a build makes does not change with the commit. Only a run that BEGAN with no ignored file (a push export; GOH_EXPORT_KEEP
# entries and tool caches aside) sees everything a step made -- in a warm checkout the build/ of
# yesterday hides today's -- so only such a run writes the `fresh` line that lets an empty list mean
# "makes nothing". A warm run adds what it saw, never removes. Caches a tool rebuilds on demand
# (PROVEN_CACHES) are not products: a lint that left `__pycache__/` made nothing anyone needs.
# Under GOH_CI_JOBS>1 a step may be credited with a neighbour's products: an extra re-run, never a
# missed one.
#
# THE RULE (proven_owed). A hit is HOLLOW when a product is not here, or -- in a fresh tree -- when
# what the step makes was never measured from one. local_ci re-runs a hollow hit when a LATER step
# will really run (it may read the products); with every later step skipped too, nothing can, so it
# is skipped (a ship push of a tree its land proved whole keeps rust_gate skipped). proven.sh sees
# one step and never a later one, so it re-runs every hollow hit.
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "gates/_proven_made.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
PROVEN_CACHES="__pycache__ .pytest_cache .ruff_cache .mypy_cache .hypothesis .DS_Store"

# proven_ignored -- the ignored entries of the working tree under the current directory, sorted,
# caches dropped. git lists an untracked directory holding only ignored entries AND those entries
# (`pkg/`, `pkg/__pycache__/`): the parent is dropped for its children.
proven_ignored() {
    local p base prev=""
    git ls-files -z --others --ignored --exclude-standard --directory 2>/dev/null | tr '\0' '\n' \
        | env LC_ALL=C sort | {
            while IFS= read -r p; do
                case "$prev" in */) case "$p" in "$prev"?*) prev="" ;; esac ;; esac  # P's parent
                [ -z "$prev" ] || printf '%s\n' "$prev"
                prev=""
                base="${p%/}"
                base="${base##*/}"
                case " $PROVEN_CACHES " in *" $base "*) continue ;; esac
                case "$base" in *.pyc) continue ;; esac
                prev="$p"
            done
            [ -z "$prev" ] || printf '%s\n' "$prev"
        }
}

# proven_fresh_tree -- 0 when no ignored entry is here but GOH_EXPORT_KEEP's (and caches): a run
# starting here sees everything each step makes.
proven_fresh_tree() {
    local e k
    while IFS= read -r e; do
        [ -n "$e" ] || continue
        for k in ${GOH_EXPORT_KEEP:-}; do
            k="${k%/}"
            case "$k/" in "${e%/}/"*) continue 2 ;; esac   # the kept path, or a directory holding it
            case "${e%/}" in "$k"/*) continue 2 ;; esac     # inside a kept directory
        done
        return 1
    done < <(proven_ignored)
    return 0
}

# proven_made <before> [stamp] -- what the step made, one per line: the ignored entries now that
# the file BEFORE (proven_ignored's output when the step started) did not hold, and -- given STAMP,
# touched at its start, in a fresh run only (a warm checkout's node_modules/ would be walked by
# every step) -- each FILE it wrote inside an entry that was already there: a release build into
# the build/ an earlier step of the run created owns build/Release/libX.a, not build/, which the
# earlier step's re-run would bring back without it. At most PROVEN_MADE_MAX files per entry: a
# hollow hit needs only one of them missing.
# `env LC_ALL=C`, never a bare prefix: in this forked group a bare one crashed bash on macOS and
# emptied the measurement (tests/test_shell_locale.py).
PROVEN_MADE_MAX=500
proven_made() {
    [ -r "$1" ] || return 0
    local now e files re="${PROVEN_CACHES//./\\.}"
    re="${re// /|}"
    now="$(proven_ignored)"
    {
        env LC_ALL=C comm -13 "$1" <(printf '%s\n' "$now")
        if [ -n "${2:-}" ] && [ -e "$2" ]; then
            while IFS= read -r e; do
                [ -n "$e" ] || continue
                files="$(find "${e%/}" -type f -newer "$2" -print 2>/dev/null \
                    | grep -vE "(^|/)($re)(/|\$)|\.pyc\$" || true)"
                [ -z "$files" ] || head -n "$PROVEN_MADE_MAX" <<<"$files"
            done < <(env LC_ALL=C comm -12 "$1" <(printf '%s\n' "$now"))
        fi
    } | sed '/^$/d' | env LC_ALL=C sort -u
}

# proven_made_file <step> -- where what the step string makes is kept.
proven_made_file() {
    local dir
    dir="$(proven_dir)" || return 1
    printf '%s-made/%s\n' "$dir" "$(printf 'made v1 %s' "$1" | hash_hex /dev/stdin)"
}

# proven_made_note <step> <fresh:0|1> <made, one per line> -- remember what a green run made. A
# fresh run's list replaces the last; a warm run's is added to it, and alone writes no `fresh`.
proven_made_note() {
    local f head="warm" old=""
    f="$(proven_made_file "$1")" || return 1
    if [ "$2" = 1 ]; then
        head="fresh"
    else
        [ -n "$3" ] || return 0
        if [ -f "$f" ]; then
            IFS= read -r head <"$f" || head="warm"
            old="$(tail -n +2 "$f")"
        fi
    fi
    mkdir -p "${f%/*}" || return 1
    { printf '%s\n' "$head"; printf '%s\n%s\n' "$old" "$3" | sed '/^$/d' | env LC_ALL=C sort -u; } \
        >"$f.tmp.$$" && mv -f "$f.tmp.$$" "$f"
}

# proven_owed <step> <fresh:0|1> -- 0 when a hit on the step is HOLLOW (the rule in the header),
# printing why: the products that are not here, or that it was never measured from a fresh tree.
proven_owed() {
    local f head="" p missing=""
    f="$(proven_made_file "$1")" || return 1
    if [ -f "$f" ]; then
        { IFS= read -r head
          while IFS= read -r p; do
              [ -z "$p" ] || [ -e "$p" ] || missing="$missing $p"
          done; } <"$f"
    fi
    if [ -n "$missing" ]; then
        printf 'its products are not here:%s\n' "$missing"
        return 0
    fi
    if [ "$2" = 1 ] && [ "$head" != fresh ]; then
        printf 'what it makes was never measured from a fresh tree\n'
        return 0
    fi
    return 1
}
