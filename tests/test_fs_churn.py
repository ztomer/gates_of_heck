"""The suite's file events are counted and held under a ceiling (GOH.FSCHURN2, tests/_fs_churn.py).

GOH.FSCHURN's cut did not hold and nothing saw it go: the suite was back at ~800 fresh directories
a second two days later. These pin the instrument (lib/fs_events.py reads an FSEvents stream and
names who made each event) and the ceiling: a planted churny test in a session the plugin watches
turns it red, naming the test; a quiet one stays green and its count is written.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import fs_events
from _fs_churn import recent_unmeasured, unmeasured_skip, verdict

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="FSEvents is macOS only")

CHURNY = (
    "import os\n"
    "def test_churny(tmp_path):\n"
    "    for i in range(100):\n"
    "        d = tmp_path / f'd{i}'\n"
    "        d.mkdir()\n"
    "        (d / 'f').write_text('x')\n"
)
QUIET = "def test_quiet(tmp_path):\n    (tmp_path / 'f').write_text('x')\n"


# A planted selection that deselects the way the land's carry does (tests/_land_verdicts.py,
# tests/_shard.py): through pytest's own hook, in every process that collects (each xdist worker).
DROPS = (
    "def pytest_collection_modifyitems(config, items):\n"
    "    drop = [i for i in items if i.name in {names!r}]\n"
    "    if drop:\n"
    "        config.hook.pytest_deselected(items=drop)\n"
    "        items[:] = [i for i in items if i not in drop]\n"
)


# A window the instrument marks unmeasured (drops, or no delivery through its end) is re-run this
# many times before the test fails naming the instrument, never the ceiling (GOH.FSDROPVERDICT).
WINDOWS = 3


def _session(
    tmp_path: Path, body: str, ceiling: dict, *args: str, drops: tuple[str, ...] | None = None
) -> tuple[subprocess.CompletedProcess, dict]:
    """A session the plugin watches. No `args`: a WHOLE run of the planted directory, the shape
    tools/pytest.sh counts; `drops` plants a conftest deselecting those tests (a carried run).
    A loaded box makes fseventsd drop events; such a window is no reading (GOH.FSDROPVERDICT), so
    it is run again, up to WINDOWS times, and only then fails, naming the instrument. Each window
    runs in a directory of its own: fseventsd folds a path's new events into its still-pending old
    ones, so a window re-made on the last one's paths read d0-d244 of 400 as removals or nothing
    (2026-11-23)."""
    why = []
    for _ in range(WINDOWS):
        window = Path(tempfile.mkdtemp(prefix="window.", dir=tmp_path))
        r, rep = _one_session(window, body, ceiling, *args, drops=drops)
        if rep.get("unmeasured"):
            assert "unmeasured, no verdict" in r.stdout, r.stdout[-1500:]
            why.append(rep["unmeasured"])
        elif lost := _short_of_the_plant(body, rep, ceiling, drops or ()):
            why.append(lost)
        else:
            return r, rep
    unmeasured_skip(why)
    raise AssertionError("unreachable: unmeasured_skip skips")


def _short_of_the_plant(
    body: str, rep: dict, ceiling: dict | None = None, drops: tuple[str, ...] = ()
) -> str:
    """Why a clean window read less than the planted test made, or "": fseventsd can lose a run of
    records with no drop flag (lib/fs_events.py), and a verdict on such a window judges the loss.
    The plant is the calibration: CHURNY makes 100 dirs, QUIET one file. A loss only undercounts,
    so a window that still read the churny test past a per-test ceiling under the plant is a
    reading of that ceiling: its red is a lower bound over the line (GOH.FSCHURNFLAKE: 354 of 400
    under load 30-110 refused a ship whose verdict was red all along). A plant the session
    deselects makes nothing, so a window without it is short of nothing (GOH.FSEVQUIET: a test
    that dropped the churny one skipped as unmeasured in every window, at any load)."""
    per_test = (ceiling or {}).get("per_test")  # no literal default: GOH.LOADSENSEGET's lint
    floor = 100 if per_test is None else min(100, per_test + 1)
    planted = CHURNY in body and "test_churny" not in drops
    if planted and rep["dirs_created"] < floor:
        return f"the instrument lost events: read {rep['dirs_created']} of the 100 planted dirs"
    if body == QUIET and rep["events"] < 1:
        return "the instrument lost events: read none of the planted write"
    return ""


def _one_session(
    tmp_path: Path, body: str, ceiling: dict, *args: str, drops: tuple[str, ...] | None = None
) -> tuple[subprocess.CompletedProcess, dict]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_planted.py").write_text(body)
    if drops is not None:
        (tmp_path / "conftest.py").write_text(DROPS.format(names=set(drops)))
    tmp, home = tmp_path / "t", tmp_path / "h"
    for d in (tmp, home):  # fresh roots: `tmp_path` is the window's own (`_session`)
        d.mkdir()
    out = tmp_path / "events.json"
    env = hermetic_env(
        TMPDIR=str(tmp), HOME=str(home), PYTEST_DEBUG_TEMPROOT=str(tmp),
        GATES_OF_HECK_FS_EVENTS=str(out), GATES_OF_HECK_FS_EVENTS_CEILING=json.dumps(ceiling),
        PYTHONPATH=str(REPO_ROOT / "tests"),
    )  # fmt: skip
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         "-p", "_fs_churn", *args],
        cwd=tmp_path, capture_output=True, text=True, timeout=patience(120), check=False,
        env=env, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert out.is_file(), f"the session wrote no count: {r.stdout[-1500:]}{r.stderr[-1500:]}"
    return r, json.loads(out.read_text())


@pytest.mark.load_sensitive
@darwin
def test_a_planted_churny_test_breaks_the_per_test_ceiling(tmp_path: Path) -> None:
    r, rep = _session(tmp_path, CHURNY, {"per_test": 75, "per_test_run": 10_000})
    assert r.returncode == 1, r.stdout[-1500:] + r.stderr[-1500:]
    assert "test_churny made" in r.stdout and "per-test ceiling 75" in r.stdout, r.stdout[-1500:]
    assert rep["per_test"]["test_churny"] > 75 and rep["dirs_created"] > 75, rep
    assert rep["faults"], rep


def test_a_window_that_lost_events_but_read_past_the_ceiling_is_a_reading() -> None:
    """The window the v0.150.0 ship's suite read (354 of 400 dirs, load 30-110) judges a 300
    per-test ceiling (here a quarter of each): a lower
    bound over it. One under the ceiling is still no reading, and a
    ceiling the plant clears only whole still needs the whole plant."""
    lossy = {"dirs_created": 88, "events": 175}
    assert _short_of_the_plant(CHURNY, lossy, {"per_test": 75}) == ""
    assert _short_of_the_plant(CHURNY, {"dirs_created": 72, "events": 145}, {"per_test": 75})
    assert _short_of_the_plant(CHURNY, lossy, {"per_test": 100_000})
    assert _short_of_the_plant(CHURNY, lossy)


def test_a_window_whose_plant_was_deselected_is_short_of_nothing() -> None:
    none_made = {"dirs_created": 4, "events": 10}
    assert _short_of_the_plant(CHURNY_AND_QUIET, none_made, None, ("test_churny",)) == ""
    assert _short_of_the_plant(CHURNY_AND_QUIET, none_made, None, ("test_quiet",))
    assert _short_of_the_plant(CHURNY_AND_QUIET, none_made)


@pytest.mark.load_sensitive
@darwin
def test_a_planted_churny_test_breaks_the_per_run_ceiling(tmp_path: Path) -> None:
    r, _ = _session(tmp_path, CHURNY, {"per_test": 100_000, "per_test_run": 50})
    assert r.returncode == 1, r.stdout[-1500:] + r.stderr[-1500:]
    assert "over the ceiling 50 each" in r.stdout, r.stdout[-1500:]


# Two workers: the controller collects nothing and each worker deselects alike. The suite's own
# PYTEST_PLUGINS (tools/pytest.sh) already loads xdist, and a second `-p xdist` is refused.
XDIST = (
    ("-n", "2") if "xdist" in os.environ.get("PYTEST_PLUGINS", "") else ("-p", "xdist", "-n", "2")
)
# A test the carried run deselects: the run is that much short of the whole suite.
CHURNY_AND_QUIET = CHURNY + QUIET


@pytest.mark.load_sensitive
@darwin
@pytest.mark.parametrize("xdist", [(), XDIST], ids=["plain", "xdist"])
def test_a_whole_run_over_the_mean_stays_red_with_nothing_deselected(
    tmp_path: Path, xdist: tuple[str, ...]
) -> None:
    """GOH.FSCEILINGRED, the red half: a carried run that carried NOTHING is the whole suite, and
    the per-run ceiling holds it, in a plain session and under xdist (whose controller collects
    nothing)."""
    r, rep = _session(
        tmp_path, CHURNY_AND_QUIET, {"per_test": 100_000, "per_test_run": 50}, *xdist, drops=()
    )
    assert r.returncode == 1, r.stdout[-1500:] + r.stderr[-1500:]
    assert "over the ceiling 50 each" in r.stdout, r.stdout[-1500:]
    assert rep["whole"] is True and rep["deselected"] == 0 and rep["tests_run"] == 2, rep


@pytest.mark.load_sensitive
@darwin
@pytest.mark.parametrize("xdist", [(), XDIST], ids=["plain", "xdist"])
def test_a_carried_run_is_not_judged_by_the_whole_run_mean(
    tmp_path: Path, xdist: tuple[str, ...]
) -> None:
    """GOH.FSCEILINGRED: the land's gate and the rust gate's external pytest are argument-less
    runs that DESELECT what the pre-step passed (1187 and 1468 of 5812 ran, 204.6 and 200.3 events
    each, 2026-11-21); the tests left are the change's own, the heaviest, so their mean is no
    reading of the ceiling `per_test_run` (re-set from a whole run). Its count is still written,
    and the per-test ceiling, which holds for any selection, still judges each test."""
    r, rep = _session(
        tmp_path, CHURNY_AND_QUIET, {"per_test": 100_000, "per_test_run": 50}, *xdist,
        drops=("test_quiet",),
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert rep["whole"] is False and rep["deselected"] == 1 and rep["tests_run"] == 1, rep
    assert rep["faults"] == [] and rep["events"] >= 100, rep
    assert "a partial run" in r.stdout, r.stdout[-1500:]
    r, rep = _session(
        tmp_path, CHURNY_AND_QUIET, {"per_test": 75, "per_test_run": 50}, *xdist,
        drops=("test_quiet",),
    )  # fmt: skip
    assert r.returncode == 1 and "per-test ceiling 75" in r.stdout, r.stdout[-1500:]


@pytest.mark.load_sensitive
@darwin
@pytest.mark.parametrize("narrow", [("test_planted.py",), ("-k", "churny")], ids=["path", "k"])
def test_a_narrowed_run_is_not_judged_by_the_whole_run_mean(
    tmp_path: Path, narrow: tuple[str, ...]
) -> None:
    """A caller that names the file for a narrowed run (tools/pytest.sh) gets its count, not the
    whole run's verdict: a path or a -k selects, whatever deselects or not."""
    r, rep = _session(
        tmp_path, CHURNY_AND_QUIET, {"per_test": 100_000, "per_test_run": 50}, *narrow
    )
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert rep["whole"] is False and rep["faults"] == [], rep


