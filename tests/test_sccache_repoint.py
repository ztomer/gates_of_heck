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
                   "SCCACHE_BASEDIRS" in os.environ, os.environ.get("SCCACHE_CACHE_SIZE")])
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
    # SCCACHE_DIR in the environment makes sccache build its disk config from the environment,
    # which replaces the config file's [cache.disk] section: without SCCACHE_CACHE_SIZE the cap
    # falls to sccache's 10 GiB default (seen live 2026-11-23). The start carries goh's one cap,
    # never the caller's own value (the base env's "1G").
    assert start == [["--start-server", ext, "/", False, f"{sr.sccache_cap.CAP_GIB}G"]]
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


# GOH.SCCACHEROOTFOLLOW: a sweep applies ANY difference between the root the server serves and the
# resolver's, with no drive change: a server started by a plain client sat on the internal disk for
# hours (2026-11-23 and 24). Idle-gated, with a deadline across sweeps; leaving it is an explicit,
# expiring hold.
def _alive(state):
    return lambda: json.loads(state.read_text())["running"]


def _standing(tmp_path, seq=()):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, t = _fake(tmp_path, internal, seq=seq)
    env.alive = _alive(state)
    return env, state, t, internal, ext


def _serving(state):
    return json.loads(state.read_text())["dir"]


def test_follow_moves_a_standing_difference_once_idle(tmp_path):
    env, state, t, internal, ext = _standing(tmp_path)
    assert sr.follow(env, ext, internal, 1000.0, None, None) == ("repointed", None)
    assert _serving(state) == ext and t[0] >= sr.IDLE_S


def test_follow_no_server_is_never_started(tmp_path):
    internal, ext = str(tmp_path / "int"), str(tmp_path / "drive" / "sccache")
    env, state, _t = _fake(tmp_path, None)
    env.alive = _alive(state)
    assert sr.follow(env, ext, internal, 1000.0, None, None) == ("no server", None)
    assert _calls(state) == []


def test_follow_a_busy_server_is_owed_to_the_next_sweep(tmp_path):
    env, state, _t, internal, ext = _standing(tmp_path, seq=range(101, 10_000))
    assert sr.follow(env, ext, internal, 1000.0, None, None, 30, 60) == ("busy", 1000.0)
    later = 1000.0 + sr.FORCE_AFTER_S - 1
    assert sr.follow(env, ext, internal, later, 1000.0, None, 30, 60) == ("busy", 1000.0)
    assert _serving(state) == internal


def test_follow_past_the_deadline_moves_a_server_that_never_goes_idle(tmp_path):
    env, state, _t, internal, ext = _standing(tmp_path, seq=range(101, 10_000))
    env.rustc_alive = lambda: True
    late = 1000.0 + sr.FORCE_AFTER_S
    status, owed = sr.follow(env, ext, internal, late, 1000.0, None, 30, 60)
    assert status == "repointed" and owed is None
    assert _serving(state) == ext


def test_follow_a_hold_leaves_it_until_it_expires(tmp_path):
    env, state, _t, internal, ext = _standing(tmp_path)
    hold = {"until": 2000.0, "why": "hit-rate window"}
    status, _owed = sr.follow(env, ext, internal, 1999.0, None, hold)
    assert status.startswith("held") and "hit-rate window" in status
    assert _serving(state) == internal
    assert sr.follow(env, ext, internal, 2000.0, None, hold)[0] == "repointed"


def test_follow_a_server_on_a_lost_root_comes_home_despite_a_hold(tmp_path):
    internal, gone = str(tmp_path / "int"), str(tmp_path / "old-drive" / "sccache")
    env, state, _t = _fake(tmp_path, gone)
    env.alive = _alive(state)
    hold = {"until": 2000.0, "why": "w"}
    assert sr.follow(env, internal, internal, 1000.0, None, hold) == ("repointed", None)
    assert _serving(state) == internal


def test_a_restart_a_racing_client_took_back_is_retried(tmp_path):
    """Between the stop and the start a plain client can start a server on the default dir."""
    env, state, _t, _internal, ext = _standing(tmp_path)
    run, raced = env.run, []

    def racing(*args, **kw):
        out = run(*args, **kw)
        if args == ("--start-server",) and not raced:
            raced.append(1)
            s = json.loads(state.read_text())
            s["dir"] = str(tmp_path / "int")
            state.write_text(json.dumps(s))
        return out

    env.run = racing
    assert sr.repoint(env, ext) == "repointed"


# A missing server is started on the resolver's root by whoever needs it first.
def _verdict(tmp_path, monkeypatch):
    """A planted volume judged by the real resolver, and an open_root that takes it."""
    from test_cache_tier import _sys, _volume

    mount = _volume(tmp_path, "FAKE1TB")
    monkeypatch.setattr(
        cache_tier, "open_root", lambda v: os.open(v.root, os.O_RDONLY | os.O_DIRECTORY)
    )
    return mount, lambda: cache_tier.resolve("/int", {}, _sys([mount])), _sys


