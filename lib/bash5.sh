# shellcheck shell=bash
# lib/bash5.sh -- the bash floor: goh's shell runs on bash >= 5, never on whichever bash is first on
# PATH (GOH.BASH5, owner ruling 2026-11-12: "require bash 5, enforced structurally").
#
# WHY. 53 of goh's 83 scripts said `#!/usr/bin/env bash` and none asked which bash that found:
# Homebrew's 5.3 in the owner's terminal, Apple's /bin/bash 3.2 on the hosted macOS runner, under a
# minimal-PATH git hook and under launchd. v0.106.1 shipped `tools/release_goh.sh` green locally and
# red on the runner (`title[@]: unbound variable`, an empty array under `set -u`, fixed in 4.4), so
# the local gate and CI judged two languages. Every bash file goh tracks now runs, as its first
# statement (docs/contracts.md section 28, tests/test_bash5_guard.py refuses one that does not):
#
#   . "$(dirname "${BASH_SOURCE[0]}")/<up>/lib/bash5.sh" && goh_bash5 "$@"
#
# EXECUTED (a script bash runs): under bash < 5 it re-execs the same script, same arguments, same
# stdin, with the first bash >= 5 it finds -- so the rest of the file, parsed whole by bash 3.2
# before this line ran (the parse guard), is RUN only by bash 5. None found: it dies naming the fix.
# SOURCED (`.`/`source`, told apart by FUNCNAME[1] = "source"): it NEVER re-execs -- a library has
# neither its caller's argv nor the right to replace its caller. Under bash < 5 it warns once per
# process tree and returns, so a library stays bash-3.2-safe: consumer scripts source goh's
# libraries under whatever bash they run (the estate sweep that guards them is its own item).
#
# NO LOOP. The re-exec exports GOH_BASH5_REEXEC=<the bash it chose>; the bash-5 child consumes it
# (unsets it), so a grandchild starts clean. A child that still finds itself below 5 with the marker
# set was handed a bash that lied about its version: refused, naming it, never exec'd again.
#
# WHERE IT LOOKS (first >= 5 wins; each candidate is asked its own BASH_VERSINFO, never trusted by
# path): macOS -> /opt/homebrew/bin/bash (Apple silicon only, house rule 3); Linux -> /usr/bin/bash,
# /bin/bash (the system bash, already 5 on every supported distribution, so the guard is a no-op
# there); then PATH's bash. The fast path -- already on bash 5 -- spawns nothing.
#
# SEAMS (tests; both directions run on this Mac, where /bin/bash 3.2 is a real red case):
#   GOH_BASH_CANDIDATES  colon-separated bash paths, replacing the list above ("no bash 5 found"
#                        is `GOH_BASH_CANDIDATES=/bin/bash`)
#   GOH_BASH_MAJOR       the major version to believe of THIS shell ("old bash" on a machine with
#                        no bash 3.2, such as Linux CI); dropped before the re-exec, so the child
#                        judges itself
#
#   bash lib/bash5.sh    (run directly) prints the bash >= 5 the guard would use, or dies: CI asks
#                        it whether to install one (.github/actions/goh-floors).

GOH_BASH_FLOOR=5
# Defined once per shell: _goh_bash5_say sources tui/lib.sh, whose own guard sources this file again
# from inside goh_bash5, and a function is never redefined while it runs.
if ! declare -F goh_bash5 >/dev/null 2>&1; then

# _goh_bash5_say <err|warn> <message> -- Kare style through tui/lib.sh (this copy, never the
# trampoline to HEAD's export, which runs Python); a bare glyph line when tui/lib.sh is absent.
_goh_bash5_say() {
    local level="$1" tui
    shift
    tui="$(cd "$(dirname "${BASH_SOURCE[0]}")/../tui" 2>/dev/null && pwd)/lib.sh"
    # shellcheck source=/dev/null  # never followed: tui/lib.sh runs this guard, a cycle for -x
    if [ -f "$tui" ] && GOH_BASH5_SAYING=1 GOH_TUI_BOOTSTRAP=1 . "$tui" 2>/dev/null; then
        "$level" "$*"
    elif [ "$level" = err ]; then
        printf '%s\n' "✗ $*" >&2
    else
        printf '%s\n' "⚠ $*" >&2
    fi
}

