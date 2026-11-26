"""A template is built once per RUN, by one worker, and packed (GOH.FSCHURN2, tests/_tree_clone.py).

Each xdist worker built every template it met -- the largest single creator of the suite's file
events -- and every clone a test took carried the build's loose objects, removed file by file at
its teardown. These pin both cuts: concurrent workers asking for one key see one build and one
path, and a template's repositories hold no loose objects.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _tree_clone
import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, commit_all, hermetic_env, write
from timing_bounds import patience

_WORKER = """\
import sys, time
sys.path[:0] = [sys.argv[1]]
from pathlib import Path
import _tree_clone

def build(tree):
    with open(sys.argv[3], "a") as log:
        log.write("built\\n")
    time.sleep(0.3)  # long enough that the other workers arrive while it builds
    tree.mkdir()
    (tree / "f").write_text("x")

print(_tree_clone._run_template(Path(sys.argv[2]), "k", build))
"""


@pytest.mark.load_sensitive
def test_concurrent_workers_build_a_template_once_and_share_it(tmp_path: Path) -> None:
    log = tmp_path / "builds.log"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(REPO_ROOT / "tests"), str(tmp_path / "run"), log],
            stdout=subprocess.PIPE,
            text=True,
            env=hermetic_env(),
            stdin=subprocess.DEVNULL,
        )
        for _ in range(4)
    ]
    paths = {p.communicate(timeout=patience(60))[0].strip() for p in procs}
    assert all(p.returncode == 0 for p in procs)
    assert log.read_text().count("built") == 1, log.read_text()
    (path,) = paths
    assert (Path(path) / "f").read_text() == "x"
    # ...and its one claim (lib/build_once.py, GOH.BUILDONCE): a second build is refused.
    assert sorted(x.name for x in (tmp_path / "run").iterdir()) == ["builds", "k", "k.lock"]
    assert len(list((tmp_path / "run" / "builds").iterdir())) == 1


def test_a_templates_repositories_hold_no_loose_objects(tmp_path_factory, tmp_path: Path) -> None:
    def build(tree: Path) -> None:
        tree.mkdir()
        fast_init(tree, "main")
        for i in range(20):
            write(tree, f"f{i}.txt", f"{i}\n")
        commit_all(tree)

    tree = _tree_clone.template(tmp_path_factory, f"packed-{tmp_path.name}", build)
    objects = tree / ".git" / "objects"
    assert [d.name for d in objects.iterdir() if len(d.name) == 2] == []
    assert list((objects / "pack").glob("*.pack")), "the objects went nowhere"
    _tree_clone.clone_tree(tree, tmp_path / "c")
    log = subprocess.run(
        ["git", "-C", str(tmp_path / "c"), "log", "--format=%s", "--stat"],
        capture_output=True, text=True, check=True, env=hermetic_env(drop_git=True),
        stdin=subprocess.DEVNULL,
    ).stdout  # fmt: skip
    assert "f19.txt" in log  # a clone reads the packed history whole
