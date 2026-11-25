"""ONE run of a change's test set before the batch gate (GOH.ONEVERDICTTREE3).

The lane runs `tools/affected_tests.py --run` on its committed tip and `goh land --enqueue` ran the
same set on the same sha again (586 s and 350 s in the two ONEVERDICTTREE lands). The set's green
run is kept in the one store (lib/reuse.py `affected`) and the next run of that set on that commit
under those suite inputs takes it: the record is handed to the caller, stamped with its base. Every
field of the key misses on its own; what the suite never reads (a verdict-free key) does not.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git

sys.path.insert(0, str(REPO_ROOT / "lib"))
import affected_verdict as av
import gate_identity
import reuse_audit

SET = ["tests/test_a.py", "tests/test_b.py::test_b"]
SUITE = 'CALLER_INPUTS = ("GOH_PY", "GOH_TEST_BIN", "GOH_LAND_VERDICTS_RECORD")\n'


def _commit(repo: Path, msg: str, **files: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def lane(repo: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_PY", sys.executable)
    for k in ("GOH_TEST_BIN", av.REUSE, av.SHAPE):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(av.RECORD, str(tmp_path / "lane.json"))
    files = {"tests/suite_home.py": SUITE, "gates/verdict_free_keys.txt": f"{av.RECORD}\n"}
    _commit(repo, "lane", **files)
    return repo


class Set:
    """The set's run: counted, and writing the record the suite would (tests/_land_verdicts.py)."""

    def __init__(self, repo: Path, rc: int = 0, identity: bool = True) -> None:
        self.repo, self.rc, self.identity, self.runs = repo, rc, identity, 0

    def __call__(self) -> int:
        self.runs += 1
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        ident = gate_identity.suite(self.repo) if self.identity else None
        rec = {"identity": ident, "sha": sha, "passed": dict.fromkeys(SET, False)}
        dest = Path(os.environ.get(av.RECORD, ""))
        dest.write_text(json.dumps(rec))
        return self.rc


def _enqueue(monkeypatch, tmp_path: Path, name: str = "enqueue.json") -> Path:
    """The enqueue's pre-step: its own record file, as tools/land.sh hands it."""
    monkeypatch.setenv(av.RECORD, str(tmp_path / name))
    return tmp_path / name


def test_the_lanes_green_run_is_the_enqueues_verdict_and_the_set_runs_once(
    lane, tmp_path, monkeypatch
) -> None:
    work = Set(lane)
    assert av.judged(lane, SET, "c" * 40, work) == 0
    assert work.runs == 1
    rec = _enqueue(monkeypatch, tmp_path)
    assert av.judged(lane, SET, "c" * 40, work) == 0
    assert work.runs == 1, "the enqueue ran the set the lane had just run green"
    got = json.loads(rec.read_text())
    assert got["passed"] == dict.fromkeys(SET, False)
    assert (got["base"], got["selection"]) == ("c" * 40, "affected"), got


@pytest.mark.parametrize(
    "move",
    ["another commit", "another set", "a suite input", "another interpreter", "another shape"],
)
def test_every_field_of_the_key_misses_on_its_own(lane, tmp_path, monkeypatch, move) -> None:
    work = Set(lane)
    av.judged(lane, SET, None, work)
    _enqueue(monkeypatch, tmp_path)
    chosen = SET
    if move == "another commit":
        _commit(lane, "more", **{"x.txt": "x\n"})
    elif move == "another set":
        chosen = SET[:1]
    elif move == "a suite input":
        monkeypatch.setenv("GOH_TEST_BIN", "/elsewhere/goh")
    elif move == "another interpreter":
        monkeypatch.setenv("GOH_PY", "/usr/bin/true")
    else:
        monkeypatch.setenv(av.SHAPE, "box")
    av.judged(lane, chosen, None, work)
    assert work.runs == 2, f"{move}: a verdict reached on other inputs was taken"


def test_what_the_suite_never_reads_moves_no_key(lane, tmp_path, monkeypatch) -> None:
    a = av.fields(lane, SET)
    monkeypatch.setenv(av.RECORD, "/another/record.json")  # verdict-free (the record's own path)
    monkeypatch.setenv("GOH_GATE_CLASS", "short")  # dropped by the suite before any test
    assert av.fields(lane, SET) == a


