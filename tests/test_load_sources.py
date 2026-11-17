"""lib/load_sources.py + lib/proc_table.py (GOH.LOADSOURCES): what burns the machine's CPU.

Every figure is a fixture process table worked BY HAND in the comment beside it: a reaped child
counted twice, a family read from the wrong end of the ancestry, or a coverage over the wrong
denominator fails here. Nothing here reads the live process table.
"""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import load_sources as ls
from proc_table import Proc, Snapshot, parse_ps, script_of

CLAUDE = "/Users/u/Library/Application Support/Claude/claude-code/2.1/x/claude"


def p(pid, ppid, exe, cpu=0.0, child=0.0, *, path=None, cwd=None, script=None, start=None, rss=0):
    return Proc(
        pid,
        ppid,
        exe,
        path or f"/usr/bin/{exe}",
        cpu,
        child,
        rss,
        cwd,
        script,
        float(pid if start is None else start),
    )


TABLE = {
    1: Proc(1, 0, "launchd", "/sbin/launchd", 0, None, 0, None),
    100: p(100, 1, "claude", path=CLAUDE),
    101: p(101, 100, "zsh"),
    102: p(102, 101, "cargo"),
    103: p(103, 102, "rustc"),
    200: p(200, 1, "bash", script="land.sh"),
    201: p(201, 200, "Python", script="pytest"),
    202: p(202, 201, "Python"),  # an xdist worker: `-c ...`, no script
    300: p(300, 100, "bash", script="push_gate.sh"),
    301: p(301, 300, "bash", script="gate.sh"),
    302: p(302, 301, "cargo"),
    400: p(400, 1, "sccache"),
    401: p(401, 400, "rustc"),
    500: Proc(500, 1, "mds_stores", "/System/x/mds_stores", 0, None, 0, None),
    600: p(600, 1, "Widget", path="/Applications/Widget.app/Contents/MacOS/Widget"),
    700: p(700, 1, "python3", script="pin_sweep.py"),
    800: p(800, 1, "foo", path="/usr/local/bin/foo"),
    900: p(900, 100, "python3", script="affected_tests.py"),
    901: p(901, 100, "Python", script="foo.py"),
}


def test_a_family_is_the_outermost_house_context_else_the_nearest_tool():
    fam = ls.families(TABLE)
    assert fam[103] == "agent:rust-build"  # rustc <- cargo <- zsh <- claude: no house context
    assert fam[100] == "agent"
    assert fam[202] == "land"  # xdist worker <- pytest <- land.sh
    assert fam[302] == "push-gate"  # cargo <- gate.sh <- push_gate.sh: the OUTERMOST context
    assert fam[401] == "sccache"  # the server's compiles are the server's
    assert fam[500] == "spotlight"
    assert fam[600] == "app:Widget"
    assert fam[700] == "pin-sweep"
    assert fam[800] == "exe:foo"
    assert fam[900] == "land"  # the enqueue pre-step, run by an agent, is the land's
    assert fam[901] == "agent:python"


def test_a_child_reaped_between_samples_is_counted_once():
    # prev: P own 1, C own 2. cur: P own 1.5; C gone, reaped by P with final own 3, and a
    # sibling born and reaped unseen burned 2: P's reaped time rose 0 -> 5.
    # New CPU: P 0.5 + C's last 1 + the unseen 2 = 3.5; P keeps 0.5, its children 5 - 2 = 3.
    prev = {10: p(10, 1, "cargo", 1.0, 0.0), 11: p(11, 10, "rustc", 2.0, 0.0)}
    cur = {10: p(10, 1, "cargo", 1.5, 5.0)}
    own, reaped = ls.attribute(prev, cur)
    assert own == {10: 0.5}
    assert reaped == {10: 3.0}


def test_a_vanished_grandchild_is_taken_off_the_live_ancestor_too():
    # A(own 1) -> B(own 1) -> C(own 1). Both B and C end; B's final own 1.5 having reaped C's
    # final 1.5, so A's reaped time is 3. Seen already: B 1 + C 1 = 2; the new CPU is 1.
    prev = {1: p(1, 0, "a", 1, 0), 2: p(2, 1, "b", 1, 0), 3: p(3, 2, "c", 1, 0)}
    cur = {1: p(1, 0, "a", 1, 3)}
    own, reaped = ls.attribute(prev, cur)
    assert own == {1: 0.0}
    assert reaped == {1: 1.0}


