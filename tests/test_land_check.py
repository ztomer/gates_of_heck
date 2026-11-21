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

# Each STRAYSIGNAL3 refusal, planted back: (file, the fix's text, the text before the fix).
PLANTS = (
    ("docs/map.md", "| `load_reap_code.py` |", ""),  # the whole row
    (
        "tests/cap_headroom_allow.txt",
        "lib/load_sources.py\n",
        "lib/load_reap.py\nlib/load_sources.py\n",
    ),
    ("tests/test_saved_pid_probes.py", "env=hermetic_env(**env)", "env={**os.environ, **env}"),
    ("tests/test_saved_pid_probes.py", "import REPO_ROOT, hermetic_env\n", "import REPO_ROOT\n"),
    ("tests/test_saved_pid_probes.py", 'TREE_READS = ("lib/",)', "_READS = ()"),
)
REFUSED = {
    "tests/test_wiring.py::test_every_script_has_a_row_in_the_map",
    "tests/test_cap_headroom.py::test_stale_entries_fail",
    "tests/test_no_env_spread.py::test_no_test_spreads_os_environ_beyond_the_seed",
    "tests/test_declared_reads.py::test_every_test_joining_a_path_it_does_not_spell_declares_its_reads",
}


def _git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True,
                          check=True, timeout=patience(120), env=hermetic_env(drop_git=True),
                          ).stdout  # fmt: skip


def _planted_clone(dest: Path) -> Path:
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
    _git(dest, "branch", "-f", "main", "HEAD")
    for rel, fixed, before in PLANTS:
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
def test_the_check_names_each_of_straysignal3s_three_refusals_in_seconds(
    tmp_path, goh, monkeypatch
):
    clone = _planted_clone(tmp_path / "clone")
    monkeypatch.setenv("GOH_BIN", str(goh))  # the suite's build of these crates: the clone's own
    said: list[str] = []
    t0 = time.monotonic()
    refused = land_check.run(clone, None, said.append)
    took = time.monotonic() - t0
    assert set(refused) == REFUSED, "\n".join(said)
    assert_sooner(took, 30, 200, "goh land --check over STRAYSIGNAL3's planted tries")


def test_every_check_step_is_verbatim_a_land_gate_step():
    """The check runs what the gate runs, never a second copy of it."""
    ci = land_check.gatesrc(REPO_ROOT, "GOH_CI_STEPS").split(":")
    steps = land_check.steps(REPO_ROOT)
    assert steps, "goh declares GOH_LAND_CHECK_STEPS"
    assert not [s for s in steps if s not in ci], ci


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
    (done / "1.json").write_text(json.dumps({"state": "red", "guards": guards}))
    times = tmp_path / "durations.json"
    times.write_text(
        json.dumps(
            {
                "tests/test_c.py::test_fast": 0.1,
                "tests/test_c.py::test_slow[1]": 0.6,
                "tests/test_c.py::test_slow[2]": 0.6,
                "tests/test_b.py::test_q": 3.0,
            }
        )
    )
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(times))
    got = land_check.tests(tmp_path / "tree", tmp_path / "q")
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
        "✗ [changelog_tags] 1 way the changelog and the tags disagree (tracked):\n"
        "✗ [1/9] FAILED (exit status 1): ./gates/rust_gate.sh\n"
    )
    red = {"state": "red", "log": str(log), "why": "gate exit 1"}
    want = [
        "tests/test_wiring.py::test_every_script_has_a_row_in_the_map",
        "tests/test_x.py::test_y",
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


def test_check_and_enqueue_refuse_on_a_guard_and_record_it(repo, tmp_path, env):  # noqa: F811
    wt, tip = _land_wt(repo, tmp_path)
    planted = "echo '✗ [planted_guard] a planted refusal'; exit 1"
    red = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "true", "GOH_LAND_CHECK_STEPS": planted}
    checked = landq(red, "--check", str(wt))
    assert checked.returncode == 1 and "[planted_guard]" in checked.stdout + checked.stderr, checked
    done = landq(red, "--enqueue", "--land", str(wt), "--item", "T.C", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode == 1 and "nothing queued" in out and "[planted_guard]" in out, out
    assert _pending(tmp_path) == []
    (rec,) = [json.loads(p.read_text()) for p in (tmp_path / "landq" / "done").glob("*.json")]
    assert (rec["state"], rec["stage"], rec["sha"]) == ("red", "check", tip), rec
    assert rec["guard"] == f"{planted} [planted_guard]", rec
    assert recorded(red)["T.C"]["guard"] == rec["guard"]
    slow = {**red, "GOH_LAND_CHECK_STEPS": "exit 124"}  # a step past its deadline: no verdict
    checked = landq(slow, "--check", str(wt))
    assert checked.returncode == 0 and "no verdict" in checked.stdout + checked.stderr, checked
    green = {**red, "GOH_LAND_CHECK_STEPS": "true"}
    assert landq(green, "--check", str(wt)).returncode == 0
    done = landq(green, "--enqueue", "--land", str(wt), "--item", "T.C", "--no-start")
    assert done.returncode == 0 and len(_pending(tmp_path)) == 1, done.stdout + done.stderr
