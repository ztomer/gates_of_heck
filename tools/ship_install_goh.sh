#!/usr/bin/env bash
# ship_install_goh.sh -- gates_of_heck's `goh ship` install step (.gatesrc GOH_SHIP_INSTALL).
#
# goh installs nothing per release: every consumer's hooks run this checkout at run time (the shim,
# GOH.CLASS.ROLLOUT). What a goh release DOES leave stale is every pin goh owns in a consumer (the
# manifest, lib/consumer_pins.py PINS: the workflow's goh tag, the ruff pin + uv.lock), so this step
# moves them to the release just pushed (scripts/wf_pin.py --bump, GOH.WFPIN, GOH.PINPROPAGATE):
# one commit per repo through its own hooks, or set aside and named in the ship's record
# (GOH_SHIP_RESULT's goh.db ship_pins row, carried in as consumer_pins). It only COMMITS: the
# pushes, each through that consumer's full push gate, run in a detached, bounded sweep after the
# ship returns (lib/pin_sweep.py, GOH.PINPUSHGATE; `goh ship --status` prints its rows apart). A
# repo that refuses does not fail the ship; the records and the next listing name it.
#
# goh's `goh ship` install step (`.gatesrc` GOH_SHIP_INSTALL): moves every pin goh owns in a
# consumer (the workflow's goh tag, the ruff pin + uv.lock) to the release just pushed and commits
# it (`scripts/wf_pin.py --bump --tag vGOH_SHIP_VERSION`, GOH.WFPIN, GOH.PINPROPAGATE); each repo's
# outcome lands in the result as `consumer_pins`; the pushes run after the ship in the detached
# sweep `lib/pin_sweep.py` (GOH.PINPUSHGATE). Pinned by `test_wf_pin.py`, `test_consumer_pins.py`,
# `test_pin_sweep.py`.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=lib/house_python.sh
. "$GOH/lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)
tag=()
[ -n "${GOH_SHIP_VERSION:-}" ] && tag=(--tag "v${GOH_SHIP_VERSION#v}")
exec "$GOH_PY" "$GOH/scripts/wf_pin.py" --bump "${tag[@]}"
exit
} # parse-guard
