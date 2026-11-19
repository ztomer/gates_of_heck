"""A gate leaves nothing in $TMPDIR (the class a private Swift app measured, 2026-10-06).

A private Swift app counted ~4,700 `goh-rust-scope.*` and ~2,200 other `goh*` entries in the shared
temp dir, every one from a gate that had finished. Two causes, both here: the EXIT trap removed its
logs with an unquoted `for`, so a gate named `python (staged)` split its own log name on the space
and removed nothing; and the rust gate's scope file was registered with no cleanup at all. Every
gate now registers what it creates with ONE helper (`goh_cleanup_add`), read back line by line.
Each test runs a real gate under a private TMPDIR and asserts it ends EMPTY.
"""

import os
import shutil
import subprocess
from pathlib import Path

import _fake_cargo
import pytest
from conftest import REPO_ROOT, hermetic_env, stage, write
from test_rust_gate import CURRENT, clone, warm_crate  # noqa: F401  # warm_crate is a fixture


def _left(tmpdir: Path) -> list[str]:
    """What a gate left in TMPDIR, inside the one parent every root lives under (GOH.TMPNOINDEX)
    too; the empty parent itself is the machine's, kept for the next run."""
    parent = tmpdir / "goh.noindex"
    inner = sorted(f"goh.noindex/{p.name}" for p in parent.iterdir()) if parent.is_dir() else []
    return sorted(p.name for p in tmpdir.iterdir() if p != parent) + inner


def test_a_gate_whose_name_has_a_space_removes_its_log(repo, tmp_path) -> None:
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    write(repo, "a.py", "x = 1\n")
    stage(repo, "a.py")
    subprocess.run(["bash", str(REPO_ROOT / "gates" / "py_staged.sh"), "."], cwd=repo,
                   capture_output=True, text=True, env={**os.environ, "TMPDIR": str(tmpdir)}, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert _left(tmpdir) == []


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")
def test_the_rust_gate_removes_its_scope_file(tmp_path, warm_crate) -> None:  # noqa: F811
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    c = clone(warm_crate, tmp_path / "crate")
    # Off the network (_fake_cargo.offline): the subject is temp-file cleanup, not the advisories.
    env = {**os.environ, "TMPDIR": str(tmpdir), **_fake_cargo.offline(tmp_path / "fake-cargo")}
    r = subprocess.run(["/bin/bash", str(CURRENT), str(c)], cwd=c, capture_output=True,
                       text=True, env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert [n for n in _left(tmpdir) if n.startswith("goh")] == []


def _records(verdicts: Path) -> set[str]:
    return {p.name for p in (verdicts / "shell-lint").rglob("*.json")}


@pytest.fixture(scope="module")
def warm_lint(tmp_path_factory, goh) -> Path:
    """A shell-lint verdict store holding every verdict of THIS tree, paid here, in setup.

    THE COST (GOH.CIRED01392): `structural --full` shellchecks every shell file of goh's tree
    (114). Cold, that is 68 s of CPU here; at ONE shellcheck token -- the default on the hosted
    runner's 7 GiB (crates/goh/src/shellcheck_slots.rs, one per 8 GiB) -- it was 48 s of this
    test's call on a 16-core box and 224 s and 300 s on the runner (v0.139.2, v0.139.3, over the
    60 s ceiling), against 14 s warm. The worker's home is fresh per process (tests/suite_home.py),
    so whichever test of a worker lints the tree first paid it, decided by scheduling. The
    subject here is temp files, and `--full`'s lint makes none (only the staged scope checks blobs
    out into a temp dir, crates/goh/src/shell_lint_scope.rs), so the verdicts are paid once, in
    this module's setup, and the gate below reads them.
    """
    verdicts = tmp_path_factory.mktemp("warm-lint") / "verdicts"
    r = subprocess.run([str(goh), "shell-lint"], cwd=REPO_ROOT, capture_output=True, text=True,
                       env=hermetic_env(GOH_VERDICT_DIR=str(verdicts)), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert _records(verdicts), "goh shell-lint recorded no verdict to warm the store with"
    return verdicts


def test_structural_full_leaves_nothing_in_tmpdir(tmp_path, warm_lint) -> None:
    """The whole layer 1, every self-proof and the empty-scope sweep included, on this repo.

    Its shellchecks are judged in `warm_lint`'s setup: the gate here runs none (no verdict
    recorded during it), so its time is the gate's own, not a cold lint of the tree's.
    """
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    before = _records(warm_lint)
    env = {**os.environ, "TMPDIR": str(tmpdir) + "/", "GOH_VERDICT_DIR": str(warm_lint)}
    r = subprocess.run(["bash", str(REPO_ROOT / "gates" / "structural.sh"), "--full"],
                       cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
                       env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert _left(tmpdir) == []
    ran = _records(warm_lint) - before
    assert not ran, f"the gate shellchecked {len(ran)} files inside the test's call"
