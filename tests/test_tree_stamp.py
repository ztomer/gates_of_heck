"""The tree-moved contract (lib/tree_stamp.py, wired into gates/_common.sh).

A gate over the working tree must say "the tree moved under the gate" when a
file changed while it ran, and must stay silent when nothing did. Every case
here runs against a real git repo and the real CLI, and the _common.sh cases
run a real gate script, so the wiring is what is tested, not a helper.
"""

import os
import subprocess
import sys
import textwrap

import pytest

from conftest import REPO_ROOT

STAMP = REPO_ROOT / "lib" / "tree_stamp.py"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / ".gitignore").write_text("build/\n")
    (r / "a.swift").write_text("let a = 1\n")
    git(r, "add", ".")
    git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return r


def stamp(repo, tmp_path, action):
    return subprocess.run([sys.executable, str(STAMP), action, str(repo), str(tmp_path / "s.json"),
                           "--name", "demo"], capture_output=True, text=True)


def bump(path, text):
    """Write and move the mtime forward, so a same-second edit is still seen."""
    path.write_text(text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_a_still_tree_passes_silently(repo, tmp_path):
    assert stamp(repo, tmp_path, "take").returncode == 0
    r = stamp(repo, tmp_path, "check")
    assert (r.returncode, r.stderr) == (0, "")


@pytest.mark.parametrize("change", ["edit", "new_untracked", "delete"])
def test_every_way_the_tree_moves_is_named(repo, tmp_path, change):
    stamp(repo, tmp_path, "take")
    if change == "edit":
        bump(repo / "a.swift", "let a = 2\n")
        expected = "a.swift"
    elif change == "new_untracked":
        (repo / "b.swift").write_text("let b = 1\n")
        expected = "b.swift"
    else:
        (repo / "a.swift").unlink()
        expected = "a.swift"
    r = stamp(repo, tmp_path, "check")
    assert r.returncode == 1, r.stderr
    assert "the tree moved under the gate" in r.stderr
    assert expected in r.stderr


def test_ignored_output_and_the_gate_lock_are_not_the_tree(repo, tmp_path):
    stamp(repo, tmp_path, "take")
    (repo / "build").mkdir()
    (repo / "build" / "out.o").write_text("object")
    (repo / ".goh-tree.lock").write_text("12345")
    # Tool caches the repo forgot to ignore are still the gate's own output.
    for d in ("__pycache__", "pkg/.pytest_cache", ".build/debug"):
        (repo / d).mkdir(parents=True)
        (repo / d / "x").write_text("cache")
    (repo / ".coverage").write_text("db")
    r = stamp(repo, tmp_path, "check")
    assert r.returncode == 0, r.stderr


def test_a_commit_during_the_run_is_not_a_move(repo, tmp_path):
    """The bytes the gate judged are still the bytes on disk."""
    stamp(repo, tmp_path, "take")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    assert stamp(repo, tmp_path, "check").returncode == 0


def test_no_stamp_is_a_named_refusal_not_a_pass(repo, tmp_path):
    r = stamp(repo, tmp_path, "check")
    assert r.returncode == 2
    assert "no readable tree stamp" in r.stderr


def test_a_source_file_that_merely_mentions_a_cache_name_is_still_the_tree(repo, tmp_path):
    stamp(repo, tmp_path, "take")
    (repo / "target.swift").write_text("let t = 1\n")
    (repo / "__pycache__.md").write_text("notes\n")
    r = stamp(repo, tmp_path, "check")
    assert r.returncode == 1 and "target.swift" in r.stderr and "__pycache__.md" in r.stderr


def test_many_moved_paths_are_summarised(repo, tmp_path):
    stamp(repo, tmp_path, "take")
    for i in range(15):
        (repo / f"n{i:02}.swift").write_text("x\n")
    r = stamp(repo, tmp_path, "check")
    assert "15 file(s) changed" in r.stderr
    assert "and 5 more" in r.stderr


# ---- the wiring: every gate built on _common.sh gets this for free ----------

GATE = textwrap.dedent("""\
    #!/usr/bin/env bash
    . "{common}"
    goh_init "demo"
    [ -n "$STAMP" ] && goh_tree_stamp
    goh_step "work" bash -c "$WORK"
    goh_done
""")


def run_gate(repo, work, env_extra=None):
    script = repo.parent / "gate.sh"
    script.write_text(GATE.format(common=REPO_ROOT / "gates" / "_common.sh"))
    env = {**os.environ, "WORK": work, "STAMP": "1", **(env_extra or {})}
    return subprocess.run(["bash", str(script)], cwd=repo, capture_output=True, text=True, env=env)


def test_a_green_gate_over_a_moving_tree_is_refused(repo):
    r = run_gate(repo, "sleep 1; echo 'let a = 3' > a.swift; touch -t 203001010000 a.swift")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the tree moved under the gate" in r.stderr
    assert "all demo gates passed" not in r.stdout


def test_a_red_gate_over_a_moving_tree_names_the_move(repo):
    r = run_gate(repo, "echo 'let a = 3' > a.swift; touch -t 203001010000 a.swift; exit 3")
    assert r.returncode != 0
    assert "work failed" in r.stderr
    assert "the tree moved under the gate" in r.stderr


def test_a_still_tree_gate_passes(repo):
    r = run_gate(repo, "true")
    assert r.returncode == 0, r.stderr
    assert "the tree moved" not in r.stderr


def test_the_gate_writing_its_own_ignored_output_is_not_a_move(repo):
    r = run_gate(repo, "mkdir -p build && echo o > build/x.o")
    assert r.returncode == 0, r.stderr


def test_a_gate_that_never_stamps_is_untouched(repo):
    """The staged gates judge the index: an edit elsewhere is not theirs to refuse."""
    r = run_gate(repo, "echo 'let a = 3' > a.swift; touch -t 203001010000 a.swift", {"STAMP": ""})
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("gate", ["swift_gate.sh", "rust_gate.sh", "py_gate.sh"])
def test_every_gate_that_builds_the_working_tree_stamps_it(gate):
    text = (REPO_ROOT / "gates" / gate).read_text()
    assert "\ngoh_tree_stamp\n" in text, f"{gate} builds the working tree but never stamps it"


@pytest.mark.parametrize("gate", ["structural.sh", "py_staged.sh"])
def test_the_staged_gates_do_not_stamp(gate):
    assert "goh_tree_stamp" not in (REPO_ROOT / "gates" / gate).read_text()
