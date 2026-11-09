# shellcheck shell=bash
# goh_exit_reason <rc> [<timeout seconds>] -- how a step ended, in words, for its failure line.
#
# A failed step's line said `failed (command: ...)` and nothing else: token-pool's push died in
# `swift test` after `Build complete`, with no test output, and whether that was a signal, an OOM
# kill (jetsam sends SIGKILL) or an exit code was recorded nowhere (GOH.GATEHONEST). Every runner
# speaks the shell convention -- 124 for a ceiling, 128+N for signal N -- so this decodes it, once,
# for goh_step (gates/_common.sh) and local_ci.sh, whose line the push log keeps. A command may
# exit 137 by itself; the words say "exit 137 = SIGKILL", which is what a status of 137 means to
# every shell. The words hold no parentheses: lib/fail_lines.py reads them inside `failed (...)`.
# Pinned by tests/test_step_exit_reason.py. Bash 3.2-safe.
goh_exit_reason() {
    local rc="$1" limit="${2:-}" name
    if [ "$rc" -eq 124 ] && [ -n "$limit" ] && [ "$limit" -gt 0 ]; then
        printf 'timed out after %ss, exit 124' "$limit"
    elif [ "$rc" -gt 128 ] && [ "$rc" -lt 160 ] && name="$(kill -l "$((rc - 128))" 2>/dev/null)" && [ -n "$name" ]; then
        printf 'exit %d = SIG%s, signal %d' "$rc" "${name#SIG}" "$((rc - 128))"
    else
        printf 'exit status %d' "$rc"
    fi
}
