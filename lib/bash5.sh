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
#   _goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; \
#   . "$_goh_d/<up>/lib/bash5.sh" || exit; goh_bash5 "$@"
#
# Bash builtins alone find the directory (GOH.GITKILLLOCKFREE): `${BASH_SOURCE[0]%/*}`, `.` for a bare
# name, so the line runs with PATH empty, where `dirname` is not found.
#
# `|| exit`, never `&&` (GOH.BASH5SILENT): with `&&` a missing file failed the `.`, skipped the guard,
# and the script ran on unguarded with no error. Now the `.` names the file and the script stops.
#
# EXECUTED (a script bash runs): under bash < 5 it re-execs the same script, same arguments, same
# stdin, with the first bash >= 5 it finds -- so the rest of the file, parsed whole by bash 3.2
# before this line ran (the parse guard), is RUN only by bash 5. None found: it dies naming the fix.
# SOURCED (`.`/`source`, told apart by FUNCNAME[1] = "source"): it NEVER re-execs -- a library has
# neither its caller's argv nor the right to replace its caller. It returns, silently: consumer
# scripts source goh's libraries under whatever bash they run, so a library stays bash-3.2-safe
# (the estate sweep that guards them is its own item). A warning there was tried and dropped: it
# printed into every unguarded consumer script's and test's output, a signal nobody acts on.
#
# NO LOOP. The re-exec exports GOH_BASH_REEXEC=<the bash it chose>; the bash-5 child consumes it
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
#   GOH_BASH_BREW        the brew to install bash with when none is found ("" = never install);
#                        unset, it is PATH's brew on a GitHub Actions runner and nothing elsewhere
#
# ON A CI RUNNER IT INSTALLS ONE (GOH.INSTALLBASH5). The hosted macOS runner has Homebrew but no
# Homebrew bash, and every consumer workflow runs install.sh BEFORE goh-floors (which installs one):
# the guard died there and the job went red before any repo step (ztools run 43796046671). So an
# EXECUTED script that finds no bash >= 5 on a runner (GITHUB_ACTIONS=true) runs `brew install bash`
# (its output to stderr; stdout stays the script's), then looks again, beside brew first. Off a
# runner it never installs anything: the owner's machine gets the error naming the fix. Run
# directly it only answers, as before -- goh-floors asks it whether to install.
#
#   bash lib/bash5.sh    (run directly) prints the bash >= 5 the guard would use, or dies: CI asks
#                        it whether to install one (.github/actions/goh-floors).

GOH_BASH_FLOOR=5
# Defined once per shell: _goh_bash5_say sources tui/lib.sh, whose own guard sources this file again
# from inside goh_bash5, and a function is never redefined while it runs.
if ! declare -F goh_bash5 >/dev/null 2>&1; then

# _goh_bash5_say <err|warn|info> <message> -- Kare style through tui/lib.sh (this copy, never the
# trampoline to HEAD's export, which runs Python); a bare glyph line when tui/lib.sh is absent.
_goh_bash5_say() {
    local level="$1" tui
    shift
    tui="$(cd "$(dirname "${BASH_SOURCE[0]}")/../tui" 2>/dev/null && pwd)/lib.sh"
    # shellcheck source=/dev/null  # never followed: tui/lib.sh runs this guard, a cycle for -x
    if [ -f "$tui" ] && GOH_TUI_BOOTSTRAP=1 . "$tui" 2>/dev/null; then
        "$level" "$*"
    elif [ "$level" = err ]; then
        printf '%s\n' "✗ $*" >&2
    elif [ "$level" = info ]; then
        printf '%s\n' "→ $*" >&2
    else
        printf '%s\n' "⚠ $*" >&2
    fi
}

# _goh_bash5_find [first] -- prints the first candidate whose own BASH_VERSINFO is >= the floor;
# `first` is asked before the list (where `brew install bash` just put one).
_goh_bash5_find() {
    local list="${GOH_BASH_CANDIDATES-}" first="${1:-}" cand v IFS=:
    if [ -z "${GOH_BASH_CANDIDATES+set}" ]; then
        case "${OSTYPE:-}" in
            darwin*) list="/opt/homebrew/bin/bash" ;;
            linux*) list="/usr/bin/bash:/bin/bash" ;;
        esac
        list="$list:$(type -P bash 2>/dev/null || true)"
    fi
    [ -z "$first" ] || list="$first:$list"
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

# _goh_bash5_brew -- prints the brew the guard may install bash with, or fails (header: CI RUNNER).
_goh_bash5_brew() {
    local brew
    if [ -n "${GOH_BASH_BREW+set}" ]; then
        brew="$GOH_BASH_BREW"
    elif [ "${GITHUB_ACTIONS:-}" = true ]; then
        brew="$(type -P brew 2>/dev/null || true)"
    else
        return 1
    fi
    [ -n "$brew" ] && [ -x "$brew" ] || return 1
    printf '%s\n' "$brew"
}

# _goh_bash5_install -- on a CI runner with brew, installs Homebrew's bash and prints the bash >= 5
# found afterwards (brew's own directory first); fails when there is no brew or nothing came of it.
_goh_bash5_install() {
    local brew
    brew="$(_goh_bash5_brew)" || return 1
    _goh_bash5_say info "no bash >= $GOH_BASH_FLOOR on this CI runner:" \
"installing Homebrew's ($brew install bash)" >&2   # stdout is the bash found
    HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 \
        "$brew" install bash >&2 </dev/null || true
    _goh_bash5_find "${brew%/*}/bash"
}

_goh_bash5_fix() {
    case "${OSTYPE:-}" in
        darwin*) printf '%s' "brew install bash (it installs /opt/homebrew/bin/bash)" ;;
        *) printf '%s' "install bash >= $GOH_BASH_FLOOR with the system package manager" ;;
    esac
}

goh_bash5() { # "$@" of the caller -- returns on bash >= 5; else re-execs (executed); sourced, returns
    local major="${GOH_BASH_MAJOR:-${BASH_VERSINFO[0]}}" script cand
    if [ "${FUNCNAME[1]:-}" = source ]; then
        return 0   # a library never re-execs and stays bash-3.2-safe (header: SOURCED)
    fi
    if [ "$major" -ge "$GOH_BASH_FLOOR" ]; then
        unset GOH_BASH_REEXEC
        return 0
    fi
    script="${BASH_SOURCE[1]}"
    if [ -n "${GOH_BASH_REEXEC:-}" ]; then
        _goh_bash5_say err "$script: re-run under $GOH_BASH_REEXEC, which said it was bash" \
"$GOH_BASH_FLOOR, and this is still bash ${BASH_VERSION} -- refused rather than re-run again"
        exit 1
    fi
    if ! cand="$(_goh_bash5_find)" && ! cand="$(_goh_bash5_install)"; then
        _goh_bash5_say err "$script needs bash >= $GOH_BASH_FLOOR; this is bash ${BASH_VERSION} ($BASH)" \
"and none was found in ${GOH_BASH_CANDIDATES-the default places and PATH} -- $(_goh_bash5_fix)"
        exit 1
    fi
    unset GOH_BASH_MAJOR
    export GOH_BASH_REEXEC="$cand"
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
