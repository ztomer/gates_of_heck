"""checks/check_saved_pid_kill.py, calibrated: the tree is clean, each bare shape planted is red.

GOH.STRAYSIGNAL: 46 sites signalled a pid read from a file or kept in a variable; the shapes below
are theirs (the 2026-11-20 incident's `finally: os.kill(int(pidfile.read_text()), SIGKILL)` first).
Each is planted through the check's `plant` seam -- a file read as if tracked -- and must be red;
each owned shape beside it must not be. The allowlist's four ratchet properties are pinned too.
"""

from __future__ import annotations

import sys

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_saved_pid_kill as gate

CHECK = REPO_ROOT / "checks" / "check_saved_pid_kill.py"


def _findings(plant: dict[str, str], allow: dict | None = None) -> list[str]:
    """The check over the planted files alone (the whole tree is read once, below)."""
    return gate.check(REPO_ROOT, plant, allow or {"entries": []}, tracked=False)[0]


def test_this_tree_is_clean_and_its_listed_sites_are_exactly_the_bare_ones(
    monkeypatch, capsys
) -> None:
    """The tree is read once (2.3 s) for both: the command's verdict, and the calibration against
    the tree itself with an empty allowlist, no fewer (blind) and no more (a false positive)."""
    sites, read = gate.scan(REPO_ROOT)
    monkeypatch.setattr(gate, "scan", lambda *_a, **_k: (sites, read))
    code, out = gate.main([str(REPO_ROOT)]), capsys.readouterr()
    assert code == 0, out.out + out.err
    assert "owned" in out.out and "listed in checks/saved_pid_kill_allow.json" in out.out
    listed, _ = gate.load_allow(REPO_ROOT)
    findings = gate.check(REPO_ROOT, {}, {"entries": []})[0]
    named = {f.split(": a signal to a saved pid")[0] for f in findings}
    bare = {s for s in sites if not s.owned}
    assert named == {str(s) for s in bare}
    assert {(s.path, s.call) for s in bare} == set(listed)


BARE_PY = {
    "the incident's shape": "import os\nos.kill(int(pidfile.read_text()), 9)\n",
    "a pid kept in a list": "import os\nfor pid in pids:\n    os.kill(pid, 9)\n",
    "a saved group": "import os\nos.killpg(gate_pid, 9)\n",
    "kill imported from os": "from os import kill\nkill(pid, 15)\n",
    "a stop/continue": "import os, signal\nos.kill(pid, signal.SIGSTOP)\n",
    "a probe of a saved pid": "import os\nos.kill(pid, 0)\n",
    "a probe of a pidfile": "import os\nos.kill(int(mark.read_text()), 0)\n",
    "a probe of a saved group": "import os\nos.killpg(pgid, 0)\n",
}
OWNED_PY = {
    "a probe of itself": "import os\nos.kill(os.getpid(), 0)\n",
    "a probe of a child handle": "import os\nos.kill(proc.pid, 0)\n",
    "the liveness helper": "import pid_owner\npid_owner.alive(s)\npid_owner.alive_line(t)\n",
    "a child handle": "import os\nos.killpg(proc.pid, 9)\nos.kill(self.proc.pid, 15)\n",
    "itself": "import os\nos.kill(os.getpid(), 15)\nos.killpg(os.getpgid(0), 1)\nos.killpg(0, 1)\n",
    "a child's group": "import os\nos.killpg(os.getpgid(proc.pid), 9)\n",
    "the helper": "import pid_owner\npid_owner.signal(pid_owner.read_stamp(f), 9)\n",
    "a handle method": "proc.kill()\nproc.send_signal(15)\n",
}


@pytest.mark.parametrize("shape", sorted(BARE_PY))
def test_a_bare_python_kill_is_red(shape: str) -> None:
    findings = _findings({"tests/test_planted.py": BARE_PY[shape]})
    assert any(f.startswith("tests/test_planted.py:") for f in findings), findings


@pytest.mark.parametrize("shape", sorted(OWNED_PY))
def test_an_owned_python_kill_is_not(shape: str) -> None:
    assert _findings({"lib/planted.py": OWNED_PY[shape]}) == []