# _goh_bash5_find -- prints the first candidate whose own BASH_VERSINFO is >= the floor.
_goh_bash5_find() {
    local list="${GOH_BASH_CANDIDATES-}" cand v IFS=:
    if [ -z "${GOH_BASH_CANDIDATES+set}" ]; then
        case "${OSTYPE:-}" in
            darwin*) list="/opt/homebrew/bin/bash" ;;
            linux*) list="/usr/bin/bash:/bin/bash" ;;
        esac
        list="$list:$(type -P bash 2>/dev/null || true)"
    fi
    for cand in $list; do
        [ -n "$cand" ] && [ -x "$cand" ] || continue
        v="$("$cand" -c 'printf %s "${BASH_VERSINFO[0]}"' 2>/dev/null </dev/null)" || v=0
        case "$v" in '' | *[!0-9]*) v=0 ;; esac
        if [ "$v" -ge "$GOH_BASH_FLOOR" ]; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}

_goh_bash5_fix() {
    case "${OSTYPE:-}" in
        darwin*) printf '%s' "brew install bash (it installs /opt/homebrew/bin/bash)" ;;
        *) printf '%s' "install bash >= $GOH_BASH_FLOOR with the system package manager" ;;
    esac
}

goh_bash5() { # "$@" of the caller -- returns on bash >= 5; else re-execs (executed) or warns (sourced)
    local major="${GOH_BASH_MAJOR:-${BASH_VERSINFO[0]}}" script cand
    if [ "${FUNCNAME[1]:-}" = source ]; then
        [ "$major" -ge "$GOH_BASH_FLOOR" ] && return 0
        [ -n "${GOH_BASH5_SAYING:-}${GOH_BASH5_WARNED:-}" ] && return 0
        export GOH_BASH5_WARNED=1
        _goh_bash5_say warn "${BASH_SOURCE[2]:-a shell} sourced ${BASH_SOURCE[1]} under bash" \
"${BASH_VERSION} ($BASH), below goh's floor of bash $GOH_BASH_FLOOR -- give that script the guard" \
"as its first statement: . \"\$GOH_DIR/lib/bash5.sh\" && goh_bash5 \"\$@\""
        return 0
    fi
    if [ "$major" -ge "$GOH_BASH_FLOOR" ]; then
        unset GOH_BASH5_REEXEC
        return 0
    fi
    script="${BASH_SOURCE[1]}"
    if [ -n "${GOH_BASH5_REEXEC:-}" ]; then
        _goh_bash5_say err "$script: re-run under $GOH_BASH5_REEXEC, which said it was bash" \
"$GOH_BASH_FLOOR, and this is still bash ${BASH_VERSION} -- refused rather than re-run again"
        exit 1
    fi
    if ! cand="$(_goh_bash5_find)"; then
        _goh_bash5_say err "$script needs bash >= $GOH_BASH_FLOOR; this is bash ${BASH_VERSION} ($BASH)" \
"and none was found in ${GOH_BASH_CANDIDATES-the default places and PATH} -- $(_goh_bash5_fix)"
        exit 1
    fi
    unset GOH_BASH_MAJOR
    export GOH_BASH5_REEXEC="$cand"
    exec "$cand" "$script" "$@"
}

fi

# Run directly: name the bash >= 5 the guard would use, or fail naming the fix.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    if cand="$(_goh_bash5_find)"; then
        printf '%s\n' "$cand"
        exit 0
    fi
    _goh_bash5_say err "no bash >= $GOH_BASH_FLOOR found -- $(_goh_bash5_fix)"
    exit 1
fi
