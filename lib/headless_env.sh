#!/usr/bin/env bash
# headless_env.sh — the RUNTIME half of the "tests never render to the real
# screen" invariant. The static half is checks/check_no_screen_presentation.py;
# neither subsumes the other: the grep sees what a test ASKS for, this file is
# what actually reaches the child process's environment.
#
# Contract, modeled on ZeroThunder's two-tier screen_policy but tool-neutral:
#
#   OFFSCREEN (the default; everything unattended runs here) — GOH_HEADLESS=1
#   is set for the whole run and forwarded to every child launch via
#   `headless_env`. GUI children must refuse to present under it.
#
#   LIVE (attended only) — a harness that genuinely needs the screen calls
#   `headless_require_live <name>` at the TOP of its entry point, which REFUSES
#   with exit code 3 while GOH_HEADLESS is enforced — distinct from 1 so a
#   runner can tell "refused, wrong tier" from "ran and failed". Run it
#   deliberately with `env -u GOH_HEADLESS`.
#
# Usage:
#   . "$GOH_DIR/lib/headless_env.sh"
#   headless_require_live "qa/screenshot_sweep"     # live tier declares itself
#   env $(headless_env) ./bin/MyApp --render-one    # child stays offscreen
#
# Truthiness matches the Python convention: "", 0, false, no, off mean OFF.

GOH_HEADLESS_REFUSAL_EXIT=3

_headless_truthy() {
    case "${1-}" in
        "" | 0 | false | no | off) return 1 ;;
        *) return 0 ;;
    esac
}

headless_enforced() {
    # No variable at all means the policy is not enforced — an attended shell
    # is legal. Any truthy value enforces it. `${VAR+x}` stays safe under the
    # `set -u` every house gate runs with.
    [ -n "${GOH_HEADLESS+x}" ] && _headless_truthy "$GOH_HEADLESS"
}

# Emit GOH_HEADLESS=1 as env assignments, usable as `env $(headless_env) cmd`
# or eval'd before launching GUI children.
headless_env() {
    printf 'GOH_HEADLESS=1\n'
}

# Live-tier declaration. Refuses (exit $GOH_HEADLESS_REFUSAL_EXIT, message on
# stderr saying WHY and HOW to proceed) while the offscreen policy is enforced.
# It EXITS rather than returns: a refusal that leaves the decision to an
# unguarded caller is a refusal that silently does not happen.
headless_require_live() {
    local name="${1:-this script}"
    if headless_enforced; then
        {
            printf '✗ [headless_env] %s needs the real screen and GOH_HEADLESS is enforced — refusing.\n' "$name"
            printf '  This is the attended LIVE tier; unattended runs use the offscreen tier only.\n'
            printf '  To run it deliberately:  env -u GOH_HEADLESS %s\n' "$0"
        } >&2
        exit "$GOH_HEADLESS_REFUSAL_EXIT"
    fi
}
