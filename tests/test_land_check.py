"""`goh land --check WORKTREE` and the enqueue's check (lib/land_check.py, GOH.LANDCHECK).

GOH.STRAYSIGNAL3 was refused three times at land, each after a 200-900 s pre-step, by guards that
read the tree and need no suite: a missing docs/map.md row (fc0cfd24), a stale 450-line headroom
entry (07f6a53d), and the env-spread and declared-reads guards (5deebdba). The replay plants each
of those three defects back into a clone of this tree and asks the check, which must name every
one of the four guards -- and nothing else, so a guard the clone already failed is no proof --
in seconds. The rest pins the parts: the steps are the land gate's own, the guard tests are the
declared and the measured ones, a red land's record names its guard, and the enqueue refuses.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_landq import _land_wt, _pending, env, landq, recorded  # noqa: F401 -- `env` is a fixture
from timing_bounds import assert_sooner, patience

sys.path.insert(0, str(REPO_ROOT))
from lib import land_check

TREE_READS = ("docs/map.md", "tests/", ".gatesrc")  # a clone of the whole tree, planted

# Each STRAYSIGNAL3 refusal, planted back, ONE CASE EACH (GOH.PRELONGPOLE): the guard the check must
# name, and the plants that break it (file, the fix's text, the text before the fix). The three
# refusals together were one 67 s test (a pre-step's floor at every enqueue); each alone is a
# clone, five cheap steps and the guard tests, which xdist spreads over the workers.
REFUSALS = {
    "map-row": (
        "tests/test_wiring.py::test_every_script_has_a_row_in_the_map",
        (("docs/map.md", "| `load_reap_code.py` |", ""),),  # the whole row
    ),
    "headroom": (
        "tests/test_cap_headroom.py::test_stale_entries_fail",
        (
            (
                "tests/cap_headroom_allow.txt",
                "lib/load_sources.py\n",
                "lib/load_reap.py\nlib/load_sources.py\n",
            ),
        ),
    ),
    "env-spread": (
        "tests/test_no_env_spread.py::test_no_test_spreads_os_environ_beyond_the_seed",
        (
            (
                "tests/test_saved_pid_probes.py",
                "env=hermetic_env(**env)",
                "env={**os.environ, **env}",
            ),
            (
                "tests/test_saved_pid_probes.py",
                "import REPO_ROOT, hermetic_env\n",
                "import REPO_ROOT\n",
            ),
        ),
    ),
    "declared-reads": (
        "tests/test_declared_reads.py::test_every_test_joining_a_path_it_does_not_spell_declares_its_reads",
        (("tests/test_saved_pid_probes.py", 'TREE_READS = ("lib/",)', "_READS = ()"),),
    ),
}
# The one step the replay does not run: `structural --full` over a fresh clone is 43 s alone (the
# shell lint's first pass), and none of the plants is its business; the other five run, and
# test_every_check_step_is_verbatim_a_land_gate_step pins that the list is the land gate's.
SKIPPED_STEP = "./gates/structural.sh --full"


def _git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True,
                          check=True, timeout=patience(120), env=hermetic_env(drop_git=True),
                          ).stdout  # fmt: skip


def _planted_clone(dest: Path, plants: tuple[tuple[str, str, str], ...]) -> Path:
    """This checkout as it stands, committed in a clone with `main` at it, then the plants."""
    subprocess.run(["git", "clone", "-q", "--shared", str(REPO_ROOT), str(dest)], check=True,
                   capture_output=True, stdin=subprocess.DEVNULL, timeout=patience(120),
                   env=hermetic_env(drop_git=True))  # fmt: skip
    diff = _git(REPO_ROOT, "diff", "HEAD", "--binary")
    if diff:
        _git(dest, "apply", "--whitespace=nowarn", data=diff)
    others = _git(REPO_ROOT, "ls-files", "-z", "--others", "--exclude-standard")
    if others:  # the untracked files too, as the lane's next commit holds them
        pack = subprocess.run(["tar", "-C", str(REPO_ROOT), "--null", "-T", "-", "-cf", "-"],
                              input=others, capture_output=True, check=True,
                              timeout=patience(120)).stdout  # fmt: skip
        subprocess.run(["tar", "-C", str(dest), "-xf", "-"], input=pack, capture_output=True,
                       check=True, timeout=patience(120))  # fmt: skip
    commit = ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-am")
    _git(dest, "add", "-A")
    _git(dest, *commit, "this tree", "--allow-empty")
    _git(dest, "update-ref", "refs/heads/main", "HEAD")  # the clone may be on main itself
    for rel, fixed, before in plants:
        path = dest / rel
        text = path.read_text()
        assert fixed in text, f"the replay's plant no longer applies: {fixed!r} not in {rel}"
        if before:
            path.write_text(text.replace(fixed, before, 1))
        else:  # the line that holds it, gone
            start = text.index(fixed)
            path.write_text(text[:start] + text[text.index("\n", start) + 1 :])
    _git(dest, *commit, "STRAYSIGNAL3's three refused tries, planted")
    return dest


@pytest.mark.load_sensitive(reason="times the check against the pre-step it replaces")
@pytest.mark.parametrize("name", REFUSALS)
def test_the_check_names_each_of_straysignal3s_three_refusals_in_seconds(
    name, tmp_path, goh, monkeypatch
):
    """One planted refusal in a clone: the check names its guard and nothing else (a guard the
    clone already failed is no proof), in seconds. The four cases are STRAYSIGNAL3's three
    refusals (the third broke two guards, so it plants two fixes back)."""
    refused_guard, plants = REFUSALS[name]
    ran = [s for s in land_check.steps(REPO_ROOT) if s != SKIPPED_STEP]
    assert len(ran) < len(land_check.steps(REPO_ROOT)), f"{SKIPPED_STEP} is no declared step"
    monkeypatch.setenv("GOH_LAND_CHECK_STEPS", ":".join(ran))
    clone = _planted_clone(tmp_path / "clone", plants)
    monkeypatch.setenv("GOH_BIN", str(goh))  # the suite's build of these crates: the clone's own
    said: list[str] = []
    t0 = time.monotonic()
    refused = land_check.run(clone, None, said.append)
    took = time.monotonic() - t0
    assert refused == [refused_guard], "\n".join(said)
    assert_sooner(took, 30, 200, "goh land --check over STRAYSIGNAL3's planted tries")


def test_every_check_step_is_verbatim_a_land_gate_step():
    """The check runs what the gate runs, never a second copy of it."""
    ci = land_check.gatesrc(REPO_ROOT, "GOH_CI_STEPS").split(":")
    steps = land_check.steps(REPO_ROOT)
    assert steps, "goh declares GOH_LAND_CHECK_STEPS"
    assert not [s for s in steps if s not in ci], ci
    no_pre = land_check.steps(REPO_ROOT, no_pre=True)[len(steps) :]
    assert no_pre, "goh declares GOH_LAND_NOPRE_STEPS (GOH.NOPREBATCH)"
    assert not [s for s in no_pre if s not in ci], ci


def test_the_guard_tests_are_the_declared_ones_and_the_measured_past_refusers(
    tmp_path, monkeypatch
):
    tests = tmp_path / "tree" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_a.py").write_text(
        "# goh-land-guard: test_x\ndef test_x(): pass\ndef test_y(): pass\n"
    )
    (tests / "test_b.py").write_text(
        "# goh-reads-staged: lib\ndef test_p(): pass\ndef test_q(): pass\n"
    )
    (tests / "test_c.py").write_text(
        "def test_fast(): pass\ndef test_slow(): pass\ndef test_new(): pass\n"
    )
    (tests / "test_d.py").write_text("# goh-land-guard:\ndef test_all(): pass\n")
    done = tmp_path / "q" / "done"
    done.mkdir(parents=True)
    guards = [
        "tests/test_c.py::test_fast",
        "tests/test_c.py::test_slow[1]@g",
        "tests/test_c.py::test_new",
    ]
    (done / "1.json").write_text(json.dumps({"state": "red", "repo": "/r", "guards": guards}))
    other = {"state": "red", "repo": "/other", "guards": ["tests/test_c.py::test_other"]}
    (done / "2.json").write_text(json.dumps(other))  # another repo's guard is not this one's
    times = tmp_path / "durations.json"
    times.write_text(
        json.dumps(
            {
                "tests/test_c.py::test_fast": 0.1,
                "tests/test_c.py::test_slow[1]": 0.6,
                "tests/test_c.py::test_slow[2]": 0.6,
                "tests/test_b.py::test_q": 12.0,
            }
        )
    )
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(times))
    (tests / "test_c.py").write_text((tests / "test_c.py").read_text() + "def test_other(): pass\n")
    times.write_text(
        json.dumps({**json.loads(times.read_text()), "tests/test_c.py::test_other": 0.1})
    )
    got = land_check.tests(tmp_path / "tree", tmp_path / "q", "/r")
    # declared: run unless measured slow; past refusers: only when measured cheap
    assert got == [
        "tests/test_a.py::test_x",
        "tests/test_b.py::test_p",
        "tests/test_d.py::test_all",
        "tests/test_c.py::test_fast",
    ], got


def test_a_red_lands_record_names_the_guard_its_log_shows(tmp_path):
    log = tmp_path / "x.log"
    log.write_text(
        "FAILED tests/test_wiring.py::test_every_script_has_a_row_in_the_map - Assertion\n"
        "E         FAILED tests/test_x.py::test_y[a]@grp - nope\n"
        "ERROR tests/test_e.py::test_setup - fixture blew up\n"
        "✗ [changelog_tags] 1 way the changelog and the tags disagree (tracked):\n"
        "✗ [1/9] FAILED (exit status 1): ./gates/rust_gate.sh\n"
    )
    red = {"state": "red", "log": str(log), "why": "gate exit 1"}
    want = [
        "tests/test_wiring.py::test_every_script_has_a_row_in_the_map",
        "tests/test_x.py::test_y",
        "tests/test_e.py::test_setup",
        "[changelog_tags]",
        "./gates/rust_gate.sh",
    ]
    assert land_check.fields(red) == {"guard": want[0], "guards": want}
    log.write_text("nothing a guard says\n")
    assert land_check.fields(red) == {"guard": "gate exit 1", "guards": ["gate exit 1"]}
    assert land_check.fields({**red, "state": "landed"}) == {}
    assert land_check.fields({**red, "guards": ["kept"]}) == {}
    done = tmp_path / "q" / "done"
    done.mkdir(parents=True)
    for n, g in enumerate(["a", "b", "a"]):
        (done / f"{n}.json").write_text(json.dumps({"state": "red", "guard": g}))
    (done / "9.json").write_text(json.dumps({"state": "landed"}))
    assert land_check.count(tmp_path / "q") == {"a": 2, "b": 1}
    # a pre-step's refusal at enqueue files no land record: it is learned, counted and run next
    refusal = (
        "the pre-land tests failed on 1234 (failing: tests/test_e.py::test_dump[x], tests/z.py)"
    )
    land_check.learn(tmp_path / "q", {"repo": "/r", "item": "T", "sha": "1234"}, refusal)
    land_check.learn(tmp_path / "q", {"repo": "/r"}, "no node named here")
    assert land_check.count(tmp_path / "q") == {"a": 2, "b": 1, "tests/test_e.py::test_dump": 1}
    assert land_check.history(tmp_path / "q", "/r") == ["tests/test_e.py::test_dump"]
    assert land_check.history(tmp_path / "q", "/elsewhere") == []


def test_the_guard_tests_split_into_runs_of_near_equal_cost(tmp_path, monkeypatch):
    times = tmp_path / "durations.json"
    times.write_text(json.dumps({"t.py::a": 6.0, "t.py::b": 3.0, "t.py::c": 3.0, "t.py::d": 0.5}))
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(times))
    assert land_check.shards(["t.py::a", "t.py::b", "t.py::c", "t.py::d"], 2) == [
        ["t.py::a", "t.py::d"],
        ["t.py::b", "t.py::c"],
    ]  # 6.5 s and 6 s
    assert land_check.shards(["t.py::a"], 3) == [["t.py::a"]]
    assert land_check.shards([], 3) == []


TABLE_TESTS = """\
def test_every_script_has_a_row():
    assert False, "lib/x.py has no row in docs/map.md"
