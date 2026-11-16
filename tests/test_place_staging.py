"""A copy killed mid-stage leaves no untracked file in the checkout (GOH.STAGINGSTRAY).

2026-11-14 21:57: the gates_of_heck checkout held an untracked `bin/goh.10644.staging`, exactly
2 MiB of a 9 MB arm64 binary. `gates/_goh_bin.sh` places a store build at `bin/goh` by copying it
to `bin/goh.<pid>.staging` and renaming it in; pid 10644 was killed mid-`cp`, the rename never
ran, and the half copy made the checkout read "1 uncommitted" (the GOH.STRAYLOCK class).
`scripts/build-goh.sh` staged its own `bin/.goh.<pid>` the same way. Both now stage through ONE
helper (`lib/place.sh`): the staging name is ignored by the checkout's `.gitignore`, a failed copy
removes its stage, and the next placement reaps every stage whose process is gone.
Since GOH.CLASS.CHECKOUT part 2 the stage lives in the destination directory's run directory
(lib/run_path.sh), outside the work tree, and beside DEST only when the run root is on another
filesystem (rename(2) is atomic only within one).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import GiveUp, patience

PLACE = REPO_ROOT / "lib" / "place.sh"


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A checkout with goh's own .gitignore and a committed tree, so `git status` is the judge."""
    repo = tmp_path / "goh"
    fast_init(repo)
    shutil.copy2(REPO_ROOT / ".gitignore", repo / ".gitignore")
    (repo / "bin").mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return repo


def _status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all", "--ignored"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout


