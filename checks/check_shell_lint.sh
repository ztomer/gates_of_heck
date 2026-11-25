#!/usr/bin/env bash
# Retired (Phase N3): this check is `goh shell-lint`. Kept so a repo calling this file by path keeps
# working; call `$GOH_DIR/gates/goh.sh shell-lint` instead.
#
# Forwarder (Phase N3): execs `gates/goh.sh shell-lint` with the same arguments, for a consumer
# that calls it by path; imported, it raises.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../gates/goh.sh" shell-lint "$@"
exit
} # parse-guard
