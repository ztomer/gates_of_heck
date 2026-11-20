#!/usr/bin/env python3
"""ruff_currency.py -- a ruff on this box newer than goh's pin is a bump goh lands, never a red
(GOH.RUFFCURRENCY).

THE CLASS. goh judges with exactly one ruff (gates/required_tools.tsv `=X.Y.Z`, read by
gates/ruff_house.py) and refuses any other on the box (gates/ruff_agree.py --pin, tools/pytest.sh;
gates/_py.sh goh_py_ruff_pinned in every consumer). The box's ruffs are moved by updaters goh does
not own: `uv tool upgrade --all` (topgrade's custom `uv` command, which sys_updater runs),
topgrade's pip-review over the house interpreter, and a hand. ruff 0.17.6 was released 2026-11-19
15:55 UTC; nothing was red for ten hours, until 20:57 EST, when `~/.local/bin/ruff` (a uv tool)
moved to it. From then every goh land, ship and estate commit was refused ("the pinned ruff
0.17.5 is not the only ruff here") until a hand bump (e7bf6d43, GOH.RUFF0176). An exact pin plus
an independent updater is an estate-wide red at every ruff release that reaches the box.

THE RULING (option a, not b). Holding the updaters at goh's pin (b) breaks the house rule that every
layer runs the newest release, and it would have to hold three updaters goh does not own and a hand.
So the pin FOLLOWS the box: the release alone moves nothing (the box still agrees with itself and
with CI's pin), and the moment a newer ruff is INSTALLED, the next goh land carries the pin bump as
its own commit on the lane's tip, and every gate of that land judges with the ruff the box runs
and the bumped manifest has hosted CI install.

    ruff_currency.py                  # prints the newer ruff this box runs, or nothing (exit 0)
    ruff_currency.py --bump-tree ROOT # in ROOT (a checkout being landed): commit the bump, if due

--bump-tree is what tools/land.sh runs before its pre-step (and so before the enqueue's pre-step,
which is land.sh with GOH_LAND_PRE_ONLY). In ROOT, when the newest ruff on the box is newer than
ROOT's pin, it: brings the house interpreter's ruff to that release through the manifest's own pip
line (it is goh's interpreter and goh's pin, as lib/consumer_pins.py gives a consumer's .venv goh's
ruff); writes the version into every ruff row of the manifest and the pin's comment, the ONE place
it is written (GOH.CLASS.PARITY); adds the changelog fragment; and commits those paths alone,
through the repo's own hooks. The commit is the same bytes in every land that makes it, so when two
lanes both carry it, the second's rebase onto main drops its copy as already applied. A tree that
moved the pin itself, or a box that is not ahead, is left alone: no output, exit 0.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    # A copy outside any checkout has nothing to re-run from.
    with _contextlib.suppress(ModuleNotFoundError):
        __import__("_from_head").reexec(__file__)

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ruff_agree

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import gitrun  # lib/: git under a deadline, aimed at its repository (GOH.GITKILL)

MANIFEST = Path("gates") / "required_tools.tsv"
FRAGMENTS = Path("changelog")
# The pin's own comment line in the manifest's header ("#   ruff =0.17.6   PIN: ...").
COMMENT = re.compile(r"^(#\s+ruff =)(\d+(?:\.\d+)+)(\s)")
PIP_TIMEOUT = 600
GIT_TIMEOUT = 600


def _key(v: str) -> tuple[int, ...]:
    return ruff_agree._key(v)


def pin_of(text: str) -> str | None:
    """The manifest's ruff pin (the `=X.Y.Z` floor of its ruff rows), or None when it pins none."""
    for line in text.splitlines():
        cols = line.split("\t")
        if not line.startswith("#") and len(cols) == 6 and cols[1] == "ruff" and cols[5][:1] == "=":
            return cols[5][1:]
    return None


def newest_on_box(py: str, path: str) -> str | None:
    """The newest release any ruff invocation on this box reports (the house interpreter's
    `-m ruff`, every `ruff` on PATH), or None when none answers."""
    found = [v for _, _, v in ruff_agree.invocations(py, path) if v]
    return max(found, key=_key) if found else None


def ahead(pin: str, py: str, path: str) -> str | None:
    """The newer ruff this box runs than `pin`, or None."""
    newest = newest_on_box(py, path)
    return newest if newest and _key(newest) > _key(pin) else None


def bumped(text: str, old: str, new: str) -> str:
    """The manifest with every ruff row's `=old` and the pin's comment moved to `=new`."""
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n")
        cols = body.split("\t")
        if (
            not body.startswith("#")
            and len(cols) == 6
            and cols[1] == "ruff"
            and cols[5] == f"={old}"
        ):
            cols[5] = f"={new}"
            out.append("\t".join(cols) + line[len(body) :])
        elif (m := COMMENT.match(body)) and m.group(2) == old:
            out.append(m.group(1) + new + line[m.end(2) :])
        else:
            out.append(line)
    moved = "".join(out)
    if pin_of(moved) != new:
        raise ValueError(f"the manifest's ruff rows did not move from {old} to {new}")
    return moved


