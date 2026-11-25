"""Every local_ci step runs with a TMPDIR of its own; what a GREEN step leaves there is named and
removed (EST.TMPLEAK, lib/step_tmp.sh).

The user's $TMPDIR held 34,022 top-level entries (2026-11-14), test scratch no passing suite was
ever asked about. Each arm runs the real local_ci.sh on a scratch repo under a private TMPDIR.
"""

import os
import subprocess
from pathlib import Path

import pytest
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, hermetic_env

LOCAL_CI = REPO_ROOT / "gates" / "local_ci.sh"
LEAK = 'mkdir "$TMPDIR/leaky-test.abc123"'


def _ci(repo: Path, tmpdir: Path, gatesrc: str):
    write_gatesrc(repo, gatesrc)
    env = hermetic_env(TMPDIR=str(tmpdir))
    return subprocess.run(["/bin/bash", str(LOCAL_CI)], cwd=repo, capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip


@pytest.fixture
def box(tmp_path: Path) -> tuple[Path, Path]:
    repo, tmpdir = tmp_path / "proj", tmp_path / "t"
    repo.mkdir()
    tmpdir.mkdir()
    return repo, tmpdir


def _top(tmpdir: Path) -> list[str]:
    """Every entry under TMPDIR but goh's own roots' parent; inside it, only step slots."""
    parent = tmpdir / "goh.noindex"
    inner = (
        [f"goh.noindex/{p.name}" for p in parent.iterdir() if p.name.startswith("step.")]
        if parent.is_dir()
        else []
    )
    return sorted([p.name for p in tmpdir.iterdir() if p != parent] + inner)


def test_a_green_steps_leftovers_are_named_and_removed(box) -> None:
    repo, tmpdir = box
    r = _ci(repo, tmpdir, f"GOH_CI_STEPS='{LEAK}'\n")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out  # warn is the default: named, not failed
    assert "left 1 temp entry (removed): leaky-test.abc123" in out, out
    assert _top(tmpdir) == [], "the step's slot or its leak outlived the run"


def test_a_red_repo_fails_the_leaking_step(box) -> None:
    repo, tmpdir = box
    r = _ci(repo, tmpdir, f"GOH_TMP_LEAKS=red\nGOH_CI_STEPS='{LEAK}'\n")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "(temp leak)" in out and "leaky-test.abc123" in out, out


def test_an_allowed_leak_passes_and_a_stale_entry_fails(box) -> None:
    repo, tmpdir = box
    r = _ci(
        repo,
        tmpdir,
        f"GOH_TMP_LEAKS=red\nGOH_TMP_LEAK_ALLOW='leaky-test.* gone.*'\nGOH_CI_STEPS='{LEAK}'\n",
    )
    out = r.stdout + r.stderr
    assert "(temp leak)" not in out, out  # the step's own leak is allowed: its red is the entry's
    assert r.returncode != 0 and "'gone.*' matched no step" in out, out
    assert "'leaky-test.*' matched no step" not in out, out


def test_a_clean_step_is_silent_and_its_slot_is_reused(box) -> None:
    repo, tmpdir = box
    probe = repo / "seen"
    for _ in range(2):  # two runs: the second reuses the first's slot
        r = _ci(repo, tmpdir, f"GOH_PROVEN=0\nGOH_CI_STEPS='echo \"$TMPDIR\" >>{probe}'\n")
        out = r.stdout + r.stderr
        assert r.returncode == 0 and "temp entr" not in out, out
    seen = probe.read_text().split()
    # one path, run after run: a fresh name per step is what fseventsd pays for (GOH.FSCHURN)
    assert seen == [str(tmpdir / "goh.noindex" / "step.00")] * 2, seen
    # a unix socket's path is 104 bytes on macOS: the slot may add no more than this to a test's
    assert len(seen[0]) - len(str(tmpdir)) <= 20, seen
    assert _top(tmpdir) == []


def test_a_red_step_keeps_its_temp_dir_with_its_log(box) -> None:
    repo, tmpdir = box
    r = _ci(repo, tmpdir, f"GOH_CI_STEPS='{LEAK}; false'\n")
    out = r.stdout + r.stderr
    assert r.returncode != 0
    kept = tmpdir / "goh.noindex" / "step.00"
    assert f"its temp dir is kept with the log: {kept}" in out, out
    assert (kept / "leaky-test.abc123").is_dir()


def test_off_runs_steps_in_the_users_tmpdir(box) -> None:
    repo, tmpdir = box
    r = _ci(repo, tmpdir, f"GOH_TMP_LEAKS=off\nGOH_CI_STEPS='{LEAK}'\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmpdir / "leaky-test.abc123").is_dir()


def test_a_step_starts_sccache_under_the_users_tmpdir(box, tmp_path) -> None:
    """GOH.SCCACHETMP: a server a step starts must never live in a slot that is about to go."""
    repo, tmpdir = box
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "sccache").write_text('#!/bin/sh\nprintf "%s\\n" "$TMPDIR" >"$SEEN"\n')
    (fake / "sccache").chmod(0o755)
    seen = tmp_path / "sccache-saw"
    write_gatesrc(repo, "GOH_CI_STEPS='sccache --start-server'\n")
    env = hermetic_env(TMPDIR=str(tmpdir), SEEN=str(seen), PATH=f"{fake}:{os.environ['PATH']}")
    r = subprocess.run(["/bin/bash", str(LOCAL_CI)], cwd=repo, capture_output=True, text=True,
                       env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert seen.read_text().strip() == str(tmpdir)