def _place(src: Path, dest: Path, path: str | None = None, *, other_device: bool = False
           ) -> subprocess.Popen:  # fmt: skip
    """goh_place SRC DEST with the run root beside the checkout (one filesystem); OTHER_DEVICE
    redefines the device probe so the run root reads as another filesystem."""
    env = hermetic_env(GOH_RUN_DIR=str(_run(dest)), **({"PATH": path} if path else {}))
    seam = (
        ' _goh_place_dev() { [ "${1#"$GOH_RUN_DIR"}" != "$1" ] && echo 1 || echo 2; };'
        if other_device
        else ""
    )
    return subprocess.Popen(
        [
            "bash",
            "-c",
            '. "$1" &&' + seam + ' goh_place "$2" "$3"',
            "_",
            str(PLACE),
            str(src),
            str(dest),
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _run(dest: Path) -> Path:
    """The run root of a test: beside the checkout, outside its work tree."""
    return dest.parent.parent.parent / "run"


def _stages(dest: Path) -> list[str]:
    return sorted(p.name for p in _run(dest).glob("*/*.staging"))


@pytest.mark.load_sensitive
def test_a_placement_killed_mid_copy_leaves_the_checkout_clean_and_is_reaped(checkout, tmp_path):
    src = tmp_path / "goh-built"
    src.write_bytes(b"\x7fELF" + b"\0" * (4 << 20))
    # A cp that writes the first 2 MiB and then hangs: the copy the 21:57 kill interrupted.
    shim = tmp_path / "slow-path"
    shim.mkdir()
    started = tmp_path / "started"
    (shim / "cp").write_text(
        f'#!/bin/bash\nhead -c 2097152 "$1" > "$2"\n: > "{started}"\nexec sleep 60\n'
    )
    (shim / "cp").chmod(0o755)
    dest = checkout / "bin" / "goh"
    proc = _place(src, dest, f"{shim}:/usr/bin:/bin")
    try:
        give_up = GiveUp(30)
        while not started.exists() and not give_up.passed():
            time.sleep(0.05)
        assert started.exists(), "the copy never started"
    finally:
        subprocess.run(["kill", "-KILL", f"-{proc.pid}"], check=False, stdin=subprocess.DEVNULL)
        proc.communicate()
    stages = _stages(dest)
    assert len(stages) == 1 and stages[0].startswith("goh."), stages  # the instrument: it IS there
    assert list((checkout / "bin").iterdir()) == []
    assert _status(checkout) == "", "a killed placement left a file in the checkout"
    # The next placement reaps the dead process's stage and lands the whole binary.
    again = _place(src, dest)
    _, errs = again.communicate(timeout=60)
    assert again.returncode == 0, errs
    assert sorted(p.name for p in (checkout / "bin").iterdir()) == ["goh"]
    assert _stages(dest) == []
    assert dest.read_bytes() == src.read_bytes()


@pytest.mark.load_sensitive
def test_a_failed_copy_removes_its_own_stage(checkout, tmp_path):
    shim = tmp_path / "failing-path"
    shim.mkdir()
    (shim / "cp").write_text('#!/bin/bash\nhead -c 10 /dev/zero > "$2"\nexit 1\n')
    (shim / "cp").chmod(0o755)
    src = tmp_path / "goh-built"
    src.write_bytes(b"x" * 100)
    proc = _place(src, checkout / "bin" / "goh", f"{shim}:/usr/bin:/bin")
    proc.communicate(timeout=60)
    assert proc.returncode != 0
    assert list((checkout / "bin").iterdir()) == []
    assert _stages(checkout / "bin" / "goh") == []


@pytest.mark.load_sensitive
def test_a_live_placements_stage_is_never_reaped(checkout, tmp_path):
    # Reaping goes by the pid in the name: a stage whose process still runs is mid-copy, not stray.
    live = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL)
    try:
        dest = checkout / "bin" / "goh"
        stage = _run(dest) / "live" / f"goh.{live.pid}.staging"
        legacy = checkout / "bin" / f"goh.{live.pid}.staging"
        stage.parent.mkdir(parents=True)
        for half in (stage, legacy):
            half.write_bytes(b"half")
        src = tmp_path / "goh-built"
        src.write_bytes(b"whole")
        proc = _place(src, dest)
        proc.communicate(timeout=60)
        assert proc.returncode == 0
        assert legacy.exists()
    finally:
        live.kill()
        live.communicate()


@pytest.mark.load_sensitive
def test_a_dead_stage_left_beside_dest_by_an_older_goh_is_reaped(checkout, tmp_path):
    gone = subprocess.Popen(["true"], stdin=subprocess.DEVNULL)
    gone.communicate()  # reaped: its pid names no process now
    legacy = checkout / "bin" / f"goh.{gone.pid}.staging"
    legacy.write_bytes(b"half")
    src = tmp_path / "goh-built"
    src.write_bytes(b"whole")
    proc = _place(src, checkout / "bin" / "goh")
    proc.communicate(timeout=patience(60))
    assert proc.returncode == 0
    assert sorted(p.name for p in (checkout / "bin").iterdir()) == ["goh"]


@pytest.mark.load_sensitive
def test_a_run_root_on_another_filesystem_stages_beside_dest(checkout, tmp_path):
    # rename(2) is atomic only within one filesystem: the seam reads the run root as another one.
    shim = tmp_path / "slow-path"
    shim.mkdir()
    started = tmp_path / "started"
    (shim / "cp").write_text(f'#!/bin/bash\n: > "$2"\n: > "{started}"\nexec sleep 60\n')
    (shim / "cp").chmod(0o755)
    src = tmp_path / "goh-built"
    src.write_bytes(b"whole")
    dest = checkout / "bin" / "goh"
    proc = _place(src, dest, f"{shim}:/usr/bin:/bin", other_device=True)
    try:
        give_up = GiveUp(30)
        while not started.exists() and not give_up.passed():
            time.sleep(0.05)
        assert started.exists(), "the copy never started"
        beside = [p.name for p in (checkout / "bin").iterdir()]
        assert len(beside) == 1 and beside[0].startswith("goh.") and beside[0].endswith(".staging")
        assert _stages(dest) == []
    finally:
        subprocess.run(["kill", "-KILL", f"-{proc.pid}"], check=False, stdin=subprocess.DEVNULL)
        proc.communicate()


def test_every_binary_placement_stages_through_the_one_helper():
    # The class: a hand-rolled `cp X bin/...; mv` beside the helper is the stray this closed.
    for rel in ("gates/_goh_bin.sh", "scripts/build-goh.sh"):
        text = (REPO_ROOT / rel).read_text()
        assert '.staging"' not in text and "/bin/.goh." not in text, rel
        assert "goh_place" in text, rel
