"""checks/check_setting_proof.py, calibrated: a settings change without both readings is red.

A utility clamp slowed lanes x9-25 for days because only lands were read (GOH.SETTINGPROOF). Each
test names the fault it plants: a settings change with no reading, with the lands' alone, with a
side or the conditions missing; a change off the surface (not judged); a function the surface
names; a surface entry that no longer matches its file. The logic runs in-process over diff text;
one test per chain drives it end to end through git -- `range` (git log and its hunk headers)
and `staged` (the index) -- each in one small planted repo.
"""

from __future__ import annotations

import re
import sys

import pytest
from conftest import REPO_ROOT, git, write

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_setting_proof as proof

LANDS = "Reading-lands: kind=land: before 526 s n=8 [a..b]; after 480 s n=6 [c..d]; load/core < 1.5"
LANES = (
    "Reading-lanes: kind=suite: before 210 s n=3 [a..b]; after 205 s n=2 [c..d]; load/core < 1.5"
)
BOTH = f"perf: two pre-steps\n\n{LANDS}\n{LANES}\n"


def diff(path: str, removed: str, added: str, ctx: str = "") -> str:
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -46 +46 @@ {ctx}\n-{removed}\n+{added}\n")  # fmt: skip


PRE_SLOTS = diff("lib/gate_lanes.py", "PRE_SLOTS = 2  # two", "PRE_SLOTS = 3  # three")


def test_a_settings_change_with_no_reading_is_refused_and_says_how() -> None:
    """Fault: PRE_SLOTS raised with no reading at all. Red, naming both trailers and the tool."""
    got = proof.verdict(PRE_SLOTS, "perf: three pre-steps\n")
    assert len(got) == 1, got
    assert "no `Reading-lands:` trailer" in got[0] and "no `Reading-lanes:` trailer" in got[0]
    assert "lib/gate_lanes.py: PRE_SLOTS = 3" in got[0] and "check_setting_proof.py read" in got[0]


def test_a_lands_only_reading_is_refused() -> None:
    """Fault: the 2026-11 blind spot itself -- lands read, lanes not. Red, naming the lanes."""
    got = proof.verdict(PRE_SLOTS, f"perf: x\n\n{LANDS}\n")
    assert len(got) == 1 and "no `Reading-lanes:` trailer" in got[0], got
    assert "Reading-lands" not in got[0].split(" -- ")[0].split("reading: ")[1]


@pytest.mark.parametrize(
    ("lanes", "says"),
    [
        (LANES.replace("before 210 s n=3 [a..b]; ", ""), "has no `before"),
        (LANES.replace("after 205 s n=2", "after: no quiet run"), "has no `after"),
        (LANES.replace("n=2", "n=0"), "after reads no run (n=0)"),
        (LANES.replace("; load/core < 1.5", ""), "names no `load/core < X`"),
    ],
    ids=["no-before", "no-after", "after-empty", "no-conditions"],
)
def test_a_reading_missing_a_side_or_its_conditions_is_refused(lanes: str, says: str) -> None:
    """Fault: one side, its runs, or the load line both sides were read under, left out."""
    got = proof.verdict(PRE_SLOTS, f"perf: x\n\n{LANDS}\n{lanes}\n")
    assert len(got) == 1 and says in got[0], got


def test_both_readings_pass() -> None:
    assert proof.verdict(PRE_SLOTS, BOTH) == []
    assert proof.problems(BOTH) == []


def test_a_change_off_the_surface_is_not_judged() -> None:
    """Fault planted the other way: the same constant in a file not on the list, module prose in a
    file that is, and a .gatesrc line whose hunk header names a setting. None is judged."""
    off = diff("lib/other.py", "PRE_SLOTS = 2", "PRE_SLOTS = 3")
    prose = diff("lib/land_share.py", "the share, not a clamp", "the share, never a clamp",
                 ctx="THE CLAMP (QoS, GOH.QOSCLAMP). macOS sets none")  # fmt: skip
    steps = diff(".gatesrc", "GOH_CI_STEPS='a'", "GOH_CI_STEPS='a:b'", ctx="GOH_LAND_SHARE=0.875")
    for text in (off, prose, steps):
        assert proof.touched(text) == [], text
        assert proof.verdict(text, "docs: x\n") == []


def test_a_line_inside_a_function_the_surface_names_is_judged() -> None:
    """Fault: worker_share.divide's arithmetic changed with no constant on the line."""
    body = diff("lib/worker_share.py", "    return max(1, total // max(1, gates))",
                "    return max(1, total // max(1, gates + 1))",
                ctx="def divide(total: int, gates: int) -> int:")  # fmt: skip
    assert [s.path for s, _ in proof.touched(body)] == ["lib/worker_share.py"] * 2  # each side