def test_a_checkout_that_is_not_its_commit_has_no_key_and_keeps_nothing(
    lane, tmp_path, monkeypatch
) -> None:
    (lane / "forgotten.py").write_text("x = 1\n")  # the file the lane forgot to add
    assert av.fields(lane, SET) is None
    work = Set(lane)
    av.judged(lane, SET, None, work)
    (lane / "forgotten.py").unlink()
    _enqueue(monkeypatch, tmp_path)
    av.judged(lane, SET, None, work)
    assert work.runs == 2


def test_a_red_run_a_moved_tree_and_a_deselecting_run_keep_nothing(
    lane, tmp_path, monkeypatch
) -> None:
    av.judged(lane, SET, None, Set(lane, rc=1))
    av.judged(lane, SET, None, Set(lane, identity=False))  # the tree moved during the run
    monkeypatch.setenv(av.REUSE, str(tmp_path / "gate.json"))
    assert av.fields(lane, SET) is None
    av.judged(lane, SET, None, Set(lane))
    monkeypatch.delenv(av.REUSE)
    work = Set(lane)
    av.judged(lane, SET, None, work)
    assert work.runs == 1, "a red, moved or deselecting run left a verdict to take"


def test_a_sampled_take_runs_the_set_and_compares(lane, tmp_path, monkeypatch) -> None:
    work = Set(lane)
    av.judged(lane, SET, None, work)
    seen = []
    monkeypatch.setattr(reuse_audit, "use", lambda path, fields: "shadow")
    monkeypatch.setattr(reuse_audit, "compare", lambda *a, **k: seen.append(a[2:4]) or True)
    av.judged(lane, SET, None, work)
    assert (work.runs, seen) == (2, [("green", "green")])
    monkeypatch.setattr(reuse_audit, "use", lambda path, fields: "off")
    av.judged(lane, SET, None, work)
    assert work.runs == 3, "an audited path switched off still reused"


def test_a_lanes_run_gets_its_own_land_record_and_a_part_of_the_set_goes_straight_through(
    lane, monkeypatch
) -> None:
    monkeypatch.delenv(av.RECORD)
    monkeypatch.delenv("GATES_OF_HECK_SUITE_RUNNER", raising=False)  # a lane's, not this suite's
    seen = []
    work = Set(lane)

    def run() -> int:
        seen.append(os.environ.get(av.RECORD))
        return work() if seen[-1] else 0

    av.whole(lane, SET, None, True, run)
    assert seen[0] and "goh-affected." in seen[0], seen  # the land's shape: a record is carried
    assert os.environ.get(av.RECORD) is None
    av.whole(lane, SET, None, False, run)  # --changed / --readers-only: no key, no record
    av.whole(lane, [], None, True, run)  # nothing chosen: nothing to keep
    assert seen[1:] == [None, None], seen


# GOH.PRESTEPVERDICTMISS: STEPCLOSUREREFUSE's lane ran its set green on 03023f7d, re-committed the
# same tree as 6de0a68d (tree 2867b103, parent c98b05c9 both), and the enqueue ran the set again
# for 515 s: the key was the sha, so a reworded commit of the very bytes the run judged missed.


def test_a_recommit_of_the_same_tree_takes_the_lanes_verdict(lane, tmp_path, monkeypatch) -> None:
    work = Set(lane)
    av.judged(lane, SET, "c" * 40, work)
    judged_on = git(lane, "rev-parse", "HEAD").strip()
    git(lane, "commit", "-q", "--amend", "--no-verify", "-m", "lane, reworded")
    head = git(lane, "rev-parse", "HEAD").strip()
    assert head != judged_on
    rec = _enqueue(monkeypatch, tmp_path)
    assert av.judged(lane, SET, "c" * 40, work) == 0
    assert work.runs == 1, "a recommit of the same tree ran the set the lane had just run green"
    got = json.loads(rec.read_text())
    assert got["sha"] == head, "the handed record names a commit that is not the checkout's"


def test_a_recommit_that_changes_the_tree_still_misses(lane, tmp_path, monkeypatch) -> None:
    work = Set(lane)
    av.judged(lane, SET, None, work)
    (lane / "x.txt").write_text("moved\n")
    git(lane, "add", "-A")
    git(lane, "commit", "-q", "--amend", "--no-verify", "-m", "lane")
    _enqueue(monkeypatch, tmp_path)
    av.judged(lane, SET, None, work)
    assert work.runs == 2, "a verdict on another tree was taken"


