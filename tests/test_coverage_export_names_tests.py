"""A coverage export that failed because TESTS failed names every one of them (BACKLOG P1c).

Run 42930175911: goh's suite ran as the coverage gate's external export, 78 tests failed and 12
errored, and the step printed the last 25 lines of the export log -- twelve ERROR lines and none of
the 78 FAILED ones. The log lived on a hosted runner that was gone when the run was read, so the
failures could not be named at all. cargo's `test ... FAILED` lines were already parsed; pytest's
short summary (`FAILED node - reason`, `ERROR node - reason`) is now too, whole, never a tail.
"""

from __future__ import annotations

import subprocess

from conftest import REPO_ROOT

GATE = REPO_ROOT / "gates" / "coverage_gate.sh"


def _export_failed(log):
    script = (
        'eval "$(sed -n "/^export_failed()/,/^}/p" "$1")"\n'
        'err() { printf "ERR %s\\n" "$*" >&2; }\n'
        'warn() { printf "WARN %s\\n" "$*" >&2; }\n'
        'export_failed external "$2"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "-", str(GATE), str(log)], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=False,
    ).stderr  # fmt: skip


def test_every_failed_pytest_node_is_named_not_only_the_tail(tmp_path):
    log = tmp_path / "part.log"
    lines = [f"FAILED tests/test_a.py::test_{i} - AssertionError" for i in range(40)]
    lines += [f"ERROR tests/test_b.py::test_{i} - NameError" for i in range(5)]
    lines += ["40 failed, 100 passed, 5 errors in 1.00s"] + ["noise"] * 30
    log.write_text("\n".join(lines) + "\n")
    said = _export_failed(log)
    assert "TESTS FAILED under coverage" in said, said
    named = {ln.split()[-1] for ln in said.splitlines() if ln.strip()}
    for i in range(40):
        assert f"tests/test_a.py::test_{i}" in named, i
    for i in range(5):
        assert f"tests/test_b.py::test_{i}" in named, i


def test_a_clean_export_failure_names_no_tests(tmp_path):
    log = tmp_path / "part.log"
    log.write_text("error: could not compile\n")
    assert "TESTS FAILED" not in _export_failed(log)


def _export_failed_env(log, tail):
    script = (
        'eval "$(sed -n "/^export_failed()/,/^}/p" "$1")"\n'
        'err() { printf "ERR %s\\n" "$*" >&2; }\n'
        'warn() { printf "WARN %s\\n" "$*" >&2; }\n'
        'export_failed external "$2"\n'
    )
    env = {"PATH": "/usr/bin:/bin"} | ({"GOH_TAIL": tail} if tail else {})
    return subprocess.run(
        ["bash", "-c", script, "-", str(GATE), str(log)], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=False, env=env,
    ).stderr  # fmt: skip


def test_the_export_log_tail_is_goh_tail_long_and_says_what_it_dropped(tmp_path):
    """Run 42944992082: GOH_TAIL=100000 printed the failed STEP whole, but the export log under it
    still came as a fixed 25-line tail -- one test's ruff refusal, and the reasons of the other 64
    red tests lost with the runner. The tail is GOH_TAIL long, and a cut one counts what it cut."""
    log = tmp_path / "part.log"
    log.write_text("".join(f"line {i}\n" for i in range(300)))
    whole = _export_failed_env(log, "100000")
    assert "    line 0\n" in whole, whole[-400:]
    cut = _export_failed_env(log, "")
    assert "    line 0\n" not in cut and "    line 299\n" in cut, cut
    assert "275 earlier lines not shown" in cut, cut
