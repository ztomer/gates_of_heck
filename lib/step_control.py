#!/usr/bin/env python3
"""lib/step_control.py -- the controls a step's CPU is divided by (BACKLOG 5.3).

    GOH_TIMINGS=/tmp/t.jsonl python3 lib/step_control.py run rust   # one control row
    python3 lib/step_control.py kinds REPO    # the controls REPO's gate needs, one per line

WHY. This box is never quiet. Across a 2x load clippy's raw CPU moved +17%: under load more of the
work runs on the efficiency cores, which spend more CPU on it. Divided by a control of its OWN kind
and concurrency, run in the same sitting, it moved +1% and +2% (three A-B-B-A profiles, 2026-10-14).
A control of another kind does not hold (a Python CPU loop read 2.8-3.5 at both loads), nor does a
serial one where the step fills the cores (-19%). The native tier's in-process steps need none: their
raw CPU held (+4%, -1%) where every shell control tried moved 15-17% (`step_timings.RAW_KINDS`).

THE CONTROL. Fixed work, pinned so two runs months apart divide by the same thing:
  rust  -- `cargo clippy` of RUST_CRATE, RUST_COPIES copies AT ONCE (CPU summed), each warm after a
           touch of its root. The crate is fetched once into the cache, the first run warms it.

A control's row carries `"control": true` and its kind; `lib/step_timings.py report` divides.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_timings  # noqa: E402  # beside this file

ROOT = Path(__file__).resolve().parent.parent
CACHE_ENV = "GOH_STEP_CONTROL_CACHE"
RUST_CRATE = ("regex-syntax", "0.8.11")
RUST_COPIES = 4


@dataclass(frozen=True)
class Run:
    """One process of a control: where it runs, what it builds into, what it is."""

    cwd: Path
    target: Path
    argv: list[str]


def cache_root() -> Path:
    explicit = os.environ.get(CACHE_ENV)
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "goh" / "step-control"


def _crate_source(cache: Path) -> Path:
    """RUST_CRATE's source, fetched once through cargo into the cache."""
    name, version = RUST_CRATE
    src = cache / "rust" / "src"
    found = next(src.glob(f"*/{name}-{version}"), None) if src.is_dir() else None
    if found:
        return found
    probe = cache / "rust" / "fetch"
    (probe / "src").mkdir(parents=True, exist_ok=True)
    (probe / "src" / "lib.rs").write_text("")
    (probe / "Cargo.toml").write_text(
        f'[package]\nname = "goh-control-fetch"\nversion = "0.0.0"\nedition = "2021"\n\n'
        f'[dependencies]\n{name} = "={version}"\n'
    )
    env = dict(os.environ, CARGO_HOME=str(cache / "rust" / "cargo-home"))
    subprocess.run(
        ["cargo", "fetch", "-q"], cwd=probe, env=env, check=True, stdin=subprocess.DEVNULL
    )
    registry = cache / "rust" / "cargo-home" / "registry" / "src"
    fetched = next(registry.glob(f"*/{name}-{version}"))
    shutil.copytree(fetched, src / "registry" / f"{name}-{version}")
    return src / "registry" / f"{name}-{version}"


def rust_plan(cache: Path) -> list[Run]:
    name, version = RUST_CRATE
    copies = [cache / "rust" / f"copy{i}" / f"{name}-{version}" for i in range(RUST_COPIES)]
    return [Run(c, c.parent / "target", ["cargo", "clippy", "--offline", "-q"]) for c in copies]


def _prepare_rust(cache: Path) -> list[Run]:
    plan = rust_plan(cache)
    if all((r.cwd / "Cargo.toml").is_file() for r in plan):
        return plan
    source = _crate_source(cache)
    for r in plan:
        if not r.cwd.is_dir():
            shutil.copytree(source, r.cwd)
    _run_all(plan)  # the first build is cold; a control is a warm build after a touch
    return plan


def _run_all(plan: list[Run]) -> int:
    """Every process at once; the worst exit code."""
    for r in plan:
        lib = r.cwd / "src" / "lib.rs"
        if lib.is_file():
            os.utime(lib)
    procs = [
        subprocess.Popen(
            r.argv,
            cwd=r.cwd,
            env=dict(os.environ, CARGO_TARGET_DIR=str(r.target)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip
        for r in plan
    ]
    return max(p.wait() for p in procs)


CONTROLS = {
    "rust": _prepare_rust,
}


def run(kind: str) -> int:
    """Run `kind`'s control and record its row. The control's own exit code is recorded, not
    returned: shellcheck's findings in a pinned file are part of the fixed work."""
    plan = CONTROLS[kind](cache_root())
    t0, cpu0 = time.monotonic(), step_timings.children_cpu_ms()
    rc = _run_all(plan)
    step_timings.record(
        f"control:{kind}", (time.monotonic() - t0) * 1000, rc, "control",
        cpu_ms=step_timings.children_cpu_ms() - cpu0, kind=kind, control=True,
    )  # fmt: skip
    return 0


def kinds_for(repo: Path) -> list[str]:
    """The controls a repo's gate needs: rust when git tracks a Cargo.toml at its root or in any
    directory (rust_each_crate.sh's rule; media_server's 29 crates have no root manifest)."""
    listed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--", "Cargo.toml", "*/Cargo.toml"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return ["rust"] if listed.returncode == 0 and listed.stdout.strip() else []


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "kinds":
        print("\n".join(kinds_for(Path(argv[1]))))
        return 0
    if len(argv) >= 2 and argv[0] == "run" and all(k in CONTROLS for k in argv[1:]):
        for kind in argv[1:]:
            run(kind)
        return 0
    unknown = [k for k in argv[1:] if k not in CONTROLS]
    print(
        f"usage: step_control.py run KIND... | kinds REPO (kinds: {' '.join(CONTROLS)})"
        + (f"; unknown: {' '.join(unknown)}" if unknown else ""),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
