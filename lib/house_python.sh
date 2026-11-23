# shellcheck shell=bash
# lib/house_python.sh -- THE HOUSE INTERPRETER: the one Python goh runs its own helpers on, found
# the one way (G2d, 2026-10-25). Sourced by gates/_from_head.sh (so by every gate) and by every goh
# shell script that runs Python; run as a script it prints the path, for any other caller:
#
#   . "$GOH_ROOT/lib/house_python.sh"     # sets and exports GOH_PY, or dies naming the floor
#   "$GOH_PY" -S "$GOH_ROOT/lib/x.py"
#   py="$(bash "$GOH_ROOT/lib/house_python.sh")"
#
# WHY. Every goh script ran a bare `python3`: the FIRST python3 on PATH. A hook started from a GUI
# client, a launchd job, or a test that hands its gate PATH=/usr/bin:/bin finds /usr/bin/python3,
# macOS's 3.9.6 (EOL since 2025-10) -- and goh's own suite does exactly that in dozens of tests, so
# G2b read "the suite is green on 3.9" as evidence and floored python3 at 3.9. It was an accident of
# PATH, not a decision. The house runs the newest RELEASE of every layer.
#
# HOW. The candidates are every `python3` and `python3.N` on PATH and in the places a release is
# installed (Homebrew on both platforms, ~/.local/bin), so a reduced PATH cannot hide the house
# interpreter; the NEWEST by the version each one reports is chosen, whatever the PATH order. The
# floor is gates/required_tools.tsv's python3 floor -- one number, read here, never restated -- and
# a newest candidate below it is a refusal naming the floor, what was found, and the fix.
#
# SEAMS (tests, and an owner who wants a specific interpreter):
#   GOH_PY=<path>          use exactly this interpreter -- still judged against the floor
#   GOH_PY_SEARCH=<dirs>   search ONLY these colon-separated directories (a planted fake 3.9)
# The verdict is exported (GOH_PY, GOH_PY_OK) so a process tree resolves once.
#
# PROBE CACHE (GOH.PYPROBECACHE, 2026-11-18). A scrubbed env (a hermetic test, a foreign-repo check)
# drops GOH_PY_OK, and each such tree re-ran every candidate's version probe: 126 python3 starts in
# one land pre-step (tools/spawn_profile.py). A probe's answer is a fact of the FILE, so it is kept in
# ${XDG_CACHE_HOME:-~/.cache}/goh/house_python.tsv, keyed by the path and the identity of the file it
# resolves to (device, inode, mtime, size -- one `stat` for all candidates); a replaced or touched
# interpreter misses and is probed again. Pinned by tests/test_house_python.py.

# _goh_house_py_version <python>: its major.minor.micro, or nothing and 1.
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/house_python.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
_goh_house_py_lib="$(cd "$_goh_d" && pwd)"
_goh_house_py_version() {
    "$1" -I -S -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null </dev/null
}

# Bash 3.2 sources this file (/bin/bash on macOS), so no associative arrays: the stat answers and
# the cache are newline-separated "path<TAB>..." strings, looked up with a case pattern.

# _goh_house_py_ids <path>...: set _goh_py_ids to "path<TAB>dev:inode:mtime:size" lines, one stat.
_goh_house_py_ids() {
    _goh_py_ids=""
    [ "$#" -gt 0 ] || return 0
    if [[ "${OSTYPE:-}" == darwin* ]]; then
        _goh_py_ids="$(/usr/bin/stat -L -f '%N	%d:%i:%m:%z' -- "$@" 2>/dev/null)"
    else
        _goh_py_ids="$(stat -L -c '%n	%d:%i:%Y:%s' -- "$@" 2>/dev/null)"
    fi
}

# _goh_house_py_cache_load: set _goh_py_cache (the file) and _goh_py_seen (its lines).
_goh_house_py_cache_load() {
    _goh_py_seen="" _goh_py_cache=""
    local root="${XDG_CACHE_HOME:-${HOME:+$HOME/.cache}}"
    [ -n "$root" ] || return 0
    _goh_py_cache="$root/goh/house_python.tsv"
    [ -r "$_goh_py_cache" ] && _goh_py_seen="$(cat -- "$_goh_py_cache" 2>/dev/null)"
    return 0
}

