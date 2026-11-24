"""lib/terse.py: a lane-facing goh command's failure is a summary by default (GOH.TERSEOUT).

Every line a goh tool prints is re-read on every later call of the lane that ran it: GOH-TINY's
lane reached ~400k context over 308 calls. `goh land --wait`, `goh land --check` and the
foreground pre-step printed whole logs, tails and excerpts on a red. The contract: the verdict
line, each failing test id with its first assertion line, and the log path, under 30 lines;
`--full` (GOH_FULL=1) prints the rest.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from conftest import REPO_ROOT
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import land_check
import land_prestep
import land_records
import landq_look
import terse


def _pytest_log(tests: int, frames: int = 80) -> str:
    """A pytest run's red output: TESTS failing sections of FRAMES lines each, then the summary."""
    out = ["noise line"] * 50 + ["=" * 10 + " FAILURES " + "=" * 10]
    for n in range(tests):
        out += [f"{'_' * 10} test_red_{n} {'_' * 10}", *(f"    frame {i}" for i in range(frames))]
        out += [f"E       AssertionError: planted {n}", "E       assert 1 == 2", "tests/t.py:9: x"]
    out.append("=" * 10 + " short test summary info " + "=" * 10)
    failed = "FAILED tests/test_t.py::test_red_{n} - AssertionError: planted {n}"
    out += [failed.format(n=n) for n in range(tests)]
    return "\n".join(out) + "\n"


def test_a_red_summary_names_each_failing_test_its_first_assertion_and_the_log():
    lines = terse.report("red @ abc12345", _pytest_log(3), "/state/logs/x.log")
    assert len(lines) < 30, lines
    assert lines[0] == "red @ abc12345"
    for n in range(3):
        assert f"  tests/test_t.py::test_red_{n}: AssertionError: planted {n}" in lines, lines
    assert "frame 7" not in "\n".join(lines) and "noise" not in "\n".join(lines)
    assert lines[-1] == "log /state/logs/x.log (--full prints it whole)", lines


def test_many_failing_tests_still_fit_and_say_how_many_were_cut():
    lines = terse.report("red", _pytest_log(60, frames=2), "/l.log")
    assert len(lines) < 30, lines
    assert any("more failing tests" in ln and "/l.log" not in ln for ln in lines), lines


def test_a_log_with_no_pytest_failure_keeps_its_marked_lines_then_its_last_ones():
    log = "\n".join([*(f"step {i} ok" for i in range(300)), "✗ structural: map row missing", "end"])
    lines = terse.report("red", log, "/l.log")
    assert len(lines) < 30, lines
    assert "  ✗ structural: map row missing" in lines and "  end" in lines, lines
    assert "  step 0 ok" not in lines


def test_full_prints_the_whole_log(monkeypatch):
    monkeypatch.setenv("GOH_FULL", "1")
    log = _pytest_log(3)
    lines = terse.report("red", log, "/l.log")
    assert any("frame 79" in ln for ln in lines) and len(lines) > len(log.splitlines()), lines[:5]


def _red_done(q: Path, log: Path) -> None:
    (q / "running").mkdir(parents=True)
    rec = {"item": "T.T", "mode": "land", "state": "red", "sha": "c" * 40, "log": str(log),
           "why": "the pre-land tests failed on cccccccc\n" + "excerpt line\n" * 200}  # fmt: skip
    land_records.write(q, "01-T.T", rec)


def test_a_red_wait_prints_a_summary_under_30_lines(tmp_path, monkeypatch, capsys):
    """GOH.TERSEOUT: the wait printed the record's whole `why` (a pre-step's excerpt of up to 400
    lines) and the log's last 30 lines, which name no failing test once a land's summary is past."""
    monkeypatch.setattr(landq_look, "poll", 0.01)
    monkeypatch.delenv("GOH_FULL", raising=False)
    log = tmp_path / "land.log"
    log.write_text(_pytest_log(4) + "\n".join(f"after {i}" for i in range(60)) + "\n")
    _red_done(tmp_path / "q", log)
    assert landq_look.wait(tmp_path / "q", "T.T", patience(30), lambda: None) == 1
    out = capsys.readouterr()
    said = (out.out + out.err).strip().splitlines()
    assert len(said) < 30, "\n".join(said)
    assert any("test_red_3: AssertionError: planted 3" in ln for ln in said), said
    assert any(str(log) in ln for ln in said) and "excerpt line" not in out.out + out.err
    assert "exit 1" in said[-1]