@pytest.mark.load_sensitive
@darwin
def test_a_quiet_session_is_green_and_its_count_is_written(tmp_path: Path) -> None:
    r, rep = _session(tmp_path, QUIET, {"per_test": 75, "per_test_run": 50})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert rep["tests_run"] == 1 and rep["faults"] == [] and rep["dropped"] == 0, rep
    assert rep["teardown_entries"] >= 1, rep  # the passed test's tmp_path went; its root did not
    assert 1 <= rep["events"] < 50, rep  # the stream saw the test's own write, and little else


def test_the_variable_is_popped_so_a_nested_session_records_nothing() -> None:
    assert os.environ.get("GATES_OF_HECK_FS_EVENTS") is None  # this session's controller took it


def test_creator_names_the_test_the_prefix_or_the_reused_dir() -> None:
    root = "/T/goh-pytest.slot03"
    roots = [root, "/H/home.slot07"]
    assert fs_events.creator(f"{root}/pytest-of-u/pytest-0/popen-gw2/test_foo_bar0/a/b", roots) == (
        "goh-pytest.slotN/pytest-N/test_foo_bar"
    )
    assert fs_events.creator(f"{root}/goh-display-seam.yz6xpt8t/case01/UI", roots) == (
        "goh-pytest.slotN/goh-display-seam.*"
    )
    assert fs_events.creator(f"{root}/goh-test-git.gw4.0/.git/HEAD", roots) == (
        "goh-pytest.slotN/goh-test-git.<worker>"
    )
    assert fs_events.creator("/H/home.slot07/.gitconfig", roots) == "home.slotN/.gitconfig"
    assert fs_events.creator("/elsewhere/x", roots) == "(outside the roots)"


