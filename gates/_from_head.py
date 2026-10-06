"""The Python entry points' half of C4: a checker run from the shared CHECKOUT runs HEAD's copy.

`gates/_from_head.sh` re-runs every bash entry point from an immutable export of the gates
checkout's HEAD. A Python checker called DIRECTLY -- `python3 "$GOH_DIR/checks/check_no_emoji.py"`,
how CadGoose2's step list and koffee_big's gates_lint.sh call the house checkers -- went around it
and judged with whatever was uncommitted in the shared working tree (BACKLOG C4 residuals). Every
script entry point now starts with

    if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gates"))
        try:
            __import__("_from_head").reexec(__file__)
        except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
            pass

and `tests/test_python_from_head.py` refuses one that does not. The export itself is built by the
bash helper, so there is one definition of it: this file only asks for it and execs the copy.

FREE where it should be: the gates already run checkers from the export, which carries
`.goh-head`, and that is one stat. `GOH_LIVE=1` runs the working tree on purpose, as for bash.
"""

from __future__ import annotations

import os
import subprocess
import sys

_ASK = '. "$1/gates/_from_head.sh" && _goh_head_dir "$2" && printf "%s\\n%s\\n%s\\n" "$_goh_copy" "$GOH_DIR" "$PYTHONPATH"'


def reexec(script: str) -> None:
    """Exec HEAD's copy of `script` when it runs from a live checkout; else return and run it."""
    if os.environ.get("GOH_LIVE"):
        return
    here = os.path.dirname(os.path.realpath(script))
    root = os.path.dirname(here)
    if os.path.isfile(os.path.join(root, ".goh-head")) or not os.path.exists(
        os.path.join(root, ".git")
    ):
        return  # an export already, or not a checkout at all
    got = subprocess.run(
        ["bash", "-c", _ASK, "_from_head", root, os.path.realpath(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    if got.returncode == 2:  # the helper's own refusal (a test without GOH_LIVE): say it, stop
        sys.stderr.write(got.stderr)
        sys.exit(2)
    lines = got.stdout.splitlines()
    if got.returncode != 0 or len(lines) < 2 or not lines[0]:
        sys.stderr.write(got.stderr)
        return  # no export (a warning was printed) or a file HEAD does not have: run this one
    copy, export = lines[0], lines[1]
    env = dict(os.environ, GOH_LIVE_ROOT=root, GOH_DIR=export)
    env["PYTHONPATH"] = lines[2] if len(lines) > 2 else export
    sys.stdout.flush()
    os.execve(sys.executable, [sys.executable, copy, *sys.argv[1:]], env)
