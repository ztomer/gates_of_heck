#!/usr/bin/env bash
# ship_install_goh.sh -- gates_of_heck's `goh ship` install step (.gatesrc GOH_SHIP_INSTALL).
#
# goh installs nothing per release: every consumer's hooks run this checkout at run time (the shim,
# GOH.CLASS.ROLLOUT). What a goh release DOES leave stale is each consumer workflow's pinned goh
# tag, so this step moves them to the release just pushed (scripts/wf_pin.py --bump, GOH.WFPIN):
# each repo's workflow files in one commit through its own hooks, or set aside and named. It never
# pushes another repo, and a set-aside repo does not fail the ship (the next listing names it).
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" || exit; goh_bash5 "$@"
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=lib/house_python.sh
. "$GOH/lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)
tag=()
[ -n "${GOH_SHIP_VERSION:-}" ] && tag=(--tag "v${GOH_SHIP_VERSION#v}")
exec "$GOH_PY" "$GOH/scripts/wf_pin.py" --bump "${tag[@]}"
exit
} # parse-guard
