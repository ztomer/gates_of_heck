"""A step that PRINTS a failure and EXITS 0 is red (GOH.XEXIT).

In the house TUI contract `✗` means failure and `⚠` means advisory. A private Swift app's
census_bench void hold and three render selftests printed a `✗` line and exited 0, so the gate
called them green (fixed there in a private Swift app 06ee510e). The class is a step whose words and
exit code disagree; every gate step passes through goh_step or local_ci.sh, so both refuse it,
through ONE predicate (lib/xexit.py). A `✗` QUOTED mid-line (a calibration plant's row) is not a
status line.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import xexit


def _goh_step(tmp_path: Path, cmd: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        cd '{tmp_path}'
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init planted
        goh_step "planted step" /bin/bash -c {cmd!r}
        goh_done
    """)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _local_ci(tmp_path: Path, cmd: str) -> subprocess.CompletedProcess:
    d = tmp_path / "proj"
    d.mkdir()
    write_gatesrc(d, "")
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "local_ci.sh"), "--steps-only", "--step", cmd],
        cwd=d,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_predicate_names_status_lines_only():
    esc = chr(27)  # a colour escape the step printed, built so no literal sits in the source
    log = f"ok\n  {esc}[31m✗{esc}[0m void hold\nrow: ✗ quoted\n⚠ advisory\n✗ second\n"
    assert xexit.failure_lines(log) == ["✗ void hold", "✗ second"]
    assert xexit.failure_lines("⚠ advisory only\n✓ fine\n") == []


def test_goh_step_refuses_cross_at_exit_zero(tmp_path):
    r = _goh_step(tmp_path, "echo '✗ void hold 4411'; exit 0")
    assert r.returncode != 0, f"a ✗ at exit 0 passed:\n{r.stdout}{r.stderr}"
    assert "void hold 4411" in r.stderr and "exited 0" in r.stderr


def test_goh_step_passes_advisory_and_quoted_cross(tmp_path):
    r = _goh_step(tmp_path, "echo '⚠ advisory'; echo 'row: ✗ quoted'; exit 0")
    assert r.returncode == 0, r.stderr


def test_local_ci_refuses_cross_at_exit_zero(tmp_path):
    r = _local_ci(tmp_path, "echo '✗ render selftest 9902'; true")
    assert r.returncode != 0, f"a ✗ at exit 0 passed:\n{r.stdout}{r.stderr}"
    assert "render selftest 9902" in r.stdout + r.stderr


def test_local_ci_passes_advisory(tmp_path):
    r = _local_ci(tmp_path, "echo '⚠ advisory'; true")
    assert r.returncode == 0, r.stdout + r.stderr


def test_cr_progress_line_is_judged_by_its_last_segment():
    # a terminal shows the text after the last `\r`; a progress line ending in a `✗` is a status line
    assert xexit.failure_lines("...\r✗ x") == ["✗ x"]


def test_python_and_rust_twins_agree_on_the_shared_fixture():
    # ONE fixture, read here and by step_report.rs `xexit_tests` (GOH.XEXIT2): the twins split alike
    import json

    cases = json.loads((REPO_ROOT / "tests/fixtures/xexit/cases.json").read_text(encoding="utf-8"))
    assert cases
    for case in cases:
        assert xexit.failure_lines(case["text"]) == case["crosses"], case["name"]


# GOH.XEXITDETAIL: the refusal shows the detail a ✗ header introduces, and where the whole log is.
# A private Swift app's push (2026-11-16) showed '✗ compiler warnings in our sources (dependency
# warnings are exempt):' with its list cut away, which hid that the file it named was a selftest's
# planted fixture; a lane spent a cycle hunting a compiler warning that did not exist.
# The detail is built at run time, so the die line's echo of the command cannot carry it.
_HEADER_STEP = (
    "echo 'building'; "
    "echo '✗ compiler warnings in our sources (dependency warnings are exempt):'; "
    "printf '    Sources/%s.swift:3: warning: var never mutated\\n' Planted; "
    "printf '    Sources/%s.swift:9: warning: unused result\\n' Planted; "
    "echo 'done'; exit 0"
)


def test_failure_blocks_keep_the_indented_detail_under_each_cross():
    log = (
        "ok\n✗ warnings:\n    a.swift:1 w\n      nested\n\n  ✗ second\n    b.swift:2 w\n  tail\n"
        "✗ bare\n"
    )
    assert xexit.failure_blocks(log) == [
        "✗ warnings:",
        "    a.swift:1 w",
        "      nested",
        "✗ second",
        "  b.swift:2 w",
        "✗ bare",
    ]
    # the predicate's verdict is unchanged: the same three ✗ lines
    assert len(xexit.failure_lines(log)) == 3


def test_failure_blocks_bound_the_detail_per_cross():
    log = "✗ many:\n" + "".join(f"  item {k}\n" for k in range(30))
    out = xexit.failure_blocks(log, per=20)
    assert out[:2] == ["✗ many:", "  item 0"]
    assert "  item 19" in out and "  item 20" not in out
    assert out[-1].strip().startswith("→ 10 more")


def test_goh_step_refusal_shows_detail_and_a_kept_log(tmp_path):
    r = _goh_step(tmp_path, _HEADER_STEP)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "Sources/Planted.swift:3: warning" in r.stderr, r.stderr
    assert "Sources/Planted.swift:9: warning" in r.stderr, r.stderr
    kept = [
        ln.split("full log: ", 1)[1].strip() for ln in r.stderr.splitlines() if "full log: " in ln
    ]
    assert kept, f"no full-log path in the refusal:\n{r.stderr}"
    path = Path(kept[0])
    try:
        assert "Planted.swift:9" in path.read_text(encoding="utf-8"), (
            "the named log is not the step's"
        )
    finally:
        path.unlink(missing_ok=True)


def test_say_without_keep_names_the_log_it_read(tmp_path):
    log = tmp_path / "step.log"
    log.write_text("✗ header:\n  detail 7731\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib/xexit.py"), "--say", "[1/1]", str(log)],
        capture_output=True,
        text=True,
        timeout=patience(30),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 1
    assert "detail 7731" in r.stderr and f"full log: {log}" in r.stderr, r.stderr
