#!/usr/bin/env bash
# _export_bin_goh.sh -- `bin/goh` of a HEAD export (C4). gates/_from_head.sh copies this file into
# every export it builds, as `<export>/bin/goh`; it is never run from the checkout itself.
#
# bin/goh is not in git, so an export of HEAD had no bin/ at all, and anything that ran
# `$GOH_DIR/bin/goh` with GOH_DIR at an export found nothing: the export that leaked into the
# coordinator's GOH_DIR (GOH.CLEANOPEN), and a ship pinned to its export (GOH.SHIPPINNED), whose
# release kit or pre-push gate may run goh. This answers with the native build of the EXPORT'S
# commit (`.goh-head`) through the one resolver, gates/_goh_bin.sh, with GOH_DIR at the export, so
# the binary and the source it runs agree. The resolver asks git for the commit's stamp, which an
# export cannot answer: the live checkout is GOH_LIVE_ROOT, else the one that made the export
# (`.goh-live`). Pinned by tests/test_ship_pinned.py.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"
_goh_export="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [ ! -f "$_goh_export/.goh-head" ]; then
    echo "✗ goh: $0 is an export's bin/goh, and $_goh_export is no export (.goh-head)" >&2
    exit 2
fi
if [ -z "${GOH_LIVE_ROOT:-}" ] || [ ! -e "$GOH_LIVE_ROOT/.git" ]; then
    GOH_LIVE_ROOT="$(cat "$_goh_export/.goh-live" 2>/dev/null || true)"
fi
if [ -z "$GOH_LIVE_ROOT" ] || [ ! -e "$GOH_LIVE_ROOT/.git" ]; then
    echo "✗ goh: no gates_of_heck checkout to build $(cat "$_goh_export/.goh-head")'s goh from" \
        "(GOH_LIVE_ROOT unset, and $_goh_export/.goh-live names none)" >&2
    exit 2
fi
export GOH_LIVE_ROOT
# shellcheck source=gates/_goh_bin.sh
. "$_goh_export/gates/_goh_bin.sh"
goh_resolve_native
if [ -z "$goh_native" ] || [ "$(cd "$(dirname "$goh_native")" && pwd -P)/$(basename "$goh_native")" = "$_goh_export"/bin/goh ]; then
    echo "✗ goh: no native goh for $(cat "$_goh_export/.goh-head"): ${goh_native_why:-it resolved to this shim}" >&2
    exit 2
fi
export GOH_DIR="$_goh_export"
exec "$goh_native" "$@"
}
