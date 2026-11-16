"""The post-land loaded run is gone (GOH.LOADEDDROP); its static guard stays, and runs at commit.

Each land started the load-sensitive tests once under tools/load.py's spinners on the tip it landed
(tools/loaded_suite.sh, recorded per tree by lib/loaded_verdict.py), and goh ship waited for that
verdict before its first step: 470.6 s of v0.125.0's 892 s ship. GOH.LOADEDSUBSET's census found it
caught nothing in 13 runs (2 false reds, 1 no verdict), and every load-only failure in
tests/load_history.json was caught by a land on a busy box, hosted CI or a consumer push. So a land
starts no loaded run, a ship's first step is its push, and what stays is the static half: the
load_sensitive marker gate (tests/test_load_sensitive.py) and the wall-clock ratchet
(tests/test_wall_clock_ratchet.py), both declared `# goh-reads-staged:` so a commit staging a test
runs them over the index (gates/staged_guards.sh) -- a new waiting test is refused at commit, not
20 minutes into a land.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import load_sensitive as ls
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from test_land import LAND, _worktree
from test_ship import _result, _ship, shipped  # noqa: F401 -- shipped: the fixture
from timing_bounds import patience

DIRECTIVE = "# goh-reads-staged:"
GUARDS = ("tests/test_load_sensitive.py", "tests/test_wall_clock_ratchet.py")


def test_a_land_starts_no_loaded_run(repo: Path, tmp_path: Path) -> None:
    """A repo still declaring the old GOH_LAND_LOADED, landed outside goh's suite (the one case
    land.sh started the run in): no record appears in the common git dir, and nothing is said."""
    wt = _worktree(repo, tmp_path)
    write_gatesrc(wt, f"GOH_LAND_GATE=true\nGOH_LAND_LOADED='touch {tmp_path / 'ran'}'\n")
    git(wt, "add", ".gatesrc")
    git(wt, "commit", "-q", "--no-verify", "-m", "declare a loaded run")
    env = hermetic_env(drop_git=True, GOH_RUN_DIR=str(tmp_path / "run"))
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    r = subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                       timeout=patience(60), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    assert not (common / "goh-loaded").exists(), sorted((common / "goh-loaded").iterdir())
    assert "loaded run" not in r.stdout + r.stderr, r.stdout + r.stderr


def test_nothing_in_land_ship_release_or_push_names_the_loaded_run() -> None:
    paths = ("tools/land.sh", "tools/ship.py", "tools/release_goh.sh", ".gatesrc",
             "gates/push_gate.sh", "lib/release_gate.py")  # fmt: skip
    words = ("loaded_verdict", "goh-loaded", "GOH_LAND_LOADED", "loaded_suite")
    named = [(p, w) for p in paths for w in words if w in (REPO_ROOT / p).read_text()]
    assert named == [], named
    left = [
        p for p in ("lib/loaded_verdict.py", "tools/loaded_suite.sh") if (REPO_ROOT / p).exists()
    ]
    assert left == [], left


def test_a_ships_first_step_is_its_push_not_a_loaded_wait(shipped: Path, tmp_path: Path) -> None:  # noqa: F811
    """No verdict is recorded for the tip and the old key is declared: the ship used to refuse
    before any step ("no loaded verdict"); now it runs its steps, the first the push."""
    r = _ship(shipped, tmp_path, "--wait", GOH_LAND_LOADED="true")
    res = _result(tmp_path)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}\n{res.get('tail')}"
    assert "before_clock" not in res, res
    assert res["steps"][0]["name"] == "push", res["steps"]


def test_a_new_test_with_an_unbounded_wait_loop_is_refused_at_commit(tmp_path: Path) -> None:
    """Calibration of the static half. The census names a new test that polls with no bound and no
    mark, so the marker gate refuses it; and both guards declare pathspecs that a commit staging
    that new test matches, so gates/staged_guards.sh runs them at commit."""
    planted = "import time\ndef test_new(p):\n    while not p.exists():\n        time.sleep(0.1)\n"
    census = ls.Census({"test_new": planted})
    assert census.derived().get("tests/test_new.py::test_new") == ["sleep"], census.derived()
    assert "tests/test_new.py::test_new" not in census.marked()
    for guard in GUARDS:
        lines = (REPO_ROOT / guard).read_text(encoding="utf-8").splitlines()
        specs = [
            s for ln in lines if ln.startswith(DIRECTIVE) for s in ln[len(DIRECTIVE) :].split()
        ]
        assert specs, f"{guard} declares no {DIRECTIVE} line: it runs only at push"
        toy = tmp_path / guard.replace("/", "_")
        git(tmp_path, "init", "-q", str(toy))
        (toy / "tests").mkdir()
        (toy / "tests" / "test_new.py").write_text(planted)
        git(toy, "add", "-A")
        hit = git(toy, "diff", "--cached", "--name-only", "--", *specs).split()
        assert hit == ["tests/test_new.py"], (guard, specs, hit)
