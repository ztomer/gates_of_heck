"""A missing sccache server is started on the resolver's root by the next compile, never on the
default by a plain client (GOH.SCCACHEROOTFOLLOW). Killed on 2026-11-23 and 24, the server came back
on the internal disk both times. A planted port, volume, sccache and starter; the machine's server,
drive and cache are never reached."""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
from pathlib import Path

from test_cache_tier import UUID, _volume
from test_sccache_repoint import FAKE

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT / "lib" / "goh-rustc"
BASH = "/bin/bash"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _plant(tmp: Path, mounted: bool = True) -> tuple[dict[str, str], Path, Path]:
    """(env, fake server's state, the planted drive's sccache dir): a fake sccache that is down, and
    a starter running the real `start` over a planted volume."""
    bin_ = tmp / "bin"
    bin_.mkdir()
    state = tmp / "state.json"
    state.write_text(json.dumps({"running": False, "dir": None, "requests": 0,
                                 "requests_seq": [], "calls": []}))  # fmt: skip
    fake = bin_ / "sccache"
    # a compile with the server down starts one on the client's own SCCACHE_DIR, as sccache does
    fake.write_text(
        FAKE.replace(
            'if arg == "--show-stats":',
            'if arg == "rustc" and not s["running"]:\n'
            '    s["running"], s["dir"] = True, os.environ.get("SCCACHE_DIR")\n'
            'elif arg == "--show-stats":',
        )
    )
    fake.chmod(0o755)
    _volume(tmp, "FAKE1TB")
    starter = tmp / "starter.py"
    starter.write_text(f"""#!{sys.executable}
import os, sys
sys.path.insert(0, {str(ROOT / "lib")!r})
import cache_tier, sccache_repoint as sr
mount = {str(tmp / "FAKE1TB")!r}
cache_tier.open_root = lambda v: os.open(v.root, os.O_RDONLY | os.O_DIRECTORY)
v = cache_tier.resolve("/int", {{}}, cache_tier.System(
    mounts=lambda: [mount] if {mounted} else [], ismount=lambda p: True,
    volume_uuid=lambda p: {UUID!r}, root_dev=lambda: -1,
    statvfs=lambda p: os.statvfs_result((4096, 4096, 1 << 28, 1 << 27, 1 << 27, 0, 0, 0, 0, 255))))
sr.say(sr.start(sr.Env(), v, sys.argv[1]))
""")
    starter.chmod(0o755)
    env = {"PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(tmp), "FAKE_STATE": str(state),
           "GOH_SCCACHE_PORT": str(_free_port()), "GOH_SCCACHE_STARTER": str(starter),
           "GOH_SCCACHE_LOG": str(tmp / "start.log")}  # fmt: skip
    return env, state, tmp / "FAKE1TB" / "cache.noindex" / "sccache"


def _compile(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "--posix", str(SHIM), "rustc", "--crate-name", "x"], env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip


def _state(p: Path) -> dict:
    return json.loads(p.read_text())


def test_a_killed_server_is_restarted_on_the_drive_by_the_next_compile(tmp_path: Path) -> None:
    env, state, ext = _plant(tmp_path)
    internal = f"{tmp_path}/Library/Caches/Mozilla.sccache"
    env.update(SCCACHE_DIR=internal, GOH_SCCACHE_PINNED=internal)  # a gate's pin
    r = _compile(env)
    assert r.returncode == 0, r.stderr
    assert _state(state)["dir"] == str(ext)
    assert r.stderr == "", "cargo replays a wrapper's stderr as the crate's own"
    assert "external" in (tmp_path / "start.log").read_text()


def test_no_drive_starts_on_the_pinned_internal_dir_and_the_log_says_why(tmp_path: Path) -> None:
    env, state, _ext = _plant(tmp_path, mounted=False)
    pin = str(tmp_path / "real-home-cache")
    env.update(SCCACHE_DIR=pin, GOH_SCCACHE_PINNED=pin)
    assert _compile(env).returncode == 0
    assert _state(state)["dir"] == pin
    assert "no mounted volume" in (tmp_path / "start.log").read_text()


def _not_started(env: dict[str, str], state: Path) -> bool:
    assert _compile(env).returncode == 0
    return not [c for c in _state(state)["calls"] if c[0] == "--start-server"]


def test_a_dir_or_port_of_the_callers_own_is_left_alone(tmp_path: Path) -> None:
    env, state, _ = _plant(tmp_path)
    assert _not_started({**env, "SCCACHE_DIR": str(tmp_path / "chosen")}, state)
    assert _not_started({**env, "SCCACHE_SERVER_PORT": "4999"}, state)
    assert _not_started({**env, "SCCACHE_SERVER_UDS": "/x"}, state)
    assert _not_started({**env, "GOH_SCCACHE_UP": "off"}, state)
    assert not (tmp_path / "start.log").exists()


def test_a_server_that_answers_costs_no_starter(tmp_path: Path) -> None:
    env, state, _ = _plant(tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        env["GOH_SCCACHE_PORT"] = str(s.getsockname()[1])
        assert _not_started(env, state)


def test_a_broken_starter_never_fails_the_compile(tmp_path: Path) -> None:
    env, _state_p, _ = _plant(tmp_path)
    env["GOH_SCCACHE_STARTER"] = "/nonexistent/starter"
    r = _compile(env)
    assert r.returncode == 0 and r.stderr == ""


def test_a_restart_never_reds_a_build_in_flight(tmp_path: Path) -> None:
    """The sweep's deadline may restart a busy server: the client then compiles locally."""
    env, _s, _ = _plant(tmp_path)
    nxt = tmp_path / "sccache"
    nxt.write_text('#!/bin/sh\necho "io=$SCCACHE_IGNORE_SERVER_IO_ERROR"\n')
    nxt.chmod(0o755)
    env.update(GOH_RUSTC_NEXT=str(nxt), GOH_SCCACHE_UP="off")
    assert _compile(env).stdout == "io=1\n"


def test_the_gates_entry_starts_a_missing_server_and_pins_the_dir(tmp_path: Path) -> None:
    env, state, ext = _plant(tmp_path)
    script = f"set -euo pipefail; . {shlex.quote(str(ROOT / 'lib/sccache_dir.sh'))}; "
    script += 'echo "$SCCACHE_DIR $GOH_SCCACHE_PINNED"'
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert _state(state)["dir"] == str(ext)
    a, b = r.stdout.split()
    assert a == b and a.startswith(str(tmp_path))
