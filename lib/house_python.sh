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

# _goh_house_py_version <python>: its major.minor.micro, or nothing and 1.
_goh_house_py_version() {
    "$1" -I -S -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null </dev/null
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
    floor="$(goh_house_python_floor)"
    if [ -n "${GOH_PY:-}" ]; then
        best="$GOH_PY"
        best_v="$(_goh_house_py_version "$GOH_PY")" || {
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
                v="$(_goh_house_py_version "$cand")" || continue
                if [ -z "$best_v" ] || ! _goh_house_py_ge "$best_v" "$v"; then   # strictly newer
                    best="$cand" best_v="$v"
                fi
            done
        done
        unset IFS
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
