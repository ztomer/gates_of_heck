"""A copy killed mid-stage leaves no untracked file in the checkout (GOH.STAGINGSTRAY).

2026-11-14 21:57: the gates_of_heck checkout held an untracked `bin/goh.10644.staging`, exactly
2 MiB of a 9 MB arm64 binary. `gates/_goh_bin.sh` places a store build at `bin/goh` by copying it
to `bin/goh.<pid>.staging` and renaming it in; pid 10644 was killed mid-`cp`, the rename never
ran, and the half copy made the checkout read "1 uncommitted" (the GOH.STRAYLOCK class).
`scripts/build-goh.sh` staged its own `bin/.goh.<pid>` the same way. Both now stage through ONE
helper (`lib/place.sh`): the staging name is ignored by the checkout's `.gitignore`, a failed copy
removes its stage, and the next placement reaps every stage whose process is gone.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

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
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout


def _place(src: Path, dest: Path, path: str | None = None) -> subprocess.Popen:
    env = hermetic_env(**({"PATH": path} if path else {}))
    return subprocess.Popen(
        ["bash", "-c", '. "$1" && goh_place "$2" "$3"', "_", str(PLACE), str(src), str(dest)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


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
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists(), "the copy never started"
    finally:
        subprocess.run(["kill", "-KILL", f"-{proc.pid}"], check=False, stdin=subprocess.DEVNULL)
        proc.communicate()
    stages = sorted(p.name for p in (checkout / "bin").iterdir())
    assert len(stages) == 1 and stages[0].endswith(".staging"), (
        stages
    )  # the instrument: it IS there
    assert _status(checkout) == "", "a killed placement left an untracked file in the checkout"
    # The next placement reaps the dead process's stage and lands the whole binary.
    again = _place(src, dest)
    _, errs = again.communicate(timeout=60)
    assert again.returncode == 0, errs
    assert sorted(p.name for p in (checkout / "bin").iterdir()) == ["goh"]
    assert dest.read_bytes() == src.read_bytes()


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


def test_a_live_placements_stage_is_never_reaped(checkout, tmp_path):
    # Reaping goes by the pid in the name: a stage whose process still runs is mid-copy, not stray.
    live = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL)
    try:
        stage = checkout / "bin" / f"goh.{live.pid}.staging"
        stage.write_bytes(b"half")
        src = tmp_path / "goh-built"
        src.write_bytes(b"whole")
        proc = _place(src, checkout / "bin" / "goh")
        proc.communicate(timeout=60)
        assert proc.returncode == 0
        assert stage.exists()
    finally:
        live.kill()
        live.communicate()


def test_every_binary_placement_stages_through_the_one_helper():
    # The class: a hand-rolled `cp X bin/...; mv` beside the helper is the stray this closed.
    for rel in ("gates/_goh_bin.sh", "scripts/build-goh.sh"):
        text = (REPO_ROOT / rel).read_text()
        assert '.staging"' not in text and "/bin/.goh." not in text, rel
        assert "goh_place" in text, rel
