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
    python3 gates/ruff_agree.py --pin 0.17.4

--pin VERSION (GOH.CIRUFF, the hosted runner): every invocation must BE that release, and the house
interpreter's `-m ruff` must exist. Run 43139901867 failed goh's rust coverage step on "ruff: 3
invocations, 2 versions": the runner got one ruff from the manifest's brew row and another from
its unpinned pip row, and the split surfaced as a coverage loss. .github/workflows/gate.yml
installs one ruff, GOH_CI_RUFF, and asks this in that provisioning step, so a runner that carries
another ruff fails there, named. tools/pytest.sh gives goh's own pin (gates/ruff_house.py RUFF,
GOH.RUFFVERSION), so the local suite and the runner hold every ruff to the same release.

Every way the machine runs ruff (`"$GOH_PY" -m ruff` and each distinct `ruff` on PATH) reports ONE
version, or exit 1 naming each with its version and its upgrade (GOH.TOOLHYGIENE: 0.17.0 beside
0.17.4 made a verdict depend on the invocation). `--pin VERSION`: every one IS that release
(GOH.CIRUFF; the hosted workflow installs one ruff, `GOH_CI_RUFF`, and asks this in its
provisioning step). Invoked by lib/suite_preflight.py (tools/pytest.sh), before the suite;
`.github/workflows/gate.yml` with `--pin`.
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
from concurrent.futures import ThreadPoolExecutor
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
    """(how it is invoked, the PATH entry or None for the interpreter's, its version or None), all
    asked at once (GOH.PYHARNESS): each is a process the machine waits on, not work."""
    asked: list[tuple[str, Path | None, list[str]]] = []
    if py:
        asked.append((f"{py} -m ruff", None, [py, "-m", "ruff"]))
    asked += [(str(p), p, [str(p)]) for p in _on_path(path)]
    with ThreadPoolExecutor(max_workers=max(1, len(asked))) as pool:
        found = list(pool.map(_version, [argv for _, _, argv in asked]))
    return [(label, where, v) for (label, where, _), v in zip(asked, found, strict=True)]


def off_pin(py: str, pin: str, every: list[tuple[str, Path | None, str | None]]) -> int:
    """1, naming each on stderr, when an invocation of `every` is not the ruff `pin`; else 0."""
    off = [(label, v) for label, _, v in every if v != pin and (v or label.endswith(" -m ruff"))]
    if not off:
        return 0
    print(f"✗ ruff: the pinned ruff {pin} is not the only ruff here -- the gate would judge with "
          "another (GOH.CIRUFF):", file=sys.stderr)  # fmt: skip
    for label, v in off:
        print(f"    {label}: {f'ruff {v}' if v else 'no ruff'}", file=sys.stderr)
    print(f'  install exactly "ruff=={pin}" into {py} and remove every other ruff on PATH',
          file=sys.stderr)  # fmt: skip
    return 1


def main() -> int:
    py = os.environ.get("GOH_PY") or sys.executable
    args = sys.argv[1:]
    if args[:1] == ["--pin"] and len(args) == 2:
        return off_pin(py, args[1], invocations(py, os.environ.get("PATH", "")))
    if args:
        print(f"usage: {sys.argv[0]} [--pin VERSION]", file=sys.stderr)
        return 2
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
