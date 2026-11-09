"""py_staged.sh — the cheap half of py_gate at commit time, on staged .py only.

Three directions: a staged ruff finding fails, a staged format drift fails, a
clean staged file passes -- and an UNSTAGED bad file is out of scope (it is
the push gate's), as is a commit with nothing Python staged.
"""

import os
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT, commit_all, stage, write

GATE = REPO_ROOT / "gates" / "py_staged.sh"

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")


def run(repo):
    return subprocess.run(
        ["/bin/bash", str(GATE), str(repo)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_nothing_python_staged_passes(repo):
    write(repo, "notes.txt", "hi\n")
    stage(repo, "notes.txt")
    r = run(repo)
    assert r.returncode == 0, r.stderr


def test_clean_staged_file_passes(repo):
    write(repo, "ok.py", "x = 1\n")
    stage(repo, "ok.py")
    r = run(repo)
    assert r.returncode == 0, r.stderr


def test_staged_ruff_finding_fails(repo):
    write(repo, "bad.py", "import os\n")  # F401: unused import
    stage(repo, "bad.py")
    r = run(repo)
    assert r.returncode != 0
    assert "F401" in r.stdout + r.stderr


def test_staged_format_drift_fails(repo):
    write(repo, "drift.py", "x   =   1\n")
    stage(repo, "drift.py")
    r = run(repo)
    assert r.returncode != 0
    assert "drift.py" in r.stdout + r.stderr


def test_unstaged_bad_file_is_not_this_gates_business(repo):
    write(repo, "bad.py", "import os\n")
    write(repo, "ok.py", "x = 1\n")
    stage(repo, "ok.py")
    r = run(repo)
    assert r.returncode == 0, r.stderr


# GOH.PYSTAGEDINDEX: the gate lints the bytes the commit RECORDS (the index), never the working
# tree's copy of the same file. Both directions, red first (LOOP.WIPLINT probed the first).


def test_a_staged_failure_whose_fix_is_only_unstaged_is_refused(repo):
    write(repo, "a.py", "import os\nx  =  1\n")  # F401 + format drift, staged
    stage(repo, "a.py")
    write(repo, "a.py", "X = 1\n")  # the fix, never staged: the commit does not carry it
    r = run(repo)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "a.py" in r.stdout + r.stderr


def test_a_clean_staged_file_with_a_failing_unstaged_edit_passes(repo):
    write(repo, "a.py", "x = 1\n")
    stage(repo, "a.py")
    write(repo, "a.py", "import os\nx  =  1\n")  # the working tree fails; the commit does not
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_index_a_hook_carries_is_the_one_judged(repo):
    """`git commit -a` / `commit <paths>` commit a TEMPORARY index that only GIT_INDEX_FILE names;
    a hook carries it as GOH_HOOK_INDEX_FILE (gates/_git_env.sh). The repository's own index here
    is clean and the working tree is clean: only the carried index holds the failing bytes."""
    write(repo, "a.py", "x = 1\n")
    commit_all(repo)
    git_dir = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=True,
    ).stdout.strip()
    alt = repo.parent / "alt-index"
    alt.write_bytes((repo / ".git" / "index").read_bytes())
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input="import os\nx = 1\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    env = {**os.environ, "GIT_INDEX_FILE": str(alt)}
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--cacheinfo", f"100644,{blob},a.py"],
        env=env,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    r = subprocess.run(
        ["/bin/bash", str(GATE), str(repo)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        env={**os.environ, "GOH_HOOK_INDEX_FILE": str(alt), "GOH_HOOK_GIT_DIR": git_dir},
    )
    assert r.returncode != 0, r.stdout + r.stderr
    assert "F401" in r.stdout + r.stderr


# --all: the push half for a repo with no pytest suite (GOH.PYALL). A COMMITTED file whose format
# drifted -- what a ruff bump does to a file nobody touches -- is invisible to the staged scope and
# must be red here.


def run_all(repo):
    return subprocess.run(
        ["/bin/bash", str(GATE), "--all", str(repo)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_all_reads_a_committed_drift_the_staged_scope_never_sees(repo):
    write(repo, "tools/drift.py", "x   =   1\n")
    stage(repo, "tools/drift.py")
    commit_all(repo)
    assert run(repo).returncode == 0  # nothing staged: the staged scope is blind to it
    r = run_all(repo)
    assert r.returncode != 0
    assert "tools/drift.py" in r.stdout + r.stderr


def test_all_passes_a_clean_tree(repo):
    write(repo, "tools/ok.py", "x = 1\n")
    stage(repo, "tools/ok.py")
    commit_all(repo)
    r = run_all(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_all_honours_goh_exclude(repo):
    write(repo, "vendor/drift.py", "x   =   1\n")
    write(repo, "ok.py", "x = 1\n")
    write(repo, ".gatesrc", "GOH_EXCLUDE='^vendor/'\n")
    stage(repo, "vendor/drift.py")
    stage(repo, "ok.py")
    commit_all(repo)
    r = run_all(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_all_over_no_tracked_python_is_red_not_clean(repo):
    write(repo, "notes.txt", "hi\n")
    stage(repo, "notes.txt")
    commit_all(repo)
    r = run_all(repo)
    assert r.returncode != 0
    assert "no tracked .py" in r.stdout + r.stderr


# A failing step's output names the files that FAILED, not every file the command was handed
# (GOH.RUFFPIN item 2). `goh_step` ended its failure with `failed (command: <argv>)`, and for a
# whole-tree run the argv is every tracked .py: one drifted file among 150 produced a 150-path line
# that looked like 150 findings.


def test_all_names_only_the_failing_files(repo):
    for i in range(6):
        write(repo, f"tools/clean_{i}.py", "x = 1\n")
    write(repo, "tools/drift.py", "x   =   1\n")
    stage(repo, "tools")
    commit_all(repo)
    r = run_all(repo)
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "tools/drift.py" in out or "drift.py" in out, out
    assert "clean_" not in out, f"a file that passed was named in the failure:\n{out}"
    assert "ruff format --check" in out, "the command is still named, only its file list is not"
