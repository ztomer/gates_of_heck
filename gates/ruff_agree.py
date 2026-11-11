#!/usr/bin/env python3
"""ruff_agree.py -- every way this machine runs ruff is the SAME ruff (GOH.TOOLHYGIENE, rule 17).

On 2026-11-11 `python3 -m ruff` (the house interpreter's site-packages, what gates/_py.sh's
runner runs) was 0.17.0 while the `ruff` on PATH (~/.local/bin, /opt/homebrew/bin: what
crates/goh/src/pyformat.rs falls back to, and what an agent types) was 0.17.4. CI installs the
newest into the house interpreter (`python3 -m pip install ruff`), so a gate's verdict depended on
how it was invoked, and the local one was not CI's. A ruff release can move a rule or the formatter:
two rufs are two gates.

This asks every invocation -- `"$GOH_PY" -m ruff --version` and each distinct `ruff` on PATH -- and
exits 1 when they report different versions, naming each one with its version and the command
that brings the older ones up to the newest (the house runs the newest release). One ruff, or
none on PATH, agrees with itself: exit 0. Run by tools/pytest.sh before the suite, so goh's own
gate fails on the split locally and in CI alike.

    python3 gates/ruff_agree.py          # GOH_PY: the house interpreter (lib/house_python.sh)
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

VERSION = re.compile(r"\bruff (\d+(?:\.\d+)+)")


def _version(argv: list[str]) -> str | None:
    try:
        out = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = VERSION.search(out.stdout + out.stderr)
    return m.group(1) if out.returncode == 0 and m else None


def _on_path(path: str) -> list[Path]:
    """Every executable `ruff` on PATH, in PATH order, one per real file."""
    seen: set[Path] = set()
    found: list[Path] = []
    for d in path.split(os.pathsep):
        cand = Path(d or ".") / "ruff"
        if not (cand.is_file() and os.access(cand, os.X_OK)):
            continue
        real = cand.resolve()
        if real not in seen:
            seen.add(real)
            found.append(cand)
    return found


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _wheel_bin(py: str) -> Path | None:
    """The binary the interpreter's ruff wheel installed (pip puts it in that python's bin/, which
    for Homebrew's python is /opt/homebrew/bin: on 2026-11-11 it shadowed brew's own ruff)."""
    try:
        out = subprocess.run([py, "-c", "import ruff; print(ruff.find_ruff_bin())"],
                             capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = out.stdout.strip()
    return Path(text).resolve() if out.returncode == 0 and text else None


def _upgrade(py: str, path: Path | None) -> str:
    pip = f'"{py}" -m pip install -U ruff'
    if path is None or path.resolve() == _wheel_bin(py):
        return pip
    text = str(path.resolve())
    if "/Cellar/ruff/" in text:
        return "brew upgrade ruff"
    if "/uv/tools/" in text:
        return "uv tool upgrade ruff"
    return f"replace {path} with the newest ruff"


def invocations(py: str, path: str) -> list[tuple[str, Path | None, str | None]]:
    """(how it is invoked, the PATH entry or None for the interpreter's, its version or None)."""
    out: list[tuple[str, Path | None, str | None]] = []
    if py:
        out.append((f"{py} -m ruff", None, _version([py, "-m", "ruff"])))
    out += [(str(p), p, _version([str(p)])) for p in _on_path(path)]
    return out


def main() -> int:
    py = os.environ.get("GOH_PY") or sys.executable
    found = [x for x in invocations(py, os.environ.get("PATH", "")) if x[2]]
    versions = {v for _, _, v in found if v}
    if len(versions) <= 1:
        return 0
    newest = max(versions, key=_key)
    print(f"✗ ruff: {len(found)} invocations, {len(versions)} versions -- a gate's verdict "
          "depends on how ruff was invoked (rule 17: local gate identical to CI):",
          file=sys.stderr)  # fmt: skip
    for label, where, v in found:
        mark = "newest" if v == newest else f"older: {_upgrade(py, where)}"
        print(f"    {label}: ruff {v} ({mark})", file=sys.stderr)
    print(f"  bring every one to ruff {newest}, then rerun", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