def item(version: str) -> str:
    """The bump's item id, in GOH.RUFF0176's form: GOH.RUFF + the release's digits."""
    return "GOH.RUFF" + "".join(version.split("."))


def fragment(old: str, new: str) -> str:
    # The same bytes in every land that writes it (no date, no sha): a second lane's copy of the
    # bump is then a patch main already has, and its rebase drops it.
    return (f"* ruff {new} is the house pin (was {old}): this box ran it, so the land that met it "
            "carried the bump (gates/ruff_currency.py, GOH.RUFFCURRENCY).\n")  # fmt: skip


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """A writer under its deadline (stopped by TERM, so no index.lock is stranded); a stopped one
    reads as a failure, never a half result."""
    done = gitrun.run_writer(list(args), repo=root, deadline_s=GIT_TIMEOUT)
    return done or subprocess.CompletedProcess(args, 124, "", f"git {args[0]} passed its deadline")


def _interpreter(py: str, want: str) -> str | None:
    """Bring the house interpreter's ruff to `want` (the manifest's pip line); None, or why not."""
    if ruff_agree._version([py, "-m", "ruff"]) == want:
        return None
    argv = [py, "-m", "pip", "install", "--quiet", f"ruff=={want}"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=PIP_TIMEOUT,
                              stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"`{' '.join(argv)}` did not run: {e}"
    if done.returncode != 0 or ruff_agree._version([py, "-m", "ruff"]) != want:
        said = " | ".join((done.stdout + done.stderr).strip().splitlines()[-2:])
        return f"`{' '.join(argv)}` left {py} without ruff {want} ({said or 'no output'})"
    return None


def bump_tree(root: Path, py: str, path: str) -> int:
    manifest = root / MANIFEST
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError:
        return 0  # not goh's tree: a consumer carries no manifest, its pin is goh's
    pin = pin_of(text)
    new = ahead(pin, py, path) if pin else None
    if not pin or not new:
        return 0
    name = item(new)
    info(f"ruff {new} is on this box and {root} pins {pin}: committing the pin bump ({name})")
    if why := _interpreter(py, new):
        err(f"ruff_currency: {why} -- the bump would judge with another ruff; nothing committed")
        return 1
    paths = [str(MANIFEST)]
    manifest.write_text(bumped(text, pin, new), encoding="utf-8")
    if (root / FRAGMENTS).is_dir():
        rel = FRAGMENTS / f"{name}.md"
        (root / rel).write_text(fragment(pin, new), encoding="utf-8")
        paths.append(str(rel))
    msg = (f"{name}: ruff {new} is the house pin\n\n"
           f"This box runs ruff {new}, newer than the pin {pin}: a uv tool, pip or brew updater "
           "moved it. The land that met it commits the bump on its tip, its own commit "
           "(gates/ruff_currency.py --bump-tree, run by tools/land.sh; GOH.RUFFCURRENCY), so its "
           "gates judge with the ruff the box runs and the manifest has hosted CI install.\n"
           )  # fmt: skip
    add = _git(root, "add", "--", *paths)
    done = _git(root, "commit", "-q", "-m", msg, "--", *paths) if add.returncode == 0 else add
    if done.returncode != 0:
        _git(root, "checkout", "HEAD", "--", str(MANIFEST))
        if len(paths) > 1:
            _git(root, "rm", "-q", "--cached", "--ignore-unmatch", "--", paths[1])
            (root / paths[1]).unlink(missing_ok=True)
        said = " | ".join((done.stdout + done.stderr).strip().splitlines()[-3:]) or "no output"
        err(f"ruff_currency: the bump's commit was refused, the tree is as it was: {said}")
        return 1
    sha = gitrun.run_reader(
        ["rev-parse", "--short=8", "HEAD"], repo=root, timeout=60
    ).stdout.strip()
    ok(f"ruff_currency: {sha} {name}: ruff {new} is the house pin (was {pin})")
    return 0


def main() -> int:
    py = os.environ.get("GOH_PY") or sys.executable
    path = os.environ.get("PATH", "")
    args = sys.argv[1:]
    if not args:
        here = Path(__file__).resolve().parent.parent
        pin = pin_of((here / MANIFEST).read_text(encoding="utf-8"))
        new = ahead(pin, py, path) if pin else None
        if new:
            print(new)
        return 0
    if args[0] == "--bump-tree" and len(args) == 2:
        return bump_tree(Path(args[1]), py, path)
    print(f"usage: {sys.argv[0]} [--bump-tree ROOT]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
