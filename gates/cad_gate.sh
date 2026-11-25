#!/usr/bin/env bash
# cad_gate.sh -- the CAD layer: printable parts judged as the slicer and the printer see them (GOH.CAD).
#
#   cad_gate.sh [repo]        run from the repo root, or name it
#
# A repo of FDM parts made by code ships STLs and a slicer project. Its generator can be green and
# the print still fail: the file a slicer reads is not the mesh the generator held, the slicer warns
# where nobody reads, a snap tears, a bezel strings. Every step judges the artifact that ships, from
# ONE layout file the repo names in .gatesrc (GOH_CAD_LAYOUT; its keys: checks/cad_bambu.py):
#
#   1. every STL the layout places, read as admesh reads it (exact float32 merge): closed, wound
#      outward, no zero-area triangle, each object's "shells" bodies (checks/cad_mesh.py);
#   2. every snap the layout's "strain" names, by bending strain against its limit
#      (checks/cad_strain.py);
#   3. the Bambu Studio project built from the layout and every plate sliced: no slicing warning,
#      support where a part asks and only where it names, the expected nozzle and bed, open-air
#      travel under its cap (checks/cad_bambu.py). It is built in a temp dir: the gate judges,
#      the repo's own build writes the committed project.
#
# Config (.gatesrc): GOH_CAD_LAYOUT (required), GOH_CAD_BAMBU (the Bambu Studio CLI; default the macOS
# app). Exit: 0 green, 1 a step failed, 2 the layer is not configured.
#
# The CAD layer (GOH.CAD): a repo of FDM parts made by code, judged as the slicer and the printer
# see them, from ONE layout file (`GOH_CAD_LAYOUT`): every STL the layout places read as admesh
# reads it (`checks/cad_mesh.py`), every snap it names by bending strain (`checks/cad_strain.py`),
# and the Bambu Studio project built in a temp dir and every plate sliced and gated
# (`checks/cad_bambu.py`). Stdlib only. Not configured is exit 2. Invoked by `--full`, opt-in.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

repo="${1:-$PWD}"
cd "$repo" || { err "cad: no repo at $repo"; exit 2; }
# The gated repo's own config, unknown when this script is linted (GOH.SHX):
# shellcheck source=/dev/null
[ -f .gatesrc ] && . ./.gatesrc
# Not configured is exit 2, a usage error, never a pass (docs/contracts.md 7); _common.sh's die exits 1.
if [ -z "${GOH_CAD_LAYOUT:-}" ]; then
    err "cad: GOH_CAD_LAYOUT is unset -- name the repo's layout JSON in .gatesrc (its keys: checks/cad_bambu.py)"
    exit 2
fi
if [ ! -f "$GOH_CAD_LAYOUT" ]; then
    err "cad: GOH_CAD_LAYOUT=$GOH_CAD_LAYOUT does not exist"
    exit 2
fi

goh_init "cad"
goh_tree_stamp
out="$(mktemp -d "$(goh_tmp)/goh-cad.XXXXXX")"
goh_cleanup_add "$out"

goh_step "STLs as a slicer reads them" "$GOH_PY" "$GOH_ROOT/checks/cad_mesh.py" --layout "$GOH_CAD_LAYOUT" --root .
goh_step "snap strain" "$GOH_PY" "$GOH_ROOT/checks/cad_strain.py" --layout "$GOH_CAD_LAYOUT" --root .
goh_step "Bambu Studio project sliced and gated" \
    "$GOH_PY" "$GOH_ROOT/checks/cad_bambu.py" "$GOH_CAD_LAYOUT" --root . --out-dir "$out" \
        ${GOH_CAD_BAMBU:+--bambu "$GOH_CAD_BAMBU"}

goh_done
exit
} # parse-guard