def test_a_removed_dash_line_is_no_file_header() -> None:
    """A removed `-- x` line reads as `--- x` in a -U0 diff: it is a change, never a new path."""
    text = PRE_SLOTS + "@@ -50 +50 @@\n--- a comment\n+PRE_SLOTS = 4\n"
    assert [h for _, h in proof.touched(text)][-1] == "lib/gate_lanes.py: PRE_SLOTS = 4"


def test_every_surface_entry_matches_its_file_and_a_moved_one_is_red(monkeypatch) -> None:
    """Fault: a setting renamed off its entry. The real tree is clean; the renamed one is stale."""
    assert proof.stale(REPO_ROOT) == []
    moved = proof.Setting("lib/gate_lanes.py", r"^PRE_STEP_SLOTS\s*=", "renamed")
    gone = proof.Setting("lib/no_such_file.py", r"x", "deleted")
    monkeypatch.setattr(proof, "SURFACE", (moved, gone))
    got = proof.stale(REPO_ROOT)
    assert len(got) == 2 and "no line matches" in got[0] and "not in the tree" in got[1], got


def test_the_reading_is_quiet_windowed_and_by_class() -> None:
    """Rows in memory: a loaded run, a run outside the window and another class's run are not
    read; the median of the rest, and the line it makes passes the gate."""
    rows = [
        {"epoch": 100, "wall_s": 10, "load1": 4, "slot_class": "lane"},
        {"epoch": 110, "wall_s": 30, "load1": 8, "slot_class": "lane"},
        {"epoch": 120, "wall_s": 900, "load1": 40, "slot_class": "lane"},  # 40/16 = 2.5: loaded
        {"epoch": 130, "wall_s": 900, "load1": 2, "slot_class": "land"},  # another class
        {"epoch": 900, "wall_s": 900, "load1": 2, "slot_class": "lane"},  # outside the window
        {"epoch": 140, "wall_s": 900, "slot_class": "lane"},  # no load: conditions unknown
    ]
    got = proof.side(rows, (0, 200), 16, 1.5, "lane")
    assert got is not None and (got.median_s, got.n) == (20, 2)
    assert proof.side(rows, (300, 400), 16, 1.5, "lane") is None
    text = proof.line("Reading-lanes", "spans kind=suite class=lane", got, got, 1.5)
    assert proof.problems(f"{LANDS}\n{text}") == []
    empty = proof.line("Reading-lanes", "x", got, None, 1.5)
    assert proof.problems(f"{LANDS}\n{empty}") == [
        "`Reading-lanes:` has no `after <seconds> s n=<runs>`"
    ]


def test_the_range_chain_end_to_end_in_a_planted_repo(repo) -> None:
    """git's own hunk header names the function a body line is in, and `range` reads each commit of
    BASE..HEAD from git log: a lands-only commit is red by its sha, a commit off the surface not."""
    write(repo, "lib/worker_share.py", "def divide(total, gates):\n    return total // gates\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    write(
        repo, "lib/worker_share.py", "def divide(total, gates):\n    return total // (gates + 1)\n"
    )
    msg = f"perf: x\n\n{LANDS}"
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", msg)
    got, n = proof.judge_range(repo, "HEAD~1")
    assert n == 1 and len(got) == 1, got
    assert re.match(r"[0-9a-f]{12}: a settings change \(lib/worker_share.py: return total", got[0])
    assert "no `Reading-lanes:` trailer" in got[0] and "Reading-lands:` trailer" not in got[0]
    assert proof.judge_range(repo, "no-such-base") == ([], -1)


def test_the_staged_chain_reads_the_index(repo, tmp_path) -> None:
    """`staged` (the commit-msg layer's mode) reads the index and the message file: red without
    the readings, git's `#` comment lines ignored, green with both. The file is committed first: a
    file new to the tree has no before."""
    write(repo, "lib/gate_lanes.py", "PRE_SLOTS = 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    write(repo, "lib/gate_lanes.py", "PRE_SLOTS = 3\n")
    git(repo, "add", "-A")
    msg = tmp_path / "MSG"
    msg.write_text(f"perf: x\n\n# {LANES}\n", encoding="utf-8")
    assert proof.main(["staged", str(msg), str(repo)]) == 1
    msg.write_text(BOTH, encoding="utf-8")
    assert proof.main(["staged", str(msg), str(repo)]) == 0


def test_a_file_new_to_the_tree_has_no_before_and_is_not_judged(repo, tmp_path) -> None:
    """A file a commit adds whole (a repo's first commit; the self-hosted hook test commits goh's
    tree that way) has no before to read, so its settings are no settings change."""
    write(repo, "lib/gate_lanes.py", "PRE_SLOTS = 3\n")
    git(repo, "add", "-A")
    msg = tmp_path / "MSG"
    msg.write_text("chore: first\n", encoding="utf-8")
    assert proof.main(["staged", str(msg), str(repo)]) == 0