def test_start_puts_a_missing_server_on_the_planted_drive(tmp_path, monkeypatch):
    mount, resolve, _ = _verdict(tmp_path, monkeypatch)
    (tmp_path / "f").mkdir()
    env, state, _t = _fake(tmp_path / "f", None)
    msg = sr.start(env, resolve(), str(tmp_path / "int"))
    ext = str(mount / "cache.noindex" / "sccache")
    assert _serving(state) == ext and ext in msg and "external" in msg
    starts = [c for c in _calls(state) if c[0] == "--start-server"]
    assert starts == [["--start-server", ext, "/", False, f"{sr.sccache_cap.CAP_GIB}G"]]


def test_start_falls_back_to_internal_and_says_why(tmp_path, monkeypatch):
    mount, resolve, _sys = _verdict(tmp_path, monkeypatch)
    (mount / "cache.noindex" / ".goh-cache-volume").unlink()  # unmarked
    (tmp_path / "f").mkdir()
    env, state, _t = _fake(tmp_path / "f", None)
    internal = str(tmp_path / "int")
    msg = sr.start(env, resolve(), internal)
    assert _serving(state) == internal
    assert "internal" in msg and "marker" in msg
    absent = cache_tier.resolve("/int", {}, _sys([tmp_path / "gone"]))
    env.run("--stop-server")
    assert "no mounted volume" in sr.start(env, absent, internal)
    assert not (tmp_path / "gone").exists()


def test_start_a_drive_that_leaves_between_the_check_and_the_start_is_internal(
    tmp_path, monkeypatch
):
    _mount, resolve, _ = _verdict(tmp_path, monkeypatch)

    def gone(_v):
        raise OSError("gone")

    monkeypatch.setattr(cache_tier, "open_root", gone)
    (tmp_path / "f").mkdir()
    env, state, _t = _fake(tmp_path / "f", None)
    msg = sr.start(env, resolve(), str(tmp_path / "int"))
    assert _serving(state) == str(tmp_path / "int") and "gone" in msg


def test_start_a_second_server_on_a_root_that_cannot_hold_one_is_internal(tmp_path, monkeypatch):
    """The external start fails (a drive in trouble): internal, and the why says so."""
    _mount, resolve, _ = _verdict(tmp_path, monkeypatch)
    (tmp_path / "f").mkdir()
    env, state, _t = _fake(tmp_path / "f", None)
    run = env.run

    def failing(*args, sccache_dir=None, cwd=None):
        if sccache_dir and "FAKE1TB" in sccache_dir:
            import subprocess

            return subprocess.CompletedProcess(args, 2, "", "Input/output error")
        return run(*args, sccache_dir=sccache_dir, cwd=cwd)

    env.run = failing
    msg = sr.start(env, resolve(), str(tmp_path / "int"))
    assert _serving(state) == str(tmp_path / "int") and "Input/output error" in msg


def test_the_fallback_is_never_a_sandboxes_home(monkeypatch):
    monkeypatch.setattr(sr.sys, "platform", "darwin")
    home_dir = "/u/me/Library/Caches/Mozilla.sccache"
    assert sr.fallback_dir("/sandbox/Library/Caches/Mozilla.sccache", "/u/me") == home_dir
    assert sr.fallback_dir("", "/u/me") == home_dir
    assert sr.fallback_dir("/u/me/pin", "/u/me") == "/u/me/pin"


# The default dir is swept to its cap once the server leaves it.
def _entries(root, n, size=4096):
    out = []
    for i in range(n):
        d = root / f"{i % 16:x}" / "0"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{i:064x}"
        f.write_bytes(b"x" * size)
        os.utime(f, (1000 + i, 1000 + i))
        out.append(f)
    return out


def test_trim_drops_the_oldest_until_the_dir_fits(tmp_path):
    files = _entries(tmp_path / "int", 10)
    assert sr.trim(str(tmp_path / "int"), 4 * 4096) == 6 * 4096
    assert [f.exists() for f in files] == [False] * 6 + [True] * 4
    assert sr.trim(str(tmp_path / "int"), 4 * 4096) == 0


def test_trim_follows_no_symlink(tmp_path):
    files = _entries(tmp_path / "real", 3)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    (tmp_path / "int").mkdir()
    (tmp_path / "int" / "a").symlink_to(tmp_path / "real")
    assert sr.trim(str(tmp_path / "link"), 0) == 0 == sr.trim(str(tmp_path / "int"), 0)
    assert all(f.exists() for f in files)
    assert sr.trim(str(tmp_path / "absent"), 0) == 0
