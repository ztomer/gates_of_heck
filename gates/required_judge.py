"""required_judge.py -- is a tool present, and at its floor (a pin: at it)? (G2b, GOH.PINCHECKFLOOR)

Split from required_tools.py at its line cap. `judge` reads one manifest row's tool (or the version
text the CALLER read, `--found`) against its floor; a PINNED row's floor is its pin, and in a goh
checkout it must be exactly the pin (required_pins.off_pin). `check` judges a selection and prints
each refusal; required_tools.py's `--check` calls it.

Helper of `required_tools.py` (`--check`), split from it at its line cap: `judge` reads one
manifest row's tool, or the version the caller read (`--found`), against its floor, and `check`
judges a selection. A PINNED row's floor is its pin (at least it in a consumer), exactly its pin in
a goh checkout (`required_pins.off_pin`, the judgement `--pins-held` makes) (GOH.PINCHECKFLOOR). A
pip row's version is read from its distribution metadata. Invoked by via required_tools.py, every
gate's `goh_require_floor`. Pinned by `test_required_tools_floors.py`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import required_pins  # the one judgement of "this release is the pin"

HERE = os.path.dirname(os.path.abspath(__file__))

# How each tool reports its version, where `<tool> --version` is not it (measured 2026-10-25):
# `zig --version` is a usage error, `cargo-llvm-cov --version` wants its subcommand first, and the
# Python modules are asked through the house interpreter seam, the one the gates run.
PROBES = {
    "zig": ["zig", "version"],
    "swiftlint": ["swiftlint", "version"],
    "cargo-llvm-cov": ["cargo-llvm-cov", "llvm-cov", "--version"],
    "python3": ["bash", os.path.join(HERE, "python.sh"), "-V"],
}
VERSION = re.compile(r"\d+(?:\.\d+)+")


# A pip row is read from its distribution's metadata through the gates' interpreter seam, the way
# required_pins.py reads it: `-m numpy --version` is no command, and `-m pytest-xdist` is no module.
# A distribution that is not there exits 1 and prints nothing.
_DIST_VERSION = (
    "import sys; from importlib import metadata as m\n"
    "try: print(m.version(sys.argv[1]))\n"
    "except m.PackageNotFoundError: sys.exit(1)"
)


def _probe(row: dict) -> list[str]:
    if row["tool"] in PROBES:
        return PROBES[row["tool"]]
    if row["kind"] == "pip":
        return ["bash", os.path.join(HERE, "python.sh"), "-c", _DIST_VERSION, row["tool"]]
    return [row["tool"], "--version"]


def is_goh_tree(cwd: str | None = None) -> bool:
    """Is `cwd` (default: the working directory) inside a gates_of_heck checkout? The toplevel
    marker gates/_self_gate.sh uses: gates/commit_gate.sh, gates/_from_head.sh and crates/goh/."""
    try:
        top = subprocess.run(
            ["git", "-C", cwd or ".", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        ).stdout.strip()
    except OSError:  # no git on PATH: the directory itself is the toplevel to look at
        top = ""
    top = top or os.path.abspath(cwd or ".")
    return (
        os.path.isfile(os.path.join(top, "gates", "commit_gate.sh"))
        and os.path.isfile(os.path.join(top, "gates", "_from_head.sh"))
        and os.path.isdir(os.path.join(top, "crates", "goh"))
    )


def version_in(text: str) -> str | None:
    match = VERSION.search(text)
    return match.group(0) if match else None


def at_least(found: str, floor: str) -> bool:
    """found >= floor, numerically, field by field ("0.6.24" >= "0.6.9"; "1.0" >= "0.99.1")."""

    def key(text: str) -> list[int]:
        return [int(part) for part in text.split(".")]

    a, b = key(found), key(floor)
    width = max(len(a), len(b))
    return a + [0] * (width - len(a)) >= b + [0] * (width - len(b))


def judge(row: dict, found_text: str | None = None, exact: bool = False) -> str | None:
    """Why `row`'s tool fails its requirement, or None when it is present and at its floor.

    A PINNED tool's floor is its pin (GOH.PINCHECKFLOOR: GOH.PYTESTPIN left `--check` judging its
    presence alone, and a box below the old floor passed), the oldest release a consumer accepts,
    the one `--floors` publishes. `exact` (a goh checkout) is stricter: exactly the pin, the
    release hosted CI installs, judged by required_pins.off_pin (the one `--pins-held` makes)."""
    tool, floor = row["tool"], row["pin"] or row["floor"]
    if found_text is None:
        probe = _probe(row)
        if probe[0] != "bash" and not shutil.which(probe[0]):
            return f"{tool} is not installed -- {row['install']}"
        if floor == "-":
            return None
        try:
            done = subprocess.run(
                probe,
                capture_output=True,
                text=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"{tool}: its version cannot be read ({error}); the floor is {floor}"
        if done.returncode != 0 and row["kind"] == "pip" and not version_in(done.stdout):
            return f"{tool} is not installed -- {row['install']}"
        found_text = done.stdout + "\n" + done.stderr
    if floor == "-":
        return None
    found = version_in(found_text)
    if found is None:
        return f"{tool}: present, but reports no version the floor ({floor}) can be read against"
    if exact and row["pin"]:
        return required_pins.off_pin(row, found)
    if not at_least(found, floor):
        return (
            f"{tool}: this machine has {found}, the floor is {floor} -- update it "
            f"({remedy(row, floor)}; the floor's reason is in gates/required_tools.tsv)"
        )
    return None


def _prefix() -> str:
    """The gates' interpreter's sys.prefix, through the seam the gates run ("" if unreadable)."""
    argv = ["bash", os.path.join(HERE, "python.sh"), "-c", "import sys; print(sys.prefix)"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _locked(lock: str, tool: str) -> str | None:
    """TOOL's version in a uv.lock's text, or None."""
    want = re.escape(tool.lower().replace("_", "-"))
    m = re.search(rf'\[\[package\]\]\nname = "{want}"\nversion = "([^"]+)"', lock)
    return m.group(1) if m else None


def remedy(row: dict, floor: str) -> str:
    """The fix, by how the repo resolves python (GOH.FLOORHINTUV, GOH.VENVDRIFT): a pip row whose
    interpreter is a uv project's .venv gets `uv sync --frozen` when the lock already meets the
    floor (the venv drifted from its lock), else `uv lock --upgrade-package`; any other the
    manifest's install line (a pip line moves no frozen lock)."""
    if row["kind"] != "pip":
        return row["install"]
    prefix = _prefix()
    project = os.path.dirname(prefix) if os.path.basename(prefix) == ".venv" else ""
    try:
        with open(os.path.join(project, "uv.lock"), encoding="utf-8") as f:
            lock = f.read()
    except OSError:
        return row["install"]
    tool, locked = row["tool"], _locked(lock, row["tool"])
    if locked and at_least(locked, floor):
        return f"its uv.lock pins {locked} but the .venv drifted: `uv sync --frozen` in {project}"
    return (f"`uv lock --upgrade-package {tool}` in {project} (and a floor of {floor} in its "
            "pyproject), then `uv sync --frozen`")  # fmt: skip


def check(selected: list[dict], found_text: str | None = None) -> int:
    failures: list[str] = []
    exact = (
        any(r["pin"] for r in selected) and is_goh_tree()
    )  # goh: a pin is exact; a consumer: the floor
    for row in {r["tool"]: r for r in selected}.values():
        reason = judge(row, found_text, exact)
        if reason:
            failures.append(reason)
    for reason in failures:
        print(f"required_tools: {reason}", file=sys.stderr)
    return 1 if failures else 0