def test_no_slow_table_rows(): pass
def test_plain_behaviour(): assert False
def test_no_new_manifest_entry(): pass
"""


def test_the_check_runs_the_table_tests_the_diff_chooses_and_names_the_table(tmp_path, monkeypatch):
    """GOH.TABLESEARLY: 25 of 48 reds in 100 land records were consistency-table tests, each a
    whole enqueue cycle. The check runs the ones the pre-step would choose for the diff, picked by
    lib/land_reds.py's rule (a plain test it chose is not run), cheapest first under a cap (an
    unmeasured one runs), and names the table the failing assertion names."""
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_t.py").write_text(TABLE_TESTS)
    times = tmp_path / "durations.json"
    times.write_text(
        json.dumps(
            {
                "tests/test_t.py::test_every_script_has_a_row": 0.1,
                "tests/test_t.py::test_no_slow_table_rows": land_check.JUDGE_EACH_S + 1,
                "tests/test_t.py::test_plain_behaviour": 0.1,
            }
        )
    )
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(times))
    monkeypatch.setattr(
        land_check,
        "chosen_by_diff",
        lambda root, judges=False: [] if judges else ["tests/test_t.py"],
    )
    assert land_check.judge_tests(tree) == [
        "tests/test_t.py::test_every_script_has_a_row",
        "tests/test_t.py::test_no_new_manifest_entry",
    ]
    said: list[str] = []
    refused = land_check.run(tree, None, said.append, "", tables=True)
    assert refused == ["tests/test_t.py::test_every_script_has_a_row"], said
    assert any("table test" in s and "docs/map.md" in s for s in said), said
    said.clear()
    assert land_check.run(tree, None, said.append, "") == [], said  # the enqueue's check: none


def test_check_and_enqueue_refuse_on_a_guard_and_record_it(repo, tmp_path, env):  # noqa: F811
    wt, tip = _land_wt(repo, tmp_path)
    planted = "echo '✗ [planted_guard] a planted refusal'; exit 1"
    red = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "true", "GOH_LAND_CHECK_STEPS": planted}
    checked = landq(red, "--check", str(wt))
    assert checked.returncode == 1 and "[planted_guard]" in checked.stdout + checked.stderr, checked
    done = landq(red, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.C", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode == 1 and "nothing queued" in out and "[planted_guard]" in out, out
    assert _pending(tmp_path) == []
    (rec,) = [json.loads(p.read_text()) for p in (tmp_path / "landq" / "done").glob("*.json")]
    assert (rec["state"], rec["stage"], rec["sha"]) == ("red", "check", tip), rec
    assert rec["guard"] == "[planted_guard]", rec
    told = recorded(red)["T.C"]["guard"]  # what loopctl was told
    assert told == rec["guard"]
    slow = {**red, "GOH_LAND_CHECK_STEPS": "exit 124"}  # a step past its deadline: no verdict
    checked = landq(slow, "--check", str(wt))
    assert checked.returncode == 0 and "no verdict" in checked.stdout + checked.stderr, checked
    green = {**red, "GOH_LAND_CHECK_STEPS": "true"}
    checked = landq(green, "--check", str(wt))
    assert checked.returncode == 0, checked.stdout + checked.stderr
    done = landq(
        green, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.C", "--no-start"
    )
    assert done.returncode == 0 and len(_pending(tmp_path)) == 1, done.stdout + done.stderr


# GOH.NOPREBATCH (2026-11-23): GOH.NITS3 enqueued with `--no-pre`, its new rubyshell.rs reached the
# lander unlinted, and clippy turned its whole batch red. `--no-pre` waives the pre-step's TESTS for
# the reason it names, never the cheap lint steps (.gatesrc GOH_LAND_NOPRE_STEPS).
def test_a_no_pre_enqueue_still_runs_the_lint_steps_and_a_finding_refuses_it(
    repo,
    tmp_path,
    env,  # noqa: F811
):
    wt, _ = _land_wt(repo, tmp_path)
    lint = "echo '✗ [planted_lint] too_long_first_doc_paragraph'; exit 1"
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "exit 1", "GOH_LAND_CHECK_STEPS": "true",
           "GOH_LAND_NOPRE_STEPS": lint}  # fmt: skip
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.NP",
                 "--no-start", "--no-pre", "lane ran the set by hand")  # fmt: skip
    out = done.stdout + done.stderr
    assert done.returncode == 1 and "[planted_lint]" in out and "nothing queued" in out, out
    assert _pending(tmp_path) == []
    green = {**env, "GOH_LAND_PRE": "true"}  # the pre-step runs: the no-pre steps are not its
    done = landq(
        green, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.NP", "--no-start"
    )
    out = done.stdout + done.stderr
    assert done.returncode == 0 and "[planted_lint]" not in out, out
    assert len(_pending(tmp_path)) == 1


# GOH.LANDCHECK2: GOH-TINY's land went red 7 times in 45 min (2026-11-22), each on a different judge
# only the pre-step ran, each attempt naming one. A fixture lane breaking three judges the old check
# never ran: a table test over the old table cap, a whole-tree judge that is no table test, and the
# diff coverage judge; its selector is the tree's own tools/affected_tests.py, as goh's is.
JUDGES = """\
def test_every_lib_has_a_row():
    assert False, "lib/x.py has no row in docs/map.md"
