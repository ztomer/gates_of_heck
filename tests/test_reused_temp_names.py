"""goh's per-run temp roots reuse their NAMES (GOH.FSCHURN, 2026-11-18).

THE CASE. fseventsd sat at one core and 11.7 GB, growing 55-90 MB/min, and 78-99% of the file events
on / were goh's: pytest temp roots (`$TMPDIR/goh.noindex/goh-pytest.XXXXXX`) and the suite's homes
(`~/.cache/goh/suite/home.XXXXXXXX`), a fresh random name for every run, so
every path a test wrote below them was a path fseventsd had never seen. Measured the same morning
(50,000 mkdir+create+unlink+rmdir at 450/s each arm): unique directory names grew fseventsd 278 MB
in 97 s, the same operations under ONE reused name 150 MB, inside the ambient band of the quiet
samples. So a root is a SLOT: the lowest free name of a small fixed set, reused run after run, and
every tmp_path below it (pytest numbers them from 0 under a fresh root) is the same path each run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

ROOT_SH = REPO_ROOT / "gates" / "_pytest_root.sh"


def _claim_root(tmpdir: Path, then: str = "") -> list[str]:
    """Claim a pytest temp root the way every gate does, print it, then run `then` in that shell."""
    script = (
        '. "$1/gates/_pytest_root.sh" && goh_pytest_root && '
        f'printf "%s\\n" "$PYTEST_DEBUG_TEMPROOT"; {then}'
    )
    r = subprocess.run(
        ["/bin/bash", "-c", script, "_", str(REPO_ROOT)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(TMPDIR=str(tmpdir), GOH_PY=sys.executable),
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.split()


def test_a_passing_runs_root_name_is_the_next_runs(tmp_path) -> None:
    """Two runs one after another, each removing its root as a passing run does: one name."""
    first = _claim_root(tmp_path, 'goh_rm_tree "$PYTEST_DEBUG_TEMPROOT"')
    second = _claim_root(tmp_path, 'goh_rm_tree "$PYTEST_DEBUG_TEMPROOT"')
    assert first == second, (first, second)


def test_a_held_root_sends_the_next_run_to_another_slot_and_back(tmp_path) -> None:
    """A root still held (a live run, or a red run's kept root) is never shared; once it is gone,
    its name is the one handed out again."""
    held = _claim_root(tmp_path)[0]
    other = _claim_root(tmp_path, 'goh_rm_tree "$PYTEST_DEBUG_TEMPROOT"')[0]
    assert other != held and Path(held).is_dir()
    shutil.rmtree(held)
    assert _claim_root(tmp_path)[0] == held


def _suite_home(real: Path) -> tuple[str, bool]:
    """The HOME a fresh (unnested) suite process takes, with `real` standing in for the user's, and
    whether a `leftover` was in it when the process started."""
    env = {
        k: v for k, v in hermetic_env().items()
        if not k.startswith("GATES_OF_HECK_SUITE") and k not in ("XDG_CACHE_HOME", "XDG_STATE_HOME")
    }  # fmt: skip
    env.update(HOME=str(real), PYTHONPATH=str(REPO_ROOT / "tests"), TMPDIR=str(real / "tmp"))
    code = (
        "import os, suite_home; h = os.environ['HOME']; print(h, os.path.exists(f'{h}/leftover'))"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False, env=env,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    home, leftover = r.stdout.split()[-2:]
    return home, leftover == "True"


def test_a_suite_process_home_is_reused_by_the_next_process(tmp_path) -> None:
    real = tmp_path / "real"
    (real / "tmp").mkdir(parents=True)
    (first, _), (second, _) = _suite_home(real), _suite_home(real)
    assert first == second, (first, second)
    assert Path(first).parent == (real / ".cache" / "goh" / "suite").resolve()
    assert not Path(first).exists(), "the suite home outlived its process"


def test_a_killed_processs_home_is_emptied_before_its_slot_is_reused(tmp_path) -> None:
    """SIGKILL skips atexit, so a home can outlive its process: the next claim of that slot starts
    from an empty home, never a dead run's leftovers."""
    real = tmp_path / "real"
    (real / "tmp").mkdir(parents=True)
    home = Path(_suite_home(real)[0])
    (home / "leftover" / "ro").mkdir(parents=True)
    os.chmod(home / "leftover", 0o500)  # a read-only tree a test left
    again, saw_leftover = _suite_home(real)
    assert Path(again) == home
    assert not saw_leftover, "the reused slot handed out the killed run's tree"


# Inside a run's root, the helpers' per-process temp dirs were random too (`goh-test-git.XXXXXXXX`,
# `goh-template-<key>.XXXXXXXX`, ...): the largest share of the paths still new in a second run of
# the same tests once the root itself was a slot (456 of 1803, GOH.FSCHURN). Each is now named for
# its xdist worker and its order in the process, so the next run's same worker reuses it.
_SEED = """\
import os, sys
sys.path[:0] = [sys.argv[1]]
import _fast_git, _tree_clone
_fast_git.fast_init(os.path.join(sys.argv[2], "repo"))
print(_fast_git._TEMPLATES[""])
print(_tree_clone.process_template("k", lambda p: os.makedirs(p)))
"""


def _helper_dirs(tmp: Path, run: str) -> list[str]:
    env = hermetic_env(TMPDIR=str(tmp), PYTEST_XDIST_WORKER="gw3")
    r = subprocess.run(
        [sys.executable, "-c", _SEED, str(REPO_ROOT / "tests"), str(tmp / run)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False, env=env,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.split()


def test_a_helpers_per_process_temp_dirs_are_the_next_processs(tmp_path) -> None:
    tmp = tmp_path / "T"
    tmp.mkdir()
    first, second = _helper_dirs(tmp, "a"), _helper_dirs(tmp, "b")
    assert first == second, (first, second)
    assert all(Path(p).is_relative_to(tmp.resolve()) or Path(p).is_relative_to(tmp) for p in first)
    assert not any(Path(p).exists() for p in first), "a helper's temp dir outlived its process"
