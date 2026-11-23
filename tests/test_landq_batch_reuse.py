"""A stacked batch land reuses the enqueue's verdict where main moved only in unread paths
(GOH.PRELANDBATCH), and no kept verdict outlives its request.

GOH.UNDOMINE's enqueue pre-step ran 1593 s and its lander then ran the pre-land again for 854 s:
the single path, because Cargo.lock (a build input of the structural binary) moved -- a genuine
rerun. The BATCH path never reused at all: lib/landq_batch.py named the enqueue's verdict to a tip
only when its tree IS the enqueued one, so every stack of two or more ran the whole set on a tree
whose difference from the judged one -- the lower items' paths and main's moves -- no test reads.
lib/pre_reuse.py decides it as it does a single land's: moved paths read by no test reuse the
verdict, a path some test reads runs its readers, a test a lower item added runs.

The reads table is test_pre_reuse.py's stand-in: `read.txt` is read by tests/test_r.py, nothing
else by anything. Each enqueue's pre-step appends `full` to `seen`; a narrow run appends `narrow`.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from _pre_reuse_kit import _main_moves, _with_table
from test_landq_batch import env, landq, lane, recorded  # noqa: F401  -- `env` is a fixture

from lib import land_prestep


def _batch(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811
    moves: str | None,
    lower: dict[str, str],
):
    """Two lanes enqueued, their pre-steps run (the LOWER one carries its files), main moved by
    MOVES, then drained as one batch: (the pre-step's runs, the two items' logs)."""
    seen = _with_table(repo, tmp_path)
    pre = f'echo full >> {seen} && echo "{{}}" > "$GOH_LAND_VERDICTS_RECORD"'
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    wts = [(lane(repo, tmp_path, "one", lower), "T.ONE"),
           (lane(repo, tmp_path, "two", {"two.txt": "2\n"}), "T.TWO")]  # fmt: skip
    for wt, item in wts:
        done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", item,
                     "--no-start")  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
    if moves:
        _main_moves(repo, moves)
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)
    assert {k: v["state"] for k, v in got.items()} == {"T.ONE": "landed", "T.TWO": "landed"}, got
    assert got["T.ONE"]["batch"]["gates"] == 1, got  # one stacked gate
    logs = "".join(Path(v["log"]).read_text() for v in got.values())
    return seen.read_text().splitlines(), logs


def test_a_stacked_batch_whose_main_moved_only_where_no_test_reads_runs_no_second_pre_land(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811
) -> None:
    seen, log = _batch(repo, tmp_path, env, "CHANGELOG.md", {"one.txt": "1\n"})
    assert seen == ["full", "full"], log  # the two enqueues' runs, and none for the stack
    assert "not run again" in log and "none read by a test" in log, log


def test_a_stacked_batch_with_a_path_a_test_reads_leaves_that_reader_to_the_gate(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811
) -> None:
    seen, log = _batch(repo, tmp_path, env, "CHANGELOG.md", {"read.txt": "one reads\n"})
    assert seen == ["full", "full"], log  # NARROW decided, never run (GOH.LANDONERUN)
    assert "read by 1 test file: " in log and "read.txt" in log, log
    assert "not run -- the gate's one suite judges them" in log, log


def test_a_red_stack_s_bisect_still_finds_each_lone_item_s_verdict(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811
) -> None:
    """The stack's gate reads a COPY of the top's verdict: land.sh removes what it reads, and the
    lone item the bisect gates next is that item, at its enqueued tree."""
    seen = _with_table(repo, tmp_path)
    pre = f'echo full >> {seen} && echo "{{}}" > "$GOH_LAND_VERDICTS_RECORD"'
    gate = "if [ -e bad.txt ]; then exit 1; fi"
    env = {**env, "GOH_LAND_GATE": gate, "GOH_LAND_PRE": pre, "GOH_GATE_SLOTS": "1"}
    wts = [(lane(repo, tmp_path, "one", {"one.txt": "1\n", "bad.txt": "x\n"}), "T.ONE"),
           (lane(repo, tmp_path, "two", {"two.txt": "2\n"}), "T.TWO")]  # fmt: skip
    for wt, item in wts:
        done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", item,
                     "--no-start")  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)
    assert got["T.ONE"]["state"] == "red" and got["T.TWO"]["state"] == "landed", got
    assert seen.read_text().splitlines() == ["full", "full"], (  # no pre-step after the enqueues
        seen.read_text(), Path(got["T.TWO"]["log"]).read_text())  # fmt: skip
    assert not list((Path(env["GOH_LANDQ_DIR"]) / "pre").glob("*")), "a kept verdict outlived it"


NOW_NS = 1_795_000_000 * 10**9
HOUR_NS = 3600 * 10**9


def _keep(q: Path, sha: str, born_ns: int) -> Path:
    keep = q / "pre" / f"{sha}-{born_ns}"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text(sha + "\n")
    Path(f"{keep}.record").write_text("{}\n")
    return keep


@pytest.mark.load_sensitive(
    reason="a keep is young by hours of margin; only a drain that stalled for hours reaps it"
)
def test_an_idle_lander_reaps_a_kept_verdict_whose_request_never_came(
    tmp_path: Path,
    env: dict,  # noqa: F811
) -> None:
    """An enqueue killed between its pre-step and its request leaves a keep nothing names; it was
    reaped only at the next recorded land, so an idle queue kept it indefinitely. A drain reaps."""
    q = Path(env["GOH_LANDQ_DIR"])
    old = _keep(q, "a" * 40, time.time_ns() - 3 * HOUR_NS)
    young = _keep(q, "b" * 40, time.time_ns())  # an enqueue still in its pre-step
    assert landq(env, "--drain").returncode == 0
    left = sorted(p.name for p in (q / "pre").iterdir())
    assert left == sorted([young.name, f"{young.name}.record"]), (old, left)


def test_a_gate_copy_of_a_verdict_leaves_the_keep_and_goes_with_its_gate(tmp_path: Path) -> None:
    q = tmp_path / "q"
    keep = _keep(q, "c" * 40, NOW_NS)
    req = {"sha": "c" * 40, "pre": {"judged": "c" * 40, "keep": str(keep)}}
    with land_prestep.gate_copy(q, req) as copy:
        assert copy is not None and copy != keep
        assert copy.read_text() == keep.read_text()
        assert Path(f"{copy}.record").is_file()
        env_words = land_prestep.lander_env(req, copy)
        assert f"GOH_LAND_PRE_FROM={copy}" in env_words
    assert not copy.exists() and not Path(f"{copy}.record").exists()
    assert keep.is_file() and Path(f"{keep}.record").is_file()
    with land_prestep.gate_copy(q, {"sha": "d" * 40}) as none:
        assert none is None