def _said(monkeypatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(av, "_say", lambda kind, text: lines.append(text))
    return lines


@pytest.mark.parametrize(
    ("move", "reason"),
    [
        ("another tree", "differs in tree"),
        ("another set", "differs in selection"),
        ("a suite input", "differs in env"),
        ("nothing kept", "no kept verdict of this repo"),
        ("an untracked file", "forgotten.py"),
        ("a deselecting run", "GOH_LAND_VERDICTS_REUSE"),
        ("the audit's shadow", "shadow"),
        ("the path is off", "off"),
    ],
)
def test_every_miss_says_its_reason_in_one_line(lane, tmp_path, monkeypatch, move, reason) -> None:
    work = Set(lane)
    if move != "nothing kept":
        av.judged(lane, SET, None, work)
    _enqueue(monkeypatch, tmp_path)
    chosen = SET
    if move == "another tree":
        _commit(lane, "more", **{"x.txt": "x\n"})
    elif move == "another set":
        chosen = SET[:1]
    elif move == "a suite input":
        monkeypatch.setenv("GOH_TEST_BIN", "/elsewhere/goh")
    elif move == "an untracked file":
        (lane / "forgotten.py").write_text("x = 1\n")
    elif move == "a deselecting run":
        monkeypatch.setenv(av.REUSE, str(tmp_path / "gate.json"))
    elif move == "the audit's shadow":
        monkeypatch.setattr(reuse_audit, "use", lambda path, fields: "shadow")
        monkeypatch.setattr(reuse_audit, "compare", lambda *a, **k: True)
    elif move == "the path is off":
        monkeypatch.setattr(reuse_audit, "use", lambda path, fields: "off")
    lines = _said(monkeypatch)
    runs = work.runs
    av.judged(lane, chosen, None, work)
    assert work.runs == runs + 1, f"{move}: the set did not run"
    why = [line for line in lines if "-- it runs" in line or "the set runs" in line]
    assert len(why) == 1 and "\n" not in why[0], lines
    assert reason in why[0], f"{move}: the miss line does not say why: {why[0]!r}"


# WHAT THE ENQUEUE DID WITH THE LANE'S RUN (GOH.LANEPRESTEP): the land request and its done record
# say whether the pre-step took a kept verdict (whose, and the seconds of the run it did not repeat)
# or ran the set (and why), read from the pre-step's own lines by `outcome`, the parser of the
# lines this module says. Without it a 35 s `pre_s` is a take or a small set, and the item's
# metric (affected-test runs per tree before the land gate) was measured from lane transcripts.
def test_a_take_of_the_lanes_verdict_is_said_as_taken_with_the_seconds_it_saved(
    lane, tmp_path, monkeypatch
) -> None:
    work = Set(lane)
    av.judged(lane, SET, "c" * 40, work)
    on = git(lane, "rev-parse", "HEAD").strip()[:8]
    _enqueue(monkeypatch, tmp_path)
    lines = _said(monkeypatch)
    av.judged(lane, SET, "c" * 40, work)
    assert work.runs == 1
    got = av.outcome([f"✓ {line}\n" for line in lines])
    assert got is not None and got.get("took") == on, (got, lines)
    assert got["by"] == "tools/affected_tests.py --run" and got["tests"] == len(SET), got
    assert isinstance(got["lane_s"], float) and got["lane_s"] >= 0, got


@pytest.mark.parametrize(
    ("move", "field"), [("another tree", "tree"), ("another set", "selection")]
)
def test_a_verdict_of_another_tree_or_set_is_refused_and_said_as_ran(
    lane, tmp_path, monkeypatch, move, field
) -> None:
    work = Set(lane)
    av.judged(lane, SET, None, work)
    _enqueue(monkeypatch, tmp_path)
    chosen = SET
    if move == "another tree":
        _commit(lane, "more", **{"x.txt": "x\n"})
    else:
        chosen = SET[:1]
    lines = _said(monkeypatch)
    av.judged(lane, chosen, "c" * 40, work)
    assert work.runs == 2, f"{move}: the lane's verdict was taken"
    got = av.outcome([f"→ {line}\n" for line in lines])
    assert got is not None and "took" not in got, (got, lines)
    assert f"differs in {field}" in got["ran"], got


def test_a_run_with_no_key_is_said_as_ran_and_no_affected_line_is_none(lane, monkeypatch) -> None:
    (lane / "forgotten.py").write_text("x = 1\n")
    lines = _said(monkeypatch)
    av.judged(lane, SET, None, Set(lane))
    got = av.outcome(lines)
    assert got is not None and "forgotten.py" in got["ran"], (got, lines)
    assert av.outcome(["→ pre-land: the structural gates over the branch first\n"]) is None
