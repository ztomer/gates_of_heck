#!/usr/bin/env bash
# Per-repo gate entry point. Declares which toolchains this repo contains and
# delegates; it holds no gate logic of its own.
#   --staged : pre-commit scope (fast) — layer 1 only
#   --full   : pre-push scope — every layer
set -euo pipefail
GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"

# Style for our own branch lines below (structural.sh styles itself in its
# own process; functions do not propagate back here).
if [ -f "$GOH/tui/lib.sh" ]; then
    # shellcheck disable=SC1090
    . "$GOH/tui/lib.sh"
else
    warn() { printf '⚠ %s\n' "$*" >&2; }
fi

"$GOH/gates/structural.sh" "$@"

case "${1:-}" in
  --full)
    # Layer 3 (genuinely local checks): create ./tools/repo_gates.sh and
    # uncomment:
    #   ./tools/repo_gates.sh
    # This repo IS the gates; its suite is the only thing standing between a
    # gate bug and every consumer.
    #
    # Parallel workers when pytest-xdist is installed (--dist loadgroup:
    # unmarked tests spread by load; files sharing an xdist_group marker stay
    # on ONE worker. That grouping exists for the release tests:
    # test_release_hardening.py corrupts tools/release-kit/release.sh MID-RUN
    # on purpose, and test_release_kit.py runs real releases — split across
    # workers, the latter reads corrupted bytes and dies. loadfile alone only
    # groups within a file, not across the two files).
    # Without xdist the flags would be a hard error, so probe first and
    # degrade to serial + warn.
    if python3 -c "import xdist" 2>/dev/null; then
        ( cd "$GOH" && python3 -m pytest tests/ -q -n 8 --dist loadgroup )
    else
        warn "pytest-xdist not installed — serial suite (pip3 install pytest-xdist)"
        ( cd "$GOH" && python3 -m pytest tests/ -q )
    fi
    ;;
esac
