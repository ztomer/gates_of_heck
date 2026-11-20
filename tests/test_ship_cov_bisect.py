"""A ship judges every coverage floor over what landed since the last ship (GOH.COVOFFLAND).

The owner's priority, 2026-11-19: a goh land took 15-30 min, the instrumented coverage run 69-76%
of its gate. So a land runs the coverage step's tests plain (gates/rust_gate.sh
GOH_RUST_COVERAGE=plain) and the ship's own instrumented full run (lib/ship_full_run.py) judges
the floors, its diff coverage based at the last ship's tag, so every line landed since is judged.
The owner approved coalescing plus bisect: a red run releases nothing, bisects the lands since the
last ship (`goh-landed`) to the first red one, and files a fault item naming it.

The measurer below is a DUMMY (the owner's ruling, 2026-11-19): a scripted percentage per commit,
judged against the floor, never a real coverage run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import test_ship_starve
from conftest import REPO_ROOT, commit_all, git, write
from test_ship import _calls
from test_ship_starve import _declare, _env_for, _judged
from timing_bounds import patience

shipped = test_ship_starve.shipped  # the fixture, by assignment

# THE DUMMY MEASURER (owner, 2026-11-19: "run them in a dummy mode to verify that the handovers
# works"): the fixture repo's own gates/coverage_gate.sh, which full_run.INSTRUMENTED runs from the
# tip's tree, reads a scripted percentage from the tree's `cov.pct` and judges the 93.1% floor --
# no llvm-cov, no coverage.py. goh's code has no measurer seam: the run is always the shipped tree's
# own committed file, in a worktree forced clean at the tip (test_a_dummy_left_in_the_ships_...).
_LOG, _RECORD = test_ship_starve.STAND_IN.split("\n", 1)
DUMMY = (
    _LOG
    + """
printf '%s %s\\n' "$(git rev-parse HEAD)" "${{GOH_CHANGE_BASE-none}}" >> "$STAND_IN_LOG.base"
pct="$(cat cov.pct 2>/dev/null || echo 100)"
if awk -v p="$pct" 'BEGIN {{ exit !(p < 93.1) }}'; then
    echo "line coverage $pct% is below the 93.1% floor" >&2; exit 1
