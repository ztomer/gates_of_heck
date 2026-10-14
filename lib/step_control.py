#!/usr/bin/env python3
"""lib/step_control.py -- the controls a step's CPU is divided by (BACKLOG 5.3).

    GOH_TIMINGS=/tmp/t.jsonl python3 lib/step_control.py run shell [rust]   # one control row each

WHY. This box is never quiet. Across a 10x load a step's raw CPU moved +36% and its wall +84%: under
load more of the work runs on the efficiency cores, which spend more CPU on it. The same step's CPU
divided by a control of its OWN kind, run in the same sitting, moved +3.3% (shell) and -8.9% (Rust).
A control of another kind does not hold (a Python CPU loop read 2.8-3.5 at both loads), and nor does
a Rust control that runs serially where the step fills the cores (-19%): the control matches the
step's kind and its concurrency.

THE CONTROLS. Each is fixed work, pinned so two runs months apart divide by the same thing:
  shell -- `shellcheck -x` over 8 files of this repo at SHELL_COMMIT, read from git, never the tree.
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
SHELL_COMMIT = "e3e5418"
SHELL_FILES = (
    "checks/check_no_screen_linkage.sh",
    "checks/check_shell_lint.sh",
    "gates/_common.sh",
    "gates/_coverage_rust.sh",
    "gates/_from_head.sh",
    "gates/_git_env.sh",
    "gates/_goh_bin.sh",
    "gates/_hash.sh",
)
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


def _shell_tree(cache: Path) -> Path:
    """SHELL_COMMIT's checks/ and gates/, once: `-x` follows their sources, so the files need their
    neighbours."""
    tree = cache / "shell"
    if (tree / SHELL_FILES[0]).is_file():
        return tree
    tree.mkdir(parents=True, exist_ok=True)
    # From HEAD's export (gate_profile.sh runs from one) the files come from the live checkout it
    # was exported from: an export is not a git repository.
    source = os.environ.get("GOH_LIVE_ROOT") or str(ROOT)
    archive = subprocess.run(
        ["git", "-C", source, "archive", SHELL_COMMIT, "checks", "gates"],
        capture_output=True, check=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True)
    return tree


def shell_plan(cache: Path) -> list[Run]:
    tree = _shell_tree(cache)
    return [Run(tree, tree, ["shellcheck", "-x", *SHELL_FILES])]


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
    "shell": shell_plan,
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


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "run" and all(k in CONTROLS for k in argv[1:]):
        for kind in argv[1:]:
            run(kind)
        return 0
    unknown = [k for k in argv[1:] if k not in CONTROLS]
    print(
        f"usage: step_control.py run KIND... (kinds: {' '.join(CONTROLS)})"
        + (f"; unknown: {' '.join(unknown)}" if unknown else ""),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
