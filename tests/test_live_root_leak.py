"""A gate run from a checkout resolves its goh from THAT checkout, whatever GOH_LIVE_ROOT says.

GOH_LIVE_ROOT exists for an EXPORT of HEAD (gates/_from_head.sh): an export has no bin/, so its
resolver finds the binary in the live checkout the export was made from. It is exported, so it
leaks: goh ship runs its release step from the main checkout's export, the release kit
(tools/release_goh.sh) runs the suite in its release worktree, and every gate a test ran there
resolved its binary from the MAIN checkout's store -- 0.118.0 serving a tree that declared 0.119.0,
"the goh binary serving this gate is BEHIND the source", 80 tests red (the v0.119.0 ship,
2026-11-15). The worktree's place did not matter: inside the checkout (build/release-vX, where it
used to be) and outside it (the run root, lib/run_path.sh) both read the leaked root.

So the resolver honours GOH_LIVE_ROOT only when it runs from an export (`.goh-head` at its root).
The fixture is a goh-shaped checkout with no crates (so the resolver takes `bin/goh` as is), a stub
`bin/goh` naming each tree, and a linked release worktree in both places.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

STUB = "#!/bin/sh\necho {name}\n"
RESOLVE = '. "$1/gates/_goh_bin.sh" && goh_resolve_native && "$goh_native"'


def _goh_tree(root: Path, name: str) -> None:
    for d in ("gates", "lib", "tui"):
        shutil.copytree(REPO_ROOT / d, root / d, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))  # fmt: skip
    _stub(root, name)


def _stub(root: Path, name: str) -> None:
    (root / "bin").mkdir(exist_ok=True)
    (root / "bin" / "goh").write_text(STUB.format(name=name))
    (root / "bin" / "goh").chmod(0o755)


def _resolve(tree: Path, live_root: Path, tmp_path: Path) -> str:
    env = hermetic_env(drop_git=True, GOH_LIVE_ROOT=str(live_root), GOH_RUN_DIR=str(tmp_path / "r"))
    env.pop("GOH_LIVE", None)
    done = subprocess.run(["bash", "-c", RESOLVE, "_", str(tree)], cwd=tree, env=env,
                          capture_output=True, text=True, timeout=patience(120),
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.strip()


@pytest.fixture
def main_checkout(tmp_path: Path) -> Path:
    main = tmp_path / "gates_of_heck"
    fast_init(main)
    _goh_tree(main, "main")
    (main / ".gitignore").write_text("/bin/\n/build/\n")
    git(main, "add", "-A")
    git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    return main


@pytest.mark.parametrize("place", ["outside", "inside"])
def test_a_release_worktree_resolves_its_own_goh_under_a_leaked_live_root(
    main_checkout: Path, tmp_path: Path, place: str
) -> None:
    wt = tmp_path / "run" / "release" if place == "outside" else main_checkout / "build" / "rel"
    git(main_checkout, "worktree", "add", "-q", "--detach", str(wt))
    _stub(wt, "release")
    assert _resolve(main_checkout, main_checkout, tmp_path) == "main"  # calibration: stubs answer
    assert _resolve(wt, main_checkout, tmp_path) == "release"


def test_an_export_still_finds_its_binary_in_the_live_root(
    main_checkout: Path, tmp_path: Path
) -> None:
    """The one job GOH_LIVE_ROOT has: an export (no bin/) resolves from the live checkout."""
    export = tmp_path / "export"
    for d in ("gates", "lib", "tui"):
        shutil.copytree(main_checkout / d, export / d)
    (export / ".goh-head").write_text(git(main_checkout, "rev-parse", "HEAD").strip() + "\n")
    assert _resolve(export, main_checkout, tmp_path) == "main"
