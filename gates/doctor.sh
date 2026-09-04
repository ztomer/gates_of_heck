#!/usr/bin/env bash
# doctor.sh — diagnose gate wiring for one repo.
#
#   doctor.sh [repo-root]    (default: $PWD)
#
# When gates "don't run" or "can't find" something, the cause is always
# wiring: a deleted GOH_DIR checkout, an unset core.hooksPath after a fresh
# clone (.githooks/ travels with the repo; core.hooksPath is local config
# and does not), a missing tools/gate.sh, or a typo'd GOH_* key that no
# gate reads. This script checks each layer and names the fix.
#
# Exit 0 when healthy (warnings allowed), 1 naming each hard problem.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOH_ROOT="$(cd "$HERE/.." && pwd)"

# Kare styling: the shipped lib when present, plain fallbacks otherwise
# (sourcing replaces the fallbacks — lib.sh defines every name itself).
info() { printf '→ %s\n' "$*"; }
ok() { printf '✓ %s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*" >&2; }
err() { printf '✗ %s\n' "$*" >&2; }
if [ -f "$GOH_ROOT/tui/lib.sh" ]; then
    # shellcheck disable=SC1090
    . "$GOH_ROOT/tui/lib.sh"
fi

PROBLEMS=0
fail() { err "problem: $*"; PROBLEMS=$((PROBLEMS + 1)); }

repo="${1:-$PWD}"
[ -d "$repo" ] || { fail "repo root is not a directory: $repo"; exit 1; }
repo="$(cd "$repo" && pwd)"

info "doctor: $repo (GOH checkout: $GOH_ROOT)"

# 1. The shared checkout itself.
for f in gates/structural.sh checks/check_no_emoji.py; do
    [ -f "$GOH_ROOT/$f" ] || fail "shared checkout is corrupt: $GOH_ROOT/$f missing"
done

# 2. A git repo at all.
if ! git -C "$repo" rev-parse --show-toplevel >/dev/null 2>&1; then
    fail "not a git repo — gates need one (git init)"
    exit 1
fi

# 3. The per-repo gate entry (pre-push hard-requires it).
if [ ! -f "$repo/tools/gate.sh" ]; then
    fail "tools/gate.sh missing — pre-push cannot run (create it: run '$GOH_ROOT/install.sh $repo')"
elif [ ! -x "$repo/tools/gate.sh" ]; then
    warn "tools/gate.sh is not executable"
else
    ok "tools/gate.sh present"
fi

# 4. Hooks. .githooks/ clones with the repo; core.hooksPath does NOT, so a
# fresh clone silently runs no gates until install.sh sets it. That silent
# skip is exactly what this check refuses to allow quietly.
hooks_path="$(git -C "$repo" config core.hooksPath || true)"
if [ -z "$hooks_path" ]; then
    if [ -d "$repo/.githooks" ]; then
        fail ".githooks/ exists but core.hooksPath is unset — no hook runs (fix: '$GOH_ROOT/install.sh $repo')"
    else
        warn "no .githooks/ and core.hooksPath unset — gates never run (fix: '$GOH_ROOT/install.sh $repo')"
    fi
elif [ "$hooks_path" != ".githooks" ]; then
    warn "core.hooksPath is '$hooks_path', not .githooks (custom setup — yours to maintain)"
else
    for h in pre-commit pre-push; do
        if [ ! -x "$repo/.githooks/$h" ]; then
            fail ".githooks/$h missing or not executable (fix: '$GOH_ROOT/install.sh $repo')"
        fi
    done
    [ "$PROBLEMS" -eq 0 ] && ok "hooks wired (core.hooksPath → .githooks)"
fi

# 5. .gatesrc: unknown keys are typos or stale names no gate reads. The known
# set derives from docs/config.md itself, so the schema cannot drift from
# this check (same single-source rule as test_config_schema_covers_keys).
if [ ! -f "$repo/.gatesrc" ]; then
    warn "no .gatesrc — all defaults apply (set GOH_MAX_LINES=500 to enable the length cap)"
else
    known="$(grep -o 'GOH_[A-Z][A-Z_]*' "$GOH_ROOT/docs/config.md" 2>/dev/null | sort -u || true)"
    used="$(grep -o '^GOH_[A-Z][A-Z_]*' "$repo/.gatesrc" 2>/dev/null | sort -u || true)"
    unknown=""
    for k in $used; do
        printf '%s\n' "$known" | grep -qx "$k" || unknown="${unknown}${unknown:+ }$k"
    done
    if [ -n "$unknown" ]; then
        warn "unknown GOH_* keys in .gatesrc (no gate reads them — typo or stale name): $unknown"
    else
        ok ".gatesrc keys all known ($(printf '%s' "$used" | wc -w | tr -d ' '))"
    fi
fi

# 6. Toolchains, informational. python3 is load-bearing for layer 1;
# language toolchains matter only to the gates that use them.
if command -v python3 >/dev/null 2>&1; then
    ok "python3 present ($(python3 --version 2>&1))"
else
    fail "python3 not on PATH — layer 1 checkers cannot run"
fi
for t in cargo swift; do
    if command -v "$t" >/dev/null 2>&1; then
        info "$t present"
    else
        info "$t absent (only its gate needs it)"
    fi
done

if [ "$PROBLEMS" -eq 0 ]; then
    ok "doctor: healthy"
    exit 0
fi
err "doctor: $PROBLEMS problem(s) — fix the named lines above"
exit 1
