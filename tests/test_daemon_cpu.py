"""tools/daemon_cpu.py: daemon CPU over a window, and the writers it served (EST.FSEVENTSCHURN)."""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))

import daemon_cpu

TOOL = REPO_ROOT / "tools" / "daemon_cpu.py"


def test_ps_cputime_forms_parse() -> None:
    assert daemon_cpu.parse_cputime("0:01.50") == 1.5
    assert daemon_cpu.parse_cputime("218:26.32") == 218 * 60 + 26.32
    assert daemon_cpu.parse_cputime("1:02:03") == 3723
    assert daemon_cpu.parse_cputime("2-00:00:01") == 2 * 86400 + 1


def _ps(rows: list[tuple[int, int, str, str]]):
    return lambda: "".join(f"{pid} {ppid} {cpu} {comm}\n" for pid, ppid, cpu, comm in rows)


def test_a_window_bills_only_the_cpu_burned_inside_it() -> None:
    """A daemon up for days has hours of CPU: only what it burned in the window counts, a process
    born inside the window counts whole, and a writer is credited to the command when it descends
    from the command's pid."""
    first = daemon_cpu.snapshot(
        _ps(
            [
                (55, 1, "218:00.00", "/System/.../fseventsd"),
                (100, 1, "0:01.00", "/bin/zsh"),
                (200, 100, "0:00.00", "/usr/bin/cargo"),
            ]
        )
    )
    w = daemon_cpu.Window(first)
    w.add(
        daemon_cpu.snapshot(
            _ps(
                [
                    (55, 1, "218:05.00", "/System/.../fseventsd"),
                    (100, 1, "0:01.00", "/bin/zsh"),
                    (200, 100, "0:02.00", "/usr/bin/cargo"),
                    (300, 200, "0:04.00", "/opt/rustc"),
                    (400, 1, "0:03.00", "/opt/rustc"),
                ]
            )
        )
    )
    out = daemon_cpu.report(w, wall=10.0, root=200, top=5)
    assert out["daemons"]["fseventsd"] == {"cpu_s": 5.0, "pct_core": 50.0}
    rows = {r["comm"]: r for r in out["writers"]}
    assert rows["rustc"] == {"comm": "rustc", "cpu_s": 7.0, "in_command_s": 4.0}
    assert rows["cargo"]["in_command_s"] == 2.0
    assert "zsh" not in rows, "a process that burned nothing in the window is no writer"
    assert out["tree_attributed_s"] == 6.0


def test_run_attributes_the_commands_own_cpu(tmp_path) -> None:
    """The live probe end to end: a bounded computation in the command's tree is seen there. It
    idles before exiting: a process's last interval before it dies is never sampled (the
    module's stated limit), and on a loaded machine one `ps` takes a second."""
    out_json = tmp_path / "r.json"
    r = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "run",
            "--interval",
            "0.1",
            "--json",
            str(out_json),
            "--",
            sys.executable,
            "-c",
            "import time; sum(i * i for i in range(20_000_000)); time.sleep(1.5)",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(out_json.read_text())
    assert out["command"]["rc"] == 0
    assert out["command"]["cpu_s"] > 0.3
    assert out["tree_attributed_s"] > 0.5 * out["command"]["cpu_s"], out
    assert set(out["daemons"]) == set(daemon_cpu.DAEMONS)
