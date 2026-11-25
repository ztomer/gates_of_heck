#!/usr/bin/env bash
# load_sources.sh -- the launchd job's body: sample what burns the CPU for an hour, then exit, so
# launchd (KeepAlive) starts the next hour from the newest HEAD (lib/load_sources.py, GOH.LOADSOURCES).
#
# An unattended job runs the COMMITTED code, never the working tree (C4): this re-runs itself from
# an export of HEAD (gates/_from_head.sh) and the export's copy of the sampler with it.
#
# `tools/load_sources.sh` / `tools/install_load_sources.sh`: the load-sources sampler's launchd job
# (`com.ztomer.goh-load-sources`, KeepAlive, an hour per run, HEAD's copy (C4)) and its one-line
# installer; `goh load --report` reads what it writes (`lib/load_sources.py`). Pinned by
# `test_install_launchagent.py`.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../gates/python.sh" "$HERE/../lib/load_sources.py" sample "$@"
exit
} # parse-guard