# _goh_house_py_cached_version <python>: its version from the cache, or a probe recorded in it.
_goh_house_py_cached_version() {
    local nl=$'\n' tab=$'\t' id="" ver rest
    case "$nl$_goh_py_ids$nl" in
        *"$nl$1$tab"*) rest="$nl$_goh_py_ids$nl"; rest="${rest#*"$nl$1$tab"}"; id="${rest%%"$nl"*}" ;;
    esac
    if [ -n "$id" ]; then
        case "$nl$_goh_py_seen$nl" in
            *"$nl$1$tab$id$tab"*)
                rest="$nl$_goh_py_seen$nl"; rest="${rest#*"$nl$1$tab$id$tab"}"
                printf '%s\n' "${rest%%"$nl"*}"
                return 0 ;;
        esac
    fi
    ver="$(_goh_house_py_version "$1")" || return 1
    [ -n "$ver" ] || return 1
    if [ -n "$id" ] && [ -n "$_goh_py_cache" ]; then
        (   # one short O_APPEND line: concurrent writers interleave whole lines
            . "$_goh_house_py_lib/suite_cache_guard.sh" && goh_suite_cache_ok "$_goh_py_cache" house_python.sh \
                && mkdir -p "${_goh_py_cache%/*}" && printf '%s\t%s\t%s\n' "$1" "$id" "$ver" >>"$_goh_py_cache"
        ) 2>/dev/null || true
    fi
    printf '%s\n' "$ver"
}

# _goh_house_py_ge <a> <b>: a >= b, numerically, field by field (3.15.1 >= 3.15; 3.9.6 < 3.15).
_goh_house_py_ge() {
    local IFS=. i
    local -a a b
    read -r -a a <<<"$1"
    read -r -a b <<<"$2"
    for i in 0 1 2; do
        (( 10#${a[i]:-0} > 10#${b[i]:-0} )) && return 0
        (( 10#${a[i]:-0} < 10#${b[i]:-0} )) && return 1
    done
    return 0
}

# goh_house_python_floor: the python3 floor from gates/required_tools.tsv, on stdout.
goh_house_python_floor() {
    local tsv
    tsv="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/gates/required_tools.tsv"
    awk -F'\t' '!/^#/ && $2 == "python3" && $6 != "-" { print $6; exit }' "$tsv" 2>/dev/null
}

# goh_house_python: set and export GOH_PY, or print why not on stderr and return 1.
goh_house_python() {
    if [ -n "${GOH_PY:-}" ] && [ "${GOH_PY_OK:-}" = "$GOH_PY" ]; then
        return 0
    fi
    local floor dirs dir cand v best="" best_v="" seen=":"
    local -a cands=()
    _goh_house_py_cache_load
    floor="$(goh_house_python_floor)"
    if [ -n "${GOH_PY:-}" ]; then
        best="$GOH_PY"
        _goh_house_py_ids "$GOH_PY"
        best_v="$(_goh_house_py_cached_version "$GOH_PY")" || {
            echo "✗ goh: GOH_PY=$GOH_PY cannot say its Python version -- point it at a python3 at or above ${floor:-the floor}" >&2
            return 1
        }
    else
        if [ -n "${GOH_PY_SEARCH:-}" ]; then
            dirs="$GOH_PY_SEARCH"
        else
            dirs="${PATH:-}:/opt/homebrew/bin:/usr/local/bin:/home/linuxbrew/.linuxbrew/bin:${HOME:+$HOME/.local/bin}"
        fi
        local IFS=:
        for dir in $dirs; do
            [ -n "$dir" ] && [ -d "$dir" ] || continue
            for cand in "$dir"/python3 "$dir"/python3.[0-9]*; do
                [[ "${cand##*/}" =~ ^python3(\.[0-9]+)?$ ]] || continue   # not python3.15-config
                [ -x "$cand" ] || continue
                case "$seen" in *":$cand:"*) continue ;; esac
                seen="$seen$cand:"
                cands+=("$cand")
            done
        done
        unset IFS
        _goh_house_py_ids ${cands[@]+"${cands[@]}"}
        for cand in ${cands[@]+"${cands[@]}"}; do
            v="$(_goh_house_py_cached_version "$cand")" || continue
            if [ -z "$best_v" ] || ! _goh_house_py_ge "$best_v" "$v"; then   # strictly newer
                best="$cand" best_v="$v"
            fi
        done
        if [ -z "$best" ]; then
            echo "✗ goh: no python3 found (searched: $dirs) -- the house interpreter is Python ${floor:-3} or newer; install it (brew install python), or set GOH_PY" >&2
            return 1
        fi
    fi
    if [ -n "$floor" ] && ! _goh_house_py_ge "$best_v" "$floor"; then
        echo "✗ goh: the house Python is $floor or newer (the python3 floor in gates/required_tools.tsv), and the newest found is $best_v at $best -- install a current release (brew install python), or set GOH_PY to a python3 at or above $floor" >&2
        return 1
    fi
    export GOH_PY="$best" GOH_PY_OK="$best"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    goh_house_python && printf '%s\n' "$GOH_PY"
else
    goh_house_python || exit 1
fi