def test_goh_obeys_every_scanner_it_ships():
    assert False, "self_walk[shell]: 1 finding"
def test_plain_behaviour(): assert False
"""
SELECTOR = """\
import sys
if "diff-cov" in sys.argv:
    print("x [diff-coverage] 2 of 160 changed executable lines uncovered (since main):")
    print("    crates/goh/src/x.rs:12  let y = 1;")
    sys.exit(1)
if "judges" in sys.argv:
    print("tests/test_j.py::test_goh_obeys_every_scanner_it_ships")
else:
    print("tests/test_j.py")
"""


def test_one_check_names_every_judge_a_lane_breaks_with_diff_coverage(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tools").mkdir()
    (tree / "tests" / "test_j.py").write_text(JUDGES)
    (tree / "tools" / "affected_tests.py").write_text(SELECTOR.replace("x [", "✗ ["))
    times = tmp_path / "durations.json"
    times.write_text(json.dumps({
        "tests/test_j.py::test_every_lib_has_a_row": 26.0,  # over the old table cap (25 s)
        "tests/test_j.py::test_goh_obeys_every_scanner_it_ships": 67.0,
        "tests/test_j.py::test_plain_behaviour": 0.1,
    }))  # fmt: skip
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(times))
    monkeypatch.setenv("GOH_PY", sys.executable)
    said: list[str] = []
    refused = land_check.run(tree, None, said.append, "", tables=True)
    assert sorted(refused) == [
        "[diff-coverage]",
        "tests/test_j.py::test_every_lib_has_a_row",
        "tests/test_j.py::test_goh_obeys_every_scanner_it_ships",
    ], said
    assert (
        "tests/test_j.py::test_plain_behaviour" not in refused
    )  # the lane's own test: not a judge
    assert any("diff coverage: REFUSED" in s and "x.rs:12" in s for s in said), said
