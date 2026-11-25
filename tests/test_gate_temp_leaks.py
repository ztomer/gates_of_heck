"""A gate leaves nothing in $TMPDIR (the class a private Swift app measured, 2026-10-06).

A private Swift app counted ~4,700 `goh-rust-scope.*` and ~2,200 other `goh*` entries in the shared
temp dir, every one from a gate that had finished. Two causes, both here: the EXIT trap removed its
logs with an unquoted `for`, so a gate named `python (staged)` split its own log name on the space
and removed nothing; and the rust gate's scope file was registered with no cleanup at all. Every
gate now registers what it creates with ONE helper (`goh_cleanup_add`), read back line by line.
Each test runs a real gate under a private TMPDIR and asserts it ends EMPTY; the structural one
lints against a private copy of the session's goh-cache.db (GOH.VERDICTDB V2).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import _fake_cargo
import pytest
from _fast_git import fast_init
from _tree_lint import private_copy, records, tree_lint  # noqa: F401  # tree_lint is a fixture
from conftest import REPO_ROOT, stage, write
from test_rust_gate import CURRENT, clone, warm_crate  # noqa: F401  # warm_crate is a fixture

sys.path.insert(0, str(REPO_ROOT / "checks"))
import _estate_table


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


def _one_corpus(home: Path) -> Path:
    """An estate of one: the first entry's corpus, at the floor the sweep takes. The machine's own
    were copied whole, four times each (5,000 file events), for the same two directories of TMPDIR
    to be made and removed. An estate of none is red: the
    sweep's green and the probe's are this one's."""
    entry = _estate_table.ESTATE[0]
    repo = home / entry["root"].removeprefix("~/")
    body = "".join(f"pub const LINE_{n}: u32 = {n};\n" for n in range(12))
    for n in range(5):
        write(repo, f"{entry['scope'][0]}/src/part_{n}{entry['ext']}", body)
    fast_init(repo)
    stage(repo, ".")
    return home


def test_structural_full_leaves_nothing_in_tmpdir(tmp_path, tree_lint) -> None:  # noqa: F811
    """The whole layer 1, every self-proof and the empty-scope sweep included, on this repo.

    Its shellchecks are judged once per session in `tree_lint`'s setup (tests/_tree_lint.py): the
    gate here runs none (no verdict recorded during it), so its time is the gate's own.
    """
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    own = private_copy(tree_lint, tmp_path / "verdicts")  # no other test writes this one
    before = records(own)
    env = {**os.environ, "TMPDIR": str(tmpdir) + "/", "GOH_CACHE_DB": str(own),
           "GOH_ESTATE_HOME": str(_one_corpus(tmp_path / "estate"))}  # fmt: skip
    r = subprocess.run(["bash", str(REPO_ROOT / "gates" / "structural.sh"), "--full"],
                       cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
                       env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert _left(tmpdir) == []
    ran = records(own) - before
    assert not ran, f"the gate shellchecked {len(ran)} files inside the test's call: {sorted(ran)}"
