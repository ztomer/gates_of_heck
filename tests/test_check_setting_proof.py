"""checks/check_setting_proof.py, calibrated: a settings change without both readings is red.

A utility clamp slowed lanes x9-25 for days because only lands were read (GOH.SETTINGPROOF). Each
test names the fault it plants: a settings change with no reading, with the lands' alone, with a
side or the conditions missing; a change off the surface (not judged); a function the surface
names; a surface entry that no longer matches its file. The logic runs in-process over diff text;
one test drives the chain end to end -- git's own hunk headers, `range` on the command line and
`staged` -- in one small planted repo.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import REPO_ROOT, git, hermetic_env, write
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_setting_proof as proof

CHECK = REPO_ROOT / "checks" / "check_setting_proof.py"
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


def test_the_chain_end_to_end_in_a_planted_repo(repo, tmp_path) -> None:
    """git's own hunk header names the function; `range` on the command line reads each commit of
    BASE..HEAD (a lands-only commit red by its sha, a whole one green); `staged` reads the index."""
    write(repo, "lib/worker_share.py", "def divide(total, gates):\n    return total // gates\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD").strip()
    write(
        repo, "lib/worker_share.py", "def divide(total, gates):\n    return total // (gates + 1)\n"
    )
    git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "-am",
        f"perf: x\n\n{LANDS}",
    )
    bad = git(repo, "rev-parse", "HEAD").strip()
    argv = [sys.executable, str(CHECK), "range", base, str(repo)]
    run = subprocess.run(argv, capture_output=True,
                         text=True, env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL,
                         check=False, timeout=patience(60))  # fmt: skip
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"{bad[:12]}: a settings change (lib/worker_share.py" in run.stderr
    assert (
        "no `Reading-lanes:` trailer" in run.stderr and "Reading-lands:` trailer" not in run.stderr
    )
    write(repo, "lib/worker_share.py", "def divide(total, gates):\n    return total // gates\n")
    git(repo, "add", "-A")
    msg = tmp_path / "MSG"
    msg.write_text("perf: back\n\n# a git comment line\n", encoding="utf-8")
    assert proof.main(["staged", str(msg), str(repo)]) == 1
    msg.write_text(BOTH, encoding="utf-8")
    assert proof.main(["staged", str(msg), str(repo)]) == 0
