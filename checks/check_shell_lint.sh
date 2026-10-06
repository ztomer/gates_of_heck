#!/usr/bin/env bash
# Retired (Phase N3): this check is `goh shell-lint`. Kept so a repo calling this file by path keeps
# working; call `$GOH_DIR/gates/goh.sh shell-lint` instead.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../gates/goh.sh" shell-lint "$@"
exit
} # parse-guard