def test_a_new_process_counts_whole_and_a_reused_pid_is_a_new_process():
    prev = {5: p(5, 1, "x", 9.0, 0, start=1)}
    cur = {5: p(5, 1, "y", 2.0, 0, start=2), 6: p(6, 1, "z", 4.0, 1.0)}
    own, reaped = ls.attribute(prev, cur)
    assert own == {5: 2.0, 6: 4.0}
    assert reaped == {6: 1.0}


def test_repo_of_names_the_repo_and_the_lane_worktree(tmp_path):
    main = tmp_path / "Projects" / "ztools"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / "src").mkdir()
    wt = tmp_path / "scratch" / "ZT.ITEM" / "wt"
    (wt / "crates").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt\n")
    resolve = ls.RepoResolver()
    assert resolve(str(main / "src")) == ("ztools", "")
    assert resolve(str(wt / "crates")) == ("ztools", "ZT.ITEM")
    assert resolve(str(tmp_path)) == ("", "")
    assert resolve(None) == ("", "")


def _snap(t, busy, procs, load1=4.0):
    return Snapshot(t, busy, load1, 16, procs)


def test_a_record_keeps_names_and_numbers_never_paths_or_arguments():
    secret = "/Users/u/Projects/secret-dir"
    prev = _snap(0, 100.0, {7: p(7, 1, "Python", 1.0, 0, script="pytest", cwd=secret)})
    cur = _snap(
        30,
        112.0,
        {
            7: p(7, 1, "Python", 11.0, 0, script="pytest", cwd=secret, rss=2048),
            8: p(8, 1, "tiny", 0.01),
        },
    )
    rec = ls.record(prev, cur, ls.RepoResolver(), self_s=0.05)
    assert rec["dt"] == 30 and rec["busy_s"] == 12.0 and rec["self_s"] == 0.05
    assert rec["rows"][0] == ["pytest", "", "", "Python", 10.0, 2.0]
    assert ["exe:tiny", "", "", "*", 0.01, 0.0] in rec["rows"]  # under the floor: merged by family
    assert secret not in json.dumps(rec) and "/usr/bin" not in json.dumps(rec)


def _ledger(tmp_path):
    rows = [
        {
            "t": 1000.0,
            "dt": 30.0,
            "busy_s": 100.0,
            "load1": 8.0,
            "cores": 16,
            "self_s": 0.03,
            "rows": [
                ["land", "goh", "", "Python", 60.0, 100.0],
                ["app:Widget", "", "", "Widget", 30.0, 50.0],
            ],
        },
        {
            "t": 1030.0,
            "dt": 30.0,
            "busy_s": 100.0,
            "load1": 40.0,
            "cores": 16,
            "self_s": 0.03,
            "rows": [
                ["land", "goh", "", "rustc", 20.0, 0.0],
                ["spotlight", "", "", "mds", 5.0, 0.0],
            ],
        },
    ]
    d = tmp_path / "ledger"
    d.mkdir()
    (d / "sources-1970-01-01.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def test_the_report_ranks_families_and_names_what_it_could_not_attribute(tmp_path):
    rep = ls.report(ls.read_ledger(_ledger(tmp_path), since=0), by="family")
    # busy 200; attributed 60 + 30 + 20 + 5 = 115; unattributed 85.
    assert [r["key"] for r in rep["rank"]] == ["land", "app:Widget", "spotlight"]
    assert rep["rank"][0]["cpu_s"] == 80.0 and rep["rank"][0]["share"] == 0.4
    assert rep["coverage"] == 0.575 and rep["unattributed_s"] == 85.0
    # load's cpu-s: min(8, 16) * 30 + min(40, 16) * 30 = 720; the sampler: 0.06 s over 60 s.
    assert rep["load_cpu_s"] == 720.0
    assert rep["self_pct_core"] == 0.1


def test_goh_load_report_prints_the_ranking(tmp_path):
    out = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "lib" / "load_sources.py"),
            "--report",
            "--since",
            "0",
            "--ledger-dir",
            str(_ledger(tmp_path)),
            "--by",
            "family+repo",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=True,
    ).stdout
    assert "land" in out and "goh" in out
    assert "57.5%" in out and "unattributed" in out


def test_script_of_and_parse_ps():
    assert script_of(["python3", "-u", "-m", "pytest.__main__", "-x"]) == "pytest"
    assert script_of(["bash", "/x/tools/land.sh", "--item", "A"]) == "land.sh"
    assert script_of(["bash", "-c", "echo hi"]) is None
    rows = parse_ps(
        "  1     0   1:02.50  12 /sbin/launchd\n 42 1 1-00:00:01 7 /A b/Claude Helper\n"
    )
    assert rows[1].cpu_s == 62.5 and rows[42].cpu_s == 86401 and rows[42].exe == "Claude Helper"
