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

# Early informational flags never reach the gates below.
case "${1:-}" in
  -h|--help)
    cat <<'EOF'
usage: gate.sh [--staged | --full | --doctor [repo] | --help]
  --staged      pre-commit scope: layer 1 only (fast)
  --full        pre-push scope: every layer (runs the test suite)
  --doctor      diagnose gate wiring (GOH resolution, hooks, .gatesrc keys)
EOF
    exit 0
    ;;
  --doctor)
    exec bash "$GOH/gates/doctor.sh" "${2:-$PWD}"
    ;;
esac

case "${1:-}" in
  --full)
    # Every layer, from the ONE list in .gatesrc (GOH_CI_STEPS).
    exec "$GOH/gates/local_ci.sh" .
    ;;
  *)
    exec "$GOH/gates/structural.sh" "$@"
    ;;
esac