fi
"""
    + _RECORD
)
ALWAYS_GREEN = _LOG + "\n" + _RECORD  # records a full instrumented run, judges no floor


def _lands(repo: Path, pcts: list[str | None]) -> list[str]:
    """One land per scripted percentage (None: a land that leaves coverage as it was), recorded
    as land.sh records a landed tip."""
    out = []
    for n, pct in enumerate(pcts):
        write(repo, f"land{n}.txt", f"{n}\n")
        if pct is not None:
            write(repo, "cov.pct", f"{pct}\n")
        commit_all(repo, f"fix: land {n}")
        sha = git(repo, "rev-parse", "HEAD").strip()
        with (repo / ".git" / "goh-landed").open("a") as f:
            f.write(f"{sha} full-identity\n")
        out.append(sha)
    return out


def _ship(repo: Path, tmp_path: Path) -> tuple[int, str, dict]:
    import subprocess

    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"phases": [{"id": "P1"}]}))
    env = {**_env_for(tmp_path), "GOH_LOOP_QUEUE": str(queue)}
    argv = [sys.executable, str(REPO_ROOT / "tools" / "ship.py"), "--repo", str(repo), "--out",
            str(tmp_path / "ship.json"), "--wait"]  # fmt: skip
    r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=patience(240),
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return r.returncode, r.stdout + r.stderr, json.loads((tmp_path / "ship.json").read_text())


def _bases(tmp_path: Path) -> set[str]:
    return {ln.split()[1] for ln in (tmp_path / "judged.base").read_text().splitlines()}


def _declare_dummy(repo: Path) -> None:
    _declare(repo)
    write(repo, "gates/coverage_gate.sh", DUMMY.format(py=sys.executable, lib=REPO_ROOT / "lib"))
    commit_all(repo, "chore: the dummy measurer, a scripted percentage per commit")


def test_a_ship_under_a_floor_releases_nothing_and_names_the_culprit_land(
    shipped: Path, tmp_path: Path
) -> None:
    _declare_dummy(shipped)
    lands = _lands(shipped, ["95.0", "90.0", None])  # the middle land drops under 93.1%
    code, out, res = _ship(shipped, tmp_path)
    assert code == 1, out
    assert res["failed_step"] == "suite", res
    assert not git(shipped, "tag", "--list", "v1.1.0").strip(), "a red ship released"
    assert [s["name"] for s in res["steps"]] == ["suite"], res["steps"]  # nothing after it ran
    # the tip (red), then the bisect: the first land (green), the middle one (red)
    assert _judged(tmp_path) == [lands[2], lands[0], lands[1]], _judged(tmp_path)
    assert res["bisect"]["culprit"] == lands[1], res.get("bisect")
    assert _bases(tmp_path) == {git(shipped, "rev-parse", "v1.0.0^{commit}").strip()}
    filed = [c for c in _calls(tmp_path) if "file" in c]
    assert len(filed) == 1, _calls(tmp_path)
    item = json.loads(filed[0][filed[0].index("--json") + 1])
    assert lands[1][:12] in item["item"] and item["status"] == "todo", item
    assert res["bisect"]["fault"].startswith("filed "), res["bisect"]


def test_a_ship_at_every_floor_releases_and_bisects_nothing(shipped: Path, tmp_path: Path) -> None:
    _declare_dummy(shipped)
    lands = _lands(shipped, ["95.0", "93.1", "97.0"])  # at or over the floor, every one
    code, out, res = _ship(shipped, tmp_path)
    assert code == 0, f"{out}\n{res.get('tail')}"
    assert git(shipped, "rev-parse", "v1.1.0^{commit}").strip() == lands[-1]
    assert _judged(tmp_path) == [lands[-1]] and "bisect" not in res, res
    assert _bases(tmp_path) == {git(shipped, "rev-parse", "v1.0.0^{commit}").strip()}


def test_a_dummy_left_in_the_ships_worktree_never_releases_a_red_tip(
    shipped: Path, tmp_path: Path
) -> None:
    """A measurer that is not the tip's committed one is never in force: the ship's run forces its
    kept worktree to the tip, tracked and untracked (full_run.prepare), so an always-green dummy
    left there (a hand edit, an earlier run) is gone before the floor is judged."""
    _declare_dummy(shipped)
    _lands(shipped, ["90.0"])
    wt = tmp_path / "suite-wt"  # the ship's kept worktree, beside its result
    git(shipped, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    green = ALWAYS_GREEN.format(py=sys.executable, lib=REPO_ROOT / "lib")
    (wt / "gates" / "coverage_gate.sh").write_text(green)
    (wt / "cov.pct").write_text("100\n")
    code, out, res = _ship(shipped, tmp_path)
    assert code == 1 and res["failed_step"] == "suite", out
    assert not git(shipped, "tag", "--list", "v1.1.0").strip(), "a dummy released a red tip"


def test_a_ship_in_coverage_dummy_mode_releases_nothing_and_says_why(
    shipped: Path, tmp_path: Path
) -> None:
    """The owner's ruling, 2026-11-19: lands run with coverage measured nowhere until the switch
    (.gatesrc GOH_SHIP_COVERAGE=dummy) is deleted; while it is set no ship releases, saying why."""
    _declare_dummy(shipped)
    with (shipped / ".gatesrc").open("a") as f:
        f.write("GOH_SHIP_COVERAGE=dummy\n")
    _lands(shipped, ["95.0"])
    import subprocess

    argv = [sys.executable, str(REPO_ROOT / "tools" / "ship.py"), "--repo", str(shipped), "--out",
            str(tmp_path / "ship.json"), "--wait"]  # fmt: skip
    r = subprocess.run(argv, env=_env_for(tmp_path), capture_output=True, text=True,
                       timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 2, r.stdout + r.stderr
    assert "coverage in dummy mode; no release" in r.stdout + r.stderr, r.stdout + r.stderr
    assert not git(shipped, "tag", "--list", "v1.1.0").strip() and _judged(tmp_path) == []
