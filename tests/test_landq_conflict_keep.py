"""A land red only on a stack conflict keeps its green pre-step verdict (GOH.CONFLICTKEEPVERDICT).

GOH.BATCHCONFLICTFIRST part (b): a conflict red threw its green pre-step verdict away with its
request, so the lane's re-enqueue after a clean rebase ran the whole pre-step again (16-20 min at
2026-11-22's load) on a tree that differs from the judged one only in what main moved. Now the
verdict is carried to the item's next enqueue, whose pre-step asks lib/pre_reuse.py first, as the
lander does: a base move no test reads reruns nothing, one a test reads runs those readers.

The reads table is test_pre_reuse.py's stand-in: `read.txt` is read by tests/test_r.py, nothing
else by anything. Each full pre-step appends `full` to `seen`; a narrow run appends `narrow ...`.
"""

from __future__ import annotations

from pathlib import Path

from conftest import REPO_ROOT, git, write
from test_landq_batch import env, landq, lane, recorded  # noqa: F401  -- `env` is a fixture

from lib import land_prestep

# test_pre_reuse.py's stand-in table, cut to the one read path this file needs: its full table
# names gates and checks, and importing it would make a change to any of them choose this file.
READS = """\
import sys
sys.path.insert(0, {kit!r})
from lib.test_closure import Closure  # the kit's graph: what the pre-step's code reaches
from lib import binary_runners  # the binary's build inputs: what `_built` asks the table
del sys.path[0]
READ = {{"read.txt": "tests/test_r.py"}}
def readers(root, changed, tests=None):
    return {{READ[p]: "reads " + p for p in changed if p in READ}}
def affected(root, changed):
    return {{"tests/test_walk.py::test_every_x": "a tree reader", **readers(root, changed)}}
if __name__ == "__main__":
    with open({seen!r}, "a") as f:
        f.write("narrow " + " ".join(sys.argv[sys.argv.index("--changed") + 1 :]) + "\\n")
"""


def _with_table(repo: Path, tmp_path: Path) -> Path:
    seen = tmp_path / "seen"
    write(repo, "tools/affected_tests.py", READS.format(seen=str(seen), kit=str(REPO_ROOT)))
    for name in ("read.txt", "CHANGELOG.md"):
        write(repo, name, "main's first\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "a reads table")
    return seen


def _conflict_then_reenqueue(repo: Path, tmp_path: Path, env: dict, also: str):  # noqa: F811
    """T.C enqueued green, red on a conflict main made in c.txt (main moving ALSO with it), the
    lane rebased cleanly (its own c.txt kept), enqueued again: (seen, the re-enqueue's output)."""
    seen = _with_table(repo, tmp_path)
    pre = f'echo full >> {seen} && echo "{{}}" > "$GOH_LAND_VERDICTS_RECORD"'
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    wt = lane(repo, tmp_path, "one", {"c.txt": "the lane's\n", "one.txt": "1\n"})
    first = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.C",
                  "--no-start")  # fmt: skip
    assert first.returncode == 0, first.stdout + first.stderr
    write(repo, "c.txt", "main's\n")
    write(repo, also, "main moved it\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", f"main moves c.txt and {also}")
    assert landq(env, "--drain").returncode == 0
    red = recorded(env)["T.C"]
    assert red["state"] == "red" and land_prestep.conflict_red(red), red
    carry = land_prestep.carry_path(Path(env["GOH_LANDQ_DIR"]), "T.C")
    assert carry.is_file(), "the conflict red threw its green verdict away"
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "rebase", "-q", "-X", "theirs", "main")
    again = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.C",
                  "--no-start")  # fmt: skip
    out = again.stdout + again.stderr
    assert again.returncode == 0, out
    assert not carry.exists(), "a carried verdict is read once"
    assert landq(env, "--drain").returncode == 0
    assert recorded(env)["T.C"]["state"] == "landed", recorded(env)
    return seen.read_text().splitlines(), out


def test_a_conflict_red_reenqueued_after_a_clean_rebase_reuses_its_green_verdict(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811
) -> None:
    seen, out = _conflict_then_reenqueue(repo, tmp_path, env, "CHANGELOG.md")
    assert seen == ["full"], out  # the re-enqueue ran nothing; neither did the lander
    assert "not run again" in out and "none read by a test" in out, out


def test_a_base_move_a_test_reads_runs_its_readers_not_the_whole_set(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811
) -> None:
    seen, out = _conflict_then_reenqueue(repo, tmp_path, env, "read.txt")
    assert seen[0] == "full" and "full" not in seen[1:], out
    assert any(s.startswith("narrow") and "read.txt" in s for s in seen[1:]), (seen, out)


def test_a_carried_verdict_is_reaped_after_a_day_with_its_pin(tmp_path: Path) -> None:
    q = tmp_path / "q"
    carry = land_prestep.carry_path(q, "T.OLD")
    carry.parent.mkdir(parents=True)
    carry.write_text("a" * 40 + "\n")
    Path(f"{carry}.record").write_text("{}")
    assert land_prestep.reap(q) == []  # fresh: kept
    later = int((carry.stat().st_mtime + 86400 * land_prestep.CARRY_DAYS + 60) * 10**9)
    assert sorted(land_prestep.reap(q, now_ns=later)) == sorted([carry, Path(f"{carry}.record")])
