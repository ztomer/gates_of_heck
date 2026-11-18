"""tools/spawn_profile.py: a ledger row per process a command starts, by kind and caller.

GOH.SPAWNPROFILE.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))

import spawn_profile

TOOL = REPO_ROOT / "tools" / "spawn_profile.py"
needs_cc = pytest.mark.skipif(shutil.which("cc") is None, reason="the shims are compiled with cc")


def test_kinds_and_scripts_are_named_from_argv() -> None:
    assert spawn_profile.classify("/opt/x/Python.app/Contents/MacOS/Python") == "python"
    assert spawn_profile.classify("/bin/sh") == "bash"
    assert spawn_profile.classify("git-remote-https") == "git"
    assert spawn_profile.classify("/usr/bin/true") == "other"
    assert spawn_profile.classify("rustc") == "rustc"  # cargo's child: never shimmed, never "blind"
    row = {"kind": "python3", "argv": "-I -S /x/gates/lib/gitrun.py status"}
    assert spawn_profile.script_of(row) == "lib/gitrun.py"
    assert spawn_profile.script_of({"kind": "python3", "argv": "-m pytest -q"}) == "-m pytest"
    assert (
        spawn_profile.script_of({"kind": "git", "argv": "-C /r rev-parse HEAD"}) == "git rev-parse"
    )


@needs_cc
@pytest.mark.load_sensitive(
    reason="the blind bash lives 0.6 s; a ps sampler at 0.1 s starved by load can miss it"
)
def test_a_run_records_each_process_under_the_script_that_started_it(tmp_path: Path) -> None:
    """bash -> outer.py -> (sys.executable -c, git) and an absolute /bin/bash the shims cannot see:
    every PATH or sys.executable start is one row with its caller script; the absolute one is in the
    sampled blind share, never silently counted as seen."""
    outer = tmp_path / "outer.py"
    outer.write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "subprocess.run(['git', '--version'], check=True, stdout=subprocess.DEVNULL)\n"
        "subprocess.run(['/bin/bash', '-c', 'sleep 0.6; :'], check=True)\n",
        encoding="utf-8",
    )
    out = tmp_path / "prof"
    done = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "run",
            "--out",
            str(out),
            "--sample",
            "0.1",
            "--",
            "bash",
            "-c",
            f"python3 {outer}; exit 3",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert done.returncode == 3, done.stderr  # the command's own exit, through the shims
    rows = spawn_profile.read_ledger(out / "ledger.tsv")
    assert sorted(r["kind"] for r in rows) == ["bash", "git", "python3", "python3"]
    cal = {"python": (0.03, 0.02), "bash": (0.015, 0.01), "git": (0.015, 0.007)}
    summary = spawn_profile.summarize(out, cal)
    pairs = {(g["caller"], g["callee"]): g for g in summary["top"]}
    assert pairs[(outer.parent.name + "/outer.py", "python3 -c")]["n"] == 1
    assert (outer.parent.name + "/outer.py", "git") in pairs
    assert ("(root)", "bash -c") in pairs
    assert summary["start_cpu_s"] == pytest.approx(2 * 0.02 + 0.01 + 0.007)
    assert summary["run"]["rc"] == 3
    blind = summary["blind"]
    assert blind["by_kind"]["bash"]["blind_pids"] >= 1, blind  # /bin/bash by absolute path
    assert 0 < blind["blind_time_share"] < 1


@needs_cc
def test_the_shim_passes_a_signal_death_through(tmp_path: Path) -> None:
    """A child killed by a signal leaves the shim dead by the same signal, as its caller sees."""
    shims = spawn_profile.build_shims(tmp_path, {"bash": shutil.which("bash") or "/bin/bash"})
    done = subprocess.run(
        [str(shims["bash"]), "-c", "kill -TERM $$"], stdin=subprocess.DEVNULL, check=False
    )
    assert done.returncode == -15
    (row,) = spawn_profile.read_ledger(tmp_path / "ledger.tsv")
    assert row["kind"] == "bash" and row["status"] == 15


def test_report_prints_the_priced_table(tmp_path: Path) -> None:
    (tmp_path / "ledger.tsv").write_text(
        "bash\t10\t11\t1\t0\t1.0\t0.1\t0.1\t0\t/r/gates/a.sh x\n"
        "python3\t12\t13\t11\t0\t0.1\t0.02\t0.01\t0\t/r/lib/b.py\n"
        "python3\t14\t15\t11\t0\t0.1\t0.02\t0.01\t0\t/r/lib/b.py\n",
        encoding="utf-8",
    )
    (tmp_path / "run.json").write_text(
        json.dumps({"wall": 2.0, "cpu": 1.0, "rc": 0}), encoding="utf-8"
    )
    cal = tmp_path / "calibration.json"
    cal.write_text(
        json.dumps({"kinds": {"python": {"direct_ms": 30, "direct_cpu_ms": 20}}}), encoding="utf-8"
    )
    done = subprocess.run(
        [sys.executable, str(TOOL), "report", str(tmp_path), "--costs", str(cal), "--json"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    summary = json.loads(done.stdout)
    top = summary["top"][0]
    assert (top["caller"], top["callee"], top["n"]) == ("gates/a.sh", "lib/b.py", 2)
    assert summary["start_cpu_s"] == pytest.approx(0.04)
    assert summary["start_share_cpu"] == pytest.approx(0.04)
    assert summary["by_kind"] == {"python": 2, "bash": 1}


def test_the_blind_share_counts_only_the_kinds_a_shim_was_aimed_at(tmp_path: Path) -> None:
    """A sampled bash the ledger has (as shim or child) is seen; one it lacks is blind; a `tee` was
    never shimmed, so it is listed apart and moves no share."""
    (tmp_path / "ledger.tsv").write_text(
        "bash\t10\t11\t1\t0\t1.0\t0.1\t0.1\t0\tx.sh\n", encoding="utf-8"
    )
    (tmp_path / "samples.tsv").write_text(
        "1\t10\tbash\n1\t11\t/opt/bin/bash\n1\t12\t/bin/bash\n2\t12\t/bin/bash\n1\t13\ttee\n",
        encoding="utf-8",
    )
    blind = spawn_profile.summarize(tmp_path)["blind"]
    assert blind["by_kind"] == {"bash": {"sampled_pids": 3, "blind_pids": 1, "time_share": 0.5}}
    assert blind["unshimmed_comm"] == [["tee", 1]] or blind["unshimmed_comm"] == [("tee", 1)]