BARE_SH = {
    "a saved pid": 'kill "$pid" 2>/dev/null || true\n',
    "with a signal": 'kill -TERM "$follower"\n',
    "a group": 'kill -9 -- -"$pgid"\n',
    "from a file": 'kill "$(cat "$pidfile")"\n',
    "after a test": '[ -n "$p" ] && kill -9 "$p"\n',
    "in a trap": "trap 'kill -TERM \"$wrap\"; exit 143' TERM\n",
    "by path": '/bin/kill "$pid"\n',
    "a probe of a saved pid": 'kill -0 "$pid" 2>/dev/null\n',
    "a probe in a wait loop": 'while kill -0 "$p" 2>/dev/null; do sleep 1; done\n',
    "a probe spelled -s 0": 'kill -s 0 "$holder"\n',
}
OWNED_SH = {
    "a probe of itself": "kill -0 $$\n",
    "the liveness helper": 'goh_pid_alive "$stamp" && echo up\n',
    "a listing": 'kill -l "$rc"\n',
    "itself": 'kill -TERM $$\nkill -KILL "$$"\n',
    "a job": "kill %1\n",
    "the helper": 'goh_pid_kill TERM "$stamp"\n',
    "a word that is not the command": "pkill_guard\nkill_tree x\necho kill_me\n",
    "a comment": '# kill "$pid" here\n',
}


@pytest.mark.parametrize("shape", sorted(BARE_SH))
def test_a_bare_shell_kill_is_red(shape: str) -> None:
    findings = _findings({"tools/planted.sh": "#!/usr/bin/env bash\n" + BARE_SH[shape]})
    assert any(f.startswith("tools/planted.sh:2:") for f in findings), findings


@pytest.mark.parametrize("shape", sorted(OWNED_SH))
def test_an_owned_shell_kill_is_not(shape: str) -> None:
    assert _findings({"tools/planted.sh": "#!/usr/bin/env bash\n" + OWNED_SH[shape]}) == []


def test_a_shebang_script_without_an_extension_is_read() -> None:
    findings = _findings({"hooks/planted": '#!/bin/sh\nkill "$pid"\n'})
    assert any(f.startswith("hooks/planted:2:") for f in findings), findings


def test_a_rust_kill_and_its_probe_are_red() -> None:
    body = (
        "fn f(pid: i32) {\n    unsafe { libc::kill(pid, libc::SIGKILL) };\n"
        "    let _ = goh_sys::killpg(pid, 0);\n}\n"
    )
    findings = _findings({"crates/goh/src/planted.rs": body})
    assert [f.split(": a signal")[0] for f in findings] == [
        "crates/goh/src/planted.rs:2: libc::kill(pid, libc::SIGKILL)",
        "crates/goh/src/planted.rs:3: goh_sys::killpg(pid, 0)",
    ], findings


def test_the_helpers_themselves_are_not_judged() -> None:
    assert (
        _findings(
            {
                "lib/pid_owner.sh": 'kill "-$sig" "$pid"\n',
                "lib/pid_owner.py": "import os\nos.kill(n, s)\n",
            }
        )
        == []
    )


def _allow(*entries: dict) -> dict:
    return {"entries": list(entries)}


def test_a_listed_bare_site_passes_and_a_stale_entry_is_red() -> None:
    plant = {"tools/planted.sh": '#!/bin/sh\nkill "$pid"\n'}
    entry = {
        "path": "tools/planted.sh",
        "call": 'kill   "$pid"',
        "status": "bare",
        "reason": "debt",
    }
    assert _findings(plant, _allow(entry)) == []
    stale = _findings({"tools/planted.sh": "#!/bin/sh\n"}, _allow(entry))
    assert any("stale bare entry" in f and "tools/planted.sh" in f for f in stale), stale


def test_one_entry_does_not_excuse_another_call_in_its_file() -> None:
    plant = {"tools/planted.sh": '#!/bin/sh\nkill "$pid"\nkill "$other"\n'}
    entry = {"path": "tools/planted.sh", "call": 'kill "$pid"', "status": "bare", "reason": "debt"}
    findings = _findings(plant, _allow(entry))
    assert [f.split(": a signal")[0] for f in findings] == ['tools/planted.sh:3: kill "$other"'], (
        findings
    )


@pytest.mark.parametrize(
    "entry, said",
    [
        (
            {"path": "x.sh", "call": "kill $p", "status": "bare"},
            "needs a path, a call and a reason",
        ),
        (
            {"path": "x.sh", "call": "kill $p", "status": "fine", "reason": "r"},
            "status must be one of",
        ),
    ],
)
def test_an_entry_without_a_reason_or_with_an_unknown_status_is_red(entry: dict, said: str) -> None:
    assert any(said in f for f in _findings({"lib/x.py": ""}, _allow(entry))), said


def test_nothing_read_is_not_clean(tmp_path) -> None:
    fast_init(tmp_path)
    findings, _sites, read, _debt = gate.check(tmp_path)
    assert read == 0 and any("refusing to report clean over nothing" in f for f in findings)
