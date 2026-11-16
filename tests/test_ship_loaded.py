"""goh ship waits for the tip's loaded verdict BEFORE its clock (GOH.SHIPLOADEDWAIT).

v0.125.0's release step spent 470.6 s of its 780.2 s in "the tip's loaded verdict": the ship
started 65 s after the land, so its release kit (tools/release_goh.sh) sat inside the release step
waiting for the land's background loaded run (lib/loaded_verdict.py). That run cannot be the land
gate's own suite: spinners take the load slot ALONE, waiting for every gate in flight on the
machine, and the land gate shares the slot with up to two others it would starve (tools/load.py
rule 8, D-0003). So a repo that declares GOH_LAND_LOADED has the ship worker wait for the verdict
under the land lock and before its first step; the release kit's own read is then instant, a red,
died or missing verdict refuses the ship before anything moved, and `--status` names the wait
apart from the steps. Calibrated: a planted failure that is red only under tools/load.py's
spinners passes bare and refuses the ship.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import test_ship
from conftest import REPO_ROOT, git, hermetic_env
from test_ship import _result, _ship
from timing_bounds import GiveUp, patience

shipped = test_ship.shipped  # the fixture, by assignment
LIB = REPO_ROOT / "lib" / "loaded_verdict.py"
SUITE = REPO_ROOT / "tools" / "loaded_suite.sh"
sys.path.insert(0, str(REPO_ROOT / "lib"))
import loaded_verdict  # noqa: E402

# Red only under spinners: a load experiment's registry (tools/load.py N>0) is a top-level `*.json`
# in GOH_LOAD_DIR naming its spinner pids; a 0-spinner run holds a place under gates/ instead.
LOAD_ONLY = (
    "import glob, json, os, sys; d = os.environ['GOH_LOAD_DIR']; "
    "sys.exit(1 if any(json.load(open(p)).get('pids') for p in glob.glob(d + '/*.json')) else 0)"
)


def _shows(cmd: str, out: Path) -> str:
    """A release kit that records the verdict's state when it starts, then tags and pushes."""
    show = f"{sys.executable} {LIB} show --repo . --sha HEAD --cmd '{cmd}' > {out}"
    return f'{show} && git tag "v$GOH_SHIP_VERSION" && git push -q origin "v$GOH_SHIP_VERSION"'


def _greens_after(path: Path, after_s: float) -> list[str]:
    """A stand-in for the land's runner: it writes its record green after AFTER_S."""
    code = (
        f"import sys, time; sys.path.insert(0, {str(REPO_ROOT / 'lib')!r}); import loaded_verdict "
        f"as v; from pathlib import Path; p = Path({str(path)!r}); time.sleep({after_s}); "
        f"r = v.read(p); r.update(state='green', rc=0, ended=int(time.time())); v.write(p, r)"
    )
    return [sys.executable, "-c", code]


def test_the_release_never_starts_while_the_tips_loaded_run_runs(shipped, tmp_path) -> None:
    cmd = "true"
    seen = tmp_path / "seen.json"
    path = loaded_verdict.record_path(shipped, "HEAD", cmd)
    with subprocess.Popen(_greens_after(path, 1.5), stdin=subprocess.DEVNULL) as runner:
        sha = git(shipped, "rev-parse", "HEAD").strip()
        loaded_verdict.write(path, {"state": "running", "sha": sha, "pid": runner.pid, "cmd": cmd,
                                    "started": int(time.time())})  # fmt: skip
        r = _ship(shipped, tmp_path, "--wait", GOH_LAND_LOADED=cmd,
                  GOH_SHIP_RELEASE=_shows(cmd, seen))  # fmt: skip
        runner.wait(timeout=patience(30))
    res = _result(tmp_path)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}\n{res.get('tail')}"
    assert json.loads(seen.read_text())["state"] == "green", "the release started while it ran"
    pre = res.get("before_clock") or {}
    assert pre.get("status") == "ok" and pre.get("name") == "the tip's loaded verdict", res
    status = subprocess.run([sys.executable, str(test_ship.SHIP), "--status", str(tmp_path /
                             "ship.json")], capture_output=True, text=True, check=False,
                            env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL,
                            timeout=patience(60))  # fmt: skip
    assert "before the clock: the tip's loaded verdict: ok" in status.stdout, status.stdout


def test_a_planted_load_only_failure_refuses_the_ship_before_any_step(shipped, tmp_path) -> None:
    env = {"GOH_LOAD_DIR": str(tmp_path / "load"), "GOH_BENCH_LOCK_DIR": str(tmp_path / "bench")}
    bare = subprocess.run([sys.executable, "-c", LOAD_ONLY], env=hermetic_env(**env),
                          stdin=subprocess.DEVNULL, check=False, timeout=patience(30))  # fmt: skip
    assert bare.returncode == 0, "the planted failure is red without load: not load-only"
    cmd = f'bash {SUITE} -n 1 -- {sys.executable} -c "{LOAD_ONLY}"'
    run_env = hermetic_env(drop_git=True, GOH_RUN_DIR=str(tmp_path / "run"), **env)
    started = subprocess.run([sys.executable, str(LIB), "start", "--repo", str(shipped), "--sha",
                              "HEAD", "--cmd", cmd], env=run_env, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, check=False,
                             timeout=patience(60))  # fmt: skip
    assert started.returncode == 0, started.stdout + started.stderr
    path = loaded_verdict.record_path(shipped, "HEAD", cmd)
    give_up = GiveUp(120)
    while not give_up.passed() and loaded_verdict.state(loaded_verdict.read(path)) == "running":
        time.sleep(0.2)
    assert loaded_verdict.state(loaded_verdict.read(path)) == "red", loaded_verdict.read(path)
    r = _ship(shipped, tmp_path, "--wait", GOH_LAND_LOADED=cmd, **env)
    res = _result(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert res["status"] == "failed" and res["steps"] == [], res
    assert (res.get("before_clock") or {}).get("status") == "failed", res
    assert any("red (exit 1)" in line for line in res["tail"]), res["tail"]
    assert not git(shipped, "ls-remote", "origin", "refs/tags/v1.1.0").strip(), "it released"


def test_a_repo_declaring_no_loaded_run_waits_for_nothing(shipped, tmp_path) -> None:
    r = _ship(shipped, tmp_path, "--wait", GOH_LAND_LOADED="")
    res = _result(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "before_clock" not in res, res