def test_the_verdict_names_each_broken_ceiling() -> None:
    rep = {"events": 8000, "teardown_entries": 1000, "per_test": {"test_a": 50, "test_b": 7000}}
    assert verdict(rep, 10, {"per_test": 8000, "per_test_run": 1000}) == []
    faults = verdict(rep, 10, {"per_test": 5000, "per_test_run": 500})
    assert len(faults) == 2 and "test_b made 7000" in faults[0] and "900.0 each" in faults[1]
    # what the run leaves under its roots counts: its removal is events the stream never sees
    assert verdict(rep, 10, {"per_test": 8000, "per_test_run": 850}) != []
    # a partial run (GOH.FSCEILINGRED): only the per-test ceiling, which holds for any selection
    assert verdict(rep, 10, {"per_test": 8000, "per_test_run": 500}, whole=False) == []
    assert len(verdict(rep, 10, {"per_test": 5000, "per_test_run": 500}, whole=False)) == 1


def test_a_large_argument_run_that_carried_nothing_is_judged_by_the_means() -> None:
    """A land's pre-step (GOH.FSEVQUIET): its tests are named, so no run is `whole`, and nothing is
    carried. Its bytes are judged in a window that lost events, its events are not."""
    at = {"per_test": 100, "per_test_run": 50, "bytes_per_test_run": 1000, "mean_from": 10}
    rep = {"events": 400, "teardown_entries": 101, "per_test": {}, "bytes": 10_001, "deselected": 0}
    faults = verdict(rep, 10, at, whole=False)
    assert len(faults) == 2 and "50.1 each" in faults[0] and "10001 bytes" in faults[1], faults
    assert verdict(rep, 9, at, whole=False) == []  # a selection this small is no sample
    assert len(verdict(rep, 9, at)) == 2  # a whole run is judged at any size
    assert verdict({**rep, "deselected": 1}, 10, at, whole=False) == []  # what is left of a run
    lost = {"unmeasured": "drops", "teardown_entries": 101, "bytes": 10_001}
    assert len(verdict(lost, 10, at, whole=False)) == 1
    assert verdict({**lost, "bytes": 10_000}, 10, at, whole=False) == []