def test_a_red_wait_with_full_prints_the_log_whole(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(landq_look, "poll", 0.01)
    monkeypatch.setenv("GOH_FULL", "1")
    log = tmp_path / "land.log"
    log.write_text(_pytest_log(4))
    _red_done(tmp_path / "q", log)
    assert landq_look.wait(tmp_path / "q", "T.T", patience(30), lambda: None) == 1
    out = capsys.readouterr()
    assert "frame 0" in out.err and "excerpt line" in out.out + out.err


def test_the_wait_heartbeat_backs_off(monkeypatch):
    """An unchanged state said every 60 s is 30 lines over a 30 min land; the gap doubles."""
    monkeypatch.delenv("GOH_FULL", raising=False)
    assert [terse.next_gap(g) for g in (60.0, 120.0, 480.0)] == [120.0, 240.0, 960.0]
    monkeypatch.setenv("GOH_FULL", "1")
    assert terse.next_gap(60.0) == 60.0


def test_the_foreground_pre_step_streams_nothing_and_its_red_names_its_log(
    tmp_path, monkeypatch, capsys
):
    """GOH.TERSEOUT: `--enqueue --land --wait` streamed every pytest line of the pre-step into the
    lane; it goes to a log file, which the red names, and only GOH_FULL=1 streams it."""
    monkeypatch.delenv("GOH_FULL", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    out = tmp_path / "pytest.out"
    out.write_text(_pytest_log(2))
    script = tmp_path / "land.sh"
    script.write_text(f"cat {out}; exit 1\n")
    log = tmp_path / "pre.log"
    try:
        land_prestep.run(tmp_path, "a" * 40, script, (), log=log)
    except land_prestep.Red as e:
        red = str(e)
    else:
        raise AssertionError("a red pre-step returned")
    said = capsys.readouterr()
    assert "frame 3" not in said.err + said.out, said.err[:400]
    assert "frame 3" in log.read_text() and f"log {log}" in red, red[:400]
    monkeypatch.setenv("GOH_FULL", "1")
    with contextlib.suppress(land_prestep.Red):
        land_prestep.run(tmp_path, "a" * 40, script, (), log=log)
    assert "frame 3" in capsys.readouterr().err


def test_a_refused_check_guard_prints_a_summary_not_a_25_line_tail(monkeypatch, tmp_path):
    """`goh land --check` printed each refused guard's last 25 lines: two guards were 50+."""
    monkeypatch.delenv("GOH_FULL", raising=False)
    node = "tests/test_t.py::test_red_0"
    monkeypatch.setattr(land_check, "_env", lambda _r: {"GOH_PY": "python3"})
    monkeypatch.setattr(land_check, "tests", lambda *_a: [node])
    monkeypatch.setattr(land_check, "steps", lambda *_a: [])
    monkeypatch.setattr(land_check, "_run", lambda *_a: (1, _pytest_log(2), 1.0))
    said: list[str] = []
    assert land_check.run(tmp_path, say=said.append)
    text = "\n".join(said)
    assert len(text.splitlines()) < 30 and "frame" not in text, text
    assert "test_red_1: AssertionError: planted 1" in text, text


def test_a_red_whose_run_named_no_test_carries_the_worker_s_own_words(tmp_path, monkeypatch):
    """GOH.REMOTEREDSILENT: the record of a pre-step whose xdist run collected nothing carries what
    tests/_xdist_usage.py said, not only `exit 1`."""
    monkeypatch.delenv("GOH_FULL", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    out = tmp_path / "pytest.out"
    out.write_text(
        "".join(f"✗ by hand line {i}\n" for i in range(12))
        + "bringing up nodes...\nbringing up nodes...\n\n"
        "✗ an xdist worker's collection failed: not found: tests/t.py::test_x\n"
        "(no match in any of [<Module t.py>])\n"
        "✗ land.sh: the pre-land tests failed on aaaaaaaa in 120 s -- nothing landed\n"
    )
    script = tmp_path / "land.sh"
    script.write_text(f"cat {out}; exit 1\n")
    try:
        land_prestep.run(tmp_path, "a" * 40, script, (), log=tmp_path / "pre.log")
    except land_prestep.Red as e:
        red = str(e)
    else:
        raise AssertionError("a red pre-step returned")
    assert "collection failed: not found: tests/t.py::test_x" in red, red
