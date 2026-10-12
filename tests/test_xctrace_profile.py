"""tools/profiling: a Time Profiler trace from today's xctrace is read into a ranked report.

Red proof: `analyze_trace.sh` exported `//profile-data`, a table xctrace no longer writes, and
guessed at two schemas the export does not use, so every modern trace reported "could not extract
symbol table". The export it now asks for is the `time-profile` table, whose rows name a thread,
process, weight, backtrace, frame or binary ONCE by `id` and point back at it by `ref` thereafter.
The fixture is that shape cut down: the second row is all refs, the third shares a frame by ref,
and a second process must be left out.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools" / "profiling"))
import xctrace_profile

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "xctrace" / "time_profile.xml"
MS = 1_000_000


def test_rows_given_by_ref_count_like_rows_given_in_full() -> None:
    s = xctrace_profile.summarise(FIXTURE, process="App")
    assert s.total == 4 * MS
    assert dict(s.self_time) == {"decode": 2 * MS, "render": 2 * MS}
    assert dict(s.inclusive) == {"main": 4 * MS, "read": 2 * MS, "decode": 2 * MS, "render": 2 * MS}
    assert dict(s.threads) == {"Main Thread": 2 * MS, "worker": 2 * MS}
    assert dict(s.binaries) == {"ImageIO": 2 * MS, "App": 2 * MS}


def test_another_process_is_left_out_and_no_filter_reads_them_all() -> None:
    assert xctrace_profile.summarise(FIXTURE, process="Other").total == 5 * MS
    assert xctrace_profile.summarise(FIXTURE).total == 9 * MS


def test_under_keeps_the_stacks_through_a_frame_and_draws_the_tree_below_it() -> None:
    s = xctrace_profile.summarise(FIXTURE, process="App", under="read", tree=2)
    assert s.total == 2 * MS
    assert dict(s.tree) == {("decode",): 2 * MS}


def test_analyze_trace_exports_the_time_profile_table_and_ranks_it(tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    asked = tmp_path / "asked"
    # a stand-in for `xcrun xctrace`: records what it was asked, answers with the fixture
    (fake / "xcrun").write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{asked}"\n'
        'while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done\n'
        f'cp "{FIXTURE}" "$out"\n'
    )
    (fake / "xcrun").chmod(0o755)
    trace = tmp_path / "run.trace"
    trace.mkdir()
    report = tmp_path / "report.txt"
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    r = subprocess.run(
        [str(REPO_ROOT / "tools/profiling/analyze_trace.sh"), str(trace), str(report), "--process", "App"],
        check=False, capture_output=True, text=True, env=env, timeout=60,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert '@schema="time-profile"' in asked.read_text()
    text = report.read_text()
    assert "total" in text and "4.0 ms" in text, text
    assert "decode" in text and "Other" not in text, text