def test_a_byte_named_file_is_out_of_the_mean_and_the_rest_is_not() -> None:
    """A pre-step that drew the three heaviest named files read 76,138 bytes a test (2026-11-25)."""
    at = {"per_test": 1, "per_test_run": 1, "bytes_per_test_run": 10, "mean_from": 1}
    files = {"a.py": {"all_bytes": 9000, "tests": 1}, "b.py": {"all_bytes": 90, "tests": 9}}
    rep = {"unmeasured": "drops", "teardown_entries": 0, "bytes": 9090, "files": files}
    assert verdict(rep, 10, {**at, "named": {"a.py": {"bytes": 9000}}}) == []
    (fault,) = verdict(rep, 10, {**at, "named": {"a.py": {"events": 1}}})  # no byte ceiling: in
    assert "9090 bytes" in fault and "a run of 10" in fault
    files["b.py"]["all_bytes"], rep["bytes"] = 91, 9091
    (fault,) = verdict(rep, 10, {**at, "named": {"a.py": {"bytes": 9000}}})
    assert "91 bytes left written by a run of 9 (10 a test)" in fault


def test_a_lands_pre_step_is_counted_and_a_narrowed_run_by_hand_is_not() -> None:
    """The one line of tools/pytest.sh that says which runs are counted, run by bash itself."""
    runner = (REPO_ROOT / "tools" / "pytest.sh").read_text().splitlines()
    (line,) = [ln for ln in runner if "export GATES_OF_HECK_FS_EVENTS=" in ln]
    for args, record, counted in (
        ((), "", True), (("a.py",), "/r", True), (("a.py",), "", False), ((), "/r", True),
    ):  # fmt: skip
        script = f'PYTEST_DEBUG_TEMPROOT=/t\n{line}\necho "${{GATES_OF_HECK_FS_EVENTS:-none}}"'
        r = subprocess.run(
            ["bash", "-euc", script, "pytest.sh", *args], capture_output=True, text=True,
            env=hermetic_env(GOH_LAND_VERDICTS_RECORD=record), check=True,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip
        assert r.stdout.strip() == ("/t/fs-events.json" if counted else "none"), (args, record)


def test_the_recorded_ceiling_is_a_ratchet_with_both_bounds() -> None:
    ceiling = json.loads((REPO_ROOT / "tests" / "fs_events_ceiling.json").read_text())
    keys = ("per_test", "per_test_run", "bytes_per_test_run", "mean_from")
    assert all(isinstance(ceiling.get(k), int) and ceiling[k] > 0 for k in keys), ceiling


def test_a_head_export_a_test_made_is_counted_as_its_own() -> None:
    """GOH.FSCHURN3: a child spawned without GOH_LIVE exports the whole goh tree under its HOME
    (`.cache/goh/head/<sha>`); the report names each test's share of such exports."""
    root = "/T/goh-pytest.slot03"
    tally = fs_events.Tally([root])
    test = f"{root}/pytest-of-u/pytest-0/popen-gw2/test_spawns_bare0"
    made = fs_events.ITEM_CREATED | fs_events.ITEM_IS_FILE
    for n in range(3):
        tally.add(f"{test}/.cache/goh/head/0123abcd/lib/m{n}.py", made)
    tally.add(f"{test}/own.txt", made)
    rep = tally.report(1.0)
    assert rep["per_test"] == {"test_spawns_bare": 4}
    assert rep["head_export_per_test"] == {"test_spawns_bare": 3}
    assert rep["head_export_events"] == 3


@pytest.mark.load_sensitive
def test_an_unmeasured_run_skips_with_why_and_is_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOH.FSDROPVERDICT ruling: every window unmeasured is a skip naming the instrument, never a
    pass, and a ledger line the run's summary counts for a day, so it is never silent."""
    book = tmp_path / "ledger.log"
    monkeypatch.setenv("GATES_OF_HECK_FS_UNMEASURED_LEDGER", str(book))
    old = json.dumps({"time": 1.0, "test": "t", "reason": "two days ago and more"})
    book.write_text(old + "\n")
    why = ["the instrument dropped events (2 drop records, first /r 0x3): it undercounts"] * 3
    with pytest.raises(pytest.skip.Exception) as skipped:
        unmeasured_skip(why)
    assert str(skipped.value.msg) == (
        "fs_events unmeasured: the instrument dropped events in 3 windows of 3"
        f" (fseventsd: {why[0]})"
    )
    line = json.loads(book.read_text().splitlines()[-1])
    assert line["reason"] == skipped.value.msg and "test_an_unmeasured_run" in line["test"], line
    assert recent_unmeasured() == 1  # the old line is outside the last 24 h
