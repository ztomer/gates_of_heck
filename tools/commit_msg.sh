#!/usr/bin/env bash
# tools/commit_msg.sh MSGFILE -- goh's own commit-message layer (gates/commit_msg.sh runs it last).
# A commit whose staged diff touches the settings surface (QoS, worker counts and shares, gate slots,
# PRE_SLOTS: checks/check_setting_proof.py SURFACE) carries `Reading-lands:` and `Reading-lanes:`,
# each with a before and an after (GOH.SETTINGPROOF). The land asks again over the lane's range (a
# GOH_CI_STEPS step), so a `--no-verify` commit does not get past it.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/house_python.sh
. "$root/lib/house_python.sh"
exec "$GOH_PY" "$root/checks/check_setting_proof.py" staged "$1" "$root"
