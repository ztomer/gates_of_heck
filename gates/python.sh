#!/usr/bin/env bash
# gates/python.sh -- the ONE way a hook or a gate script runs Python (BACKLOG 6.2):
#
#   bash "$GOH/gates/python.sh" tools/check_roadmap.py --staged
#
# A bare `python3` in a hook is whichever interpreter Homebrew installed last, with whatever
# PYTHONPATH the shell that made the commit happened to export. On 2026-10-10 Homebrew moved
# python3 to 3.15 and three repos' gates broke three ways at once, none of them naming the
# interpreter. This runs the repo's PINNED interpreter (gates/_py.sh: a .venv, else the version
# GOH_PYTHON_VERSION or a committed .python-version names, found and read back, else python3), with
# the house libraries on its path and the shell's PYTHONPATH dropped -- so `import tui` works in
# every hook without an export in ~/.zshrc, and a hook run from a GUI client behaves as one run
# from a terminal. The structural step "no bare python in a hook or gate" refuses the other way.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../tui/lib.sh"
. "$HERE/_py.sh"
GOH_NAME="python"
py="$(goh_py_interpreter)"
exec env -u PYTHONHOME PYTHONPATH="$(cd "$HERE/.." && pwd)" "$py" "$@"
exit
} # parse-guard
