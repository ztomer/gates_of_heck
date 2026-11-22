"""sccache follows the resolver's root when the removable drive comes or goes (GOH.CACHEEVICT).

A fake `sccache` (a script over a JSON state file) stands in for the server: --show-stats starts it
on SCCACHE_DIR when it is not running, as the real client does; --stop-server and --start-server
record what they saw. A fake clock and sleep drive the idle wait. Nothing touches the live server.
"""

from __future__ import annotations

import json
import os
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cache_tier
import sccache_repoint as sr

FAKE = r"""#!/usr/bin/env python3
import json, os, sys
p = os.environ["FAKE_STATE"]
s = json.load(open(p))
arg = sys.argv[1]
s["calls"].append([arg, os.environ.get("SCCACHE_DIR"), os.getcwd(),
                   "SCCACHE_BASEDIRS" in os.environ, "SCCACHE_CACHE_SIZE" in os.environ])
if arg == "--show-stats":
    if not s["running"]:
        s["running"], s["dir"] = True, os.environ.get("SCCACHE_DIR")
    if s["requests_seq"]:
        s["requests"] = s["requests_seq"].pop(0)
    print(json.dumps({"stats": {"compile_requests": s["requests"]},
                      "cache_location": 'Local disk: "%s"' % s["dir"]}))
elif arg == "--stop-server":
    s["running"] = False
elif arg == "--start-server":
    s["running"], s["dir"] = True, os.environ.get("SCCACHE_DIR")
json.dump(s, open(p, "w"))
"""


def _fake(tmp_path, running_dir, seq=()):
    exe = tmp_path / "sccache"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    state = tmp_path / "state.json"
    init = {"running": running_dir is not None, "dir": running_dir, "requests": 100,
            "requests_seq": list(seq), "calls": []}  # fmt: skip
    state.write_text(json.dumps(init))
    t = [0.0]
    env = sr.Env(sccache=str(exe), clock=lambda: t[0],
                 sleep=lambda s: t.__setitem__(0, t[0] + s), rustc_alive=lambda: False,
                 base={"PATH": os.environ["PATH"], "FAKE_STATE": str(state),
                       "SCCACHE_BASEDIRS": "/x", "SCCACHE_CACHE_SIZE": "1G"})  # fmt: skip
    return env, state, t


def _calls(state):
    return json.loads(state.read_text())["calls"]


def test_drive_arrives_the_server_moves_to_it_from_cwd_root(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, _t = _fake(tmp_path, internal)
    assert sr.repoint(env, ext, idle_s=30, timeout_s=600) == "repointed"
    calls = _calls(state)
    start = [c for c in calls if c[0] == "--start-server"]
    assert start == [["--start-server", ext, "/", False, False]]
    assert [c[0] for c in calls].index("--stop-server") < [c[0] for c in calls].index(
        "--start-server")  # fmt: skip
    assert json.loads(state.read_text())["dir"] == ext


def test_drive_leaves_the_server_comes_home(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, _t = _fake(tmp_path, ext)
    assert sr.repoint(env, internal, idle_s=30, timeout_s=600) == "repointed"
    assert json.loads(state.read_text())["dir"] == internal


def test_already_there_is_left_alone(tmp_path):
    internal = str(tmp_path / "int")
    env, state, _t = _fake(tmp_path, internal)
    assert sr.repoint(env, internal) == "already"
    assert all(c[0] == "--show-stats" for c in _calls(state))


def test_a_busy_server_is_never_restarted(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, _t = _fake(tmp_path, internal, seq=range(101, 10_000))
    assert sr.repoint(env, ext, idle_s=30, timeout_s=120) == "busy"
    assert not [c for c in _calls(state) if c[0] in ("--stop-server", "--start-server")]


def test_it_waits_for_quiet_before_the_restart(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, _state, t = _fake(tmp_path, internal, seq=[101, 102, 103])
    assert sr.repoint(env, ext, idle_s=30, timeout_s=600) == "repointed"
    assert t[0] >= 5 + 30  # the last change lands at the first poll (5 s), then 30 s of quiet


def test_a_running_rustc_holds_the_restart(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, _state, _t = _fake(tmp_path, internal)
    env.rustc_alive = lambda: True
    assert sr.repoint(env, ext, idle_s=30, timeout_s=100) == "busy"


def test_want_dir_follows_the_verdict_and_never_makes_the_root(tmp_path):
    gone = cache_tier.Verdict(str(tmp_path / "int"), False, "absent")
    assert sr.want_dir(gone, {"HOME": str(tmp_path)}).startswith(str(tmp_path))
    assert not (tmp_path / "int").exists()
    root = tmp_path / "drive" / "cache.noindex"
    root.mkdir(parents=True)
    here = cache_tier.Verdict(str(root), True, "test")
    real = cache_tier.open_root
    try:
        cache_tier.open_root = lambda v: os.open(v.root, os.O_RDONLY | os.O_DIRECTORY)
        assert sr.want_dir(here) == str(root / "sccache") and (root / "sccache").is_dir()
    finally:
        cache_tier.open_root = real


def test_location_parses_the_0_19_form():
    assert sr.location({"cache_location": 'Local disk: "/a/b/"'}) == "/a/b"
    assert sr.location({"cache_location": "S3, bucket: x"}) is None
    assert sr.location(None) is None


# GOH.CACHEEVICT2 (1): the sweep follows the drive with no coordinator call. `follow` re-points on
# a CHANGE of the resolver's answer since the last sweep (the drive came or went), and on a server
# serving a root that is neither the internal one nor the verified drive; a mismatch that was
# already there at the last sweep is somebody's choice (a measurement window: a restart resets the
# stats) and is reported, never acted on. A busy server is not recorded, so the next sweep retries.


def _alive(state):
    return lambda: json.loads(state.read_text())["running"]


def test_follow_first_sweep_only_records(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, _t = _fake(tmp_path, internal)
    env.alive = _alive(state)
    status, record = sr.follow(env, ext, None, internal)
    assert (status, record) == ("recorded", ext)
    assert not [c for c in _calls(state) if c[0] in ("--stop-server", "--start-server")]


def test_follow_a_standing_mismatch_is_left_alone(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, _t = _fake(tmp_path, internal)
    env.alive = _alive(state)
    status, record = sr.follow(env, ext, ext, internal)
    assert status.startswith("differs") and record == ext
    assert json.loads(state.read_text())["dir"] == internal


def test_follow_no_server_is_never_started(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, _t = _fake(tmp_path, None)
    env.alive = _alive(state)
    assert sr.follow(env, ext, internal, internal) == ("no server", internal)
    assert _calls(state) == []


def test_follow_a_busy_server_is_retried_next_sweep(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, _t = _fake(tmp_path, internal, seq=range(101, 10_000))
    env.alive = _alive(state)
    assert sr.follow(env, ext, internal, internal, idle_s=30, timeout_s=60) == ("busy", internal)


def test_follow_a_server_on_a_lost_root_comes_home_without_a_change(tmp_path):
    internal, gone = str(tmp_path / "int"), str(tmp_path / "old-drive" / "sccache")
    env, state, _t = _fake(tmp_path, gone)
    env.alive = _alive(state)
    assert sr.follow(env, internal, internal, internal, idle_s=30) == ("repointed", internal)
    assert json.loads(state.read_text())["dir"] == internal
