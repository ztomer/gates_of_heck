"""A goh release moves every pin goh owns in a consumer AND lands it there (GOH.PINPROPAGATE).

On 2026-11-15 two copies of goh's versions stranded the estate. ruff: goh pinned 0.17.5 while four
consumers' pyproject.toml + uv.lock still said `ruff==0.17.4`, so every commit there was refused
until a hand bump (three more at 0.17.4 the day before). The wf pin: `goh wf-pin --bump` committed
"ci: pin goh vX" in every consumer and pushed none, so after v0.121.0 about 17 repos sat ahead of
origin and the next writer inherited a rebase. The fixture is that estate in small: one consumer
with a stale ruff pin, one with a stale wf pin, one an owner's unpushed commit holds, and one a past
bump stranded. After ONE `wf_pin.py --bump` (what goh ship's install step runs) and the push sweep
it hands off (lib/pin_sweep.py, GOH.PINPUSHGATE; run here in the foreground, `--sweep wait`) no
consumer pins an old version and none sits ahead of origin with a pin commit; the one that may not
be pushed is named in the sweep's record, never silent.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import consumer_pins
import pytest

sys.path.pop(0)

SCRIPT = REPO_ROOT / "scripts" / "wf_pin.py"
TAG = "v0.118.0"
RUFF = subprocess.run([sys.executable, str(REPO_ROOT / "gates" / "ruff_house.py"), "--pin"],
                      capture_output=True, text=True, check=True,
                      stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
WF = """jobs:
  gate:
    steps:
      - run: git clone --depth 1 --branch {tag} https://github.com/owner/gates_of_heck "$GOH_DIR"
      - run: python3 -m pip install --quiet ruff=={ruff}  # ruff==0.0.1 here is prose
"""
PYPROJECT = """[project]
name = "ruffy"
[dependency-groups]
dev = [
    "pytest==9.0.3",
    "ruff==0.0.1",  # ruff==0.0.2 in a comment is prose
]
"""
LOCK = """version = 1
[[package]]
name = "pytest"
version = "9.0.3"

[[package]]
name = "ruff"
version = "0.0.1"
"""
# The relock and the venv sync are uv's: this one rewrites the lock's ruff row to the pyproject's
# pin and logs every call, so the test needs no network and sees what was asked.
FAKE_UV = f"""#!{sys.executable}
import os, re, sys
open(os.environ["UV_LOG"], "a").write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:2] == ["lock"]:
    want = re.search(r'"ruff==([0-9.]+)"', open("pyproject.toml").read()).group(1)
    lock = re.sub(r'(name = "ruff"\\nversion = ")[^"]+', r"\\g<1>" + want, open("uv.lock").read())
    open("uv.lock", "w").write(lock)
"""
OLD_PIN = "ci: pin goh v0.117.0 in the hosted gate -- goh wf-pin --bump (GOH.WFPIN)"


def _env(tmp_path: Path) -> dict[str, str]:
    env = hermetic_env(drop_git=True)
    env["GOH_ESTATE_STATE"] = str(tmp_path / "state" / "estate_audit.json")
    env["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{env['PATH']}"
    env["UV_LOG"] = str(tmp_path / "uv.log")
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=hermetic_env(drop_git=True),
                          stdin=subprocess.DEVNULL).stdout  # fmt: skip


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", msg)


def _consumer(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """A consumer at ROOT/NAME whose main tracks a bare origin outside the estate."""
    repo = tmp_path / "estate" / name
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    fast_init(repo, "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "fixture")
    origin = tmp_path / "origins" / f"{name}.git"  # a bare clone: the origin holds the fixture
    _git(repo, "clone", "-q", "--bare", str(repo), str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "branch", "-q", "--set-upstream-to=origin/main", "main")
    return repo


def _ahead(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "@{u}..HEAD").strip())


def _estate(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(FAKE_UV)
    (bin_dir / "uv").chmod(0o755)
    current_wf = WF.format(tag=TAG, ruff=RUFF)
    ruffy = _consumer(tmp_path, "ruffy", {"pyproject.toml": PYPROJECT, "uv.lock": LOCK,
                                         ".gitignore": ".venv/\n",
                                         ".github/workflows/ci.yml": current_wf})  # fmt: skip
    venv_py = ruffy / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/bin/sh\n")
    venv_py.chmod(venv_py.stat().st_mode | stat.S_IXUSR)
    flow = ".github/workflows/ci.yml"
    wfy = _consumer(tmp_path, "wfy", {flow: WF.format(tag="v0.96.0", ruff="0.17.2")})
    busy = _consumer(tmp_path, "busy", {flow: WF.format(tag="v0.96.0", ruff=RUFF)})
    (busy / "work.txt").write_text("the owner's unpushed work\n")
    _commit(busy, "feat: the owner's work, not pushed yet")
    stranded = _consumer(
        tmp_path, "stranded", {".github/workflows/ci.yml": WF.format(tag="v0.117.0", ruff=RUFF)}
    )
    (stranded / ".github/workflows/ci.yml").write_text(current_wf)
    _commit(stranded, OLD_PIN)  # a past --bump's commit, never pushed (the v0.121.0 strand)
    return {"ruffy": ruffy, "wfy": wfy, "busy": busy, "stranded": stranded}


def _bump(tmp_path: Path, record: Path, *args: str) -> subprocess.CompletedProcess:
    env = _env(tmp_path)
    env["GOH_SHIP_RESULT"] = str(record)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path / "estate"), "--slug",
         "owner/gates_of_heck", "--tag", TAG, "--bump", "--sweep", "wait", *args],
        capture_output=True, text=True, check=False, env=env, timeout=patience(120),
        stdin=subprocess.DEVNULL,
    )  # fmt: skip


def test_the_manifest_names_each_pin_and_where_its_version_is_written():
    kinds = {k.name: k for k in consumer_pins.PINS}
    assert set(kinds) == {"goh", "ruff"}, kinds
    assert "required_tools.tsv" in kinds["ruff"].source, kinds["ruff"]
    assert consumer_pins.ruff_pin() == RUFF  # read from goh's one pin, never a second copy


def test_ruff_pins_are_read_in_code_never_in_prose():
    pins = consumer_pins.scan_ruff(PYPROJECT)
    assert [(p.line, p.tag) for p in pins] == [(6, "0.0.1")], pins
    wf = consumer_pins.scan_ruff(WF.format(tag=TAG, ruff="0.17.2"))
    assert [(p.line, p.tag) for p in wf] == [(5, "0.17.2")], wf


def test_one_bump_leaves_no_consumer_stale_and_none_ahead_with_a_pin_commit(tmp_path):
    repos = _estate(tmp_path)
    record = tmp_path / "ship.json"
    r = _bump(tmp_path, record)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    ruffy, wfy, busy, stranded = (repos[k] for k in ("ruffy", "wfy", "busy", "stranded"))
    # The ruff pin: the pyproject's, the lock and the venv all moved, in one commit, pushed.
    sweep = json.loads(consumer_pins.record_path(record).read_text())["sweep"]
    assert f'"ruff=={RUFF}"' in (ruffy / "pyproject.toml").read_text()
    assert "ruff==0.0.2 in a comment" in (ruffy / "pyproject.toml").read_text()  # prose kept
    assert f'name = "ruff"\nversion = "{RUFF}"' in (ruffy / "uv.lock").read_text()
    uv = (tmp_path / "uv.log").read_text().splitlines()
    assert "lock" in uv and f"pip install --python .venv/bin/python ruff=={RUFF}" in uv, uv
    changed = sorted(_git(ruffy, "show", "--name-only", "--format=", "HEAD").split())
    assert changed == ["pyproject.toml", "uv.lock"], changed
    assert _ahead(ruffy) == 0 and _git(ruffy, "status", "--porcelain") == "", out
    # the wf pin, and the workflow's own ruff install, moved and pushed.
    body = (wfy / ".github/workflows/ci.yml").read_text()
    assert f"--branch {TAG}" in body and f"ruff=={RUFF}  # ruff==0.0.1 here is prose" in body
    assert _ahead(wfy) == 0, out
    # a past bump's stranded commit is goh's own: pushed though nothing is stale now.
    assert _ahead(stranded) == 0, out
    # an owner's unpushed commit is never pushed by goh: the pin is committed and the refusal NAMED.
    assert f"--branch {TAG}" in (busy / ".github/workflows/ci.yml").read_text()
    assert _ahead(busy) == 2, out
    rec = json.loads(consumer_pins.record_path(record).read_text())
    assert rec["tag"] == TAG and rec["ruff"] == RUFF, rec
    status = {k: v["status"] for k, v in rec["repos"].items()}  # the install: commits only
    assert status == dict.fromkeys(("ruffy", "wfy", "busy"), "committed"), rec
    pushes = json.loads(Path(sweep).read_text())  # the sweep: each pin commit ahead, pushed
    status = {k: v["status"] for k, v in pushes["repos"].items()}
    assert status == {"ruffy": "pushed", "wfy": "pushed", "stranded": "pushed",
                      "busy": "committed"}, pushes  # fmt: skip
    assert "not pushed" in pushes["repos"]["busy"]["said"], pushes
    assert "the owner's work" in pushes["repos"]["busy"]["said"], pushes
    again = _bump(tmp_path, record)
    rec = json.loads(consumer_pins.record_path(record).read_text())
    pushes = json.loads(Path(rec["sweep"]).read_text())
    assert again.returncode == 0 and not rec["repos"], again.stdout + again.stderr
    assert set(pushes["repos"]) == {"busy"}, pushes


@pytest.mark.load_sensitive
def test_a_spent_push_budget_commits_and_names_what_the_next_sweep_pushes(tmp_path):
    repos = _estate(tmp_path)
    r = _bump(tmp_path, tmp_path / "ship.json", "--budget", "0")
    rec = json.loads(consumer_pins.record_path(tmp_path / "ship.json").read_text())
    rec = json.loads(Path(rec["sweep"]).read_text())
    assert r.returncode == 0 and rec["repos"]["wfy"]["status"] == "committed", r.stdout + r.stderr
    assert "budget is spent" in rec["repos"]["wfy"]["said"], rec
    assert _ahead(repos["wfy"]) == 1


def test_the_ship_result_carries_each_consumers_outcome(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import ship_status

    sys.path.pop(0)
    res = {"result": str(tmp_path / "ship.json"), "repo": str(tmp_path), "sha": "a" * 40}
    consumer_pins.record_path(Path(res["result"])).write_text(json.dumps({
        "schema": 2, "tag": TAG, "ruff": RUFF, "sweep": str(tmp_path / "ship.sweep.json"),
        "repos": {"ruffy": {"status": "committed", "said": "pinned in abc"},
                  "busy": {"status": "set_aside", "said": "set aside: on hold"}},
    }))  # fmt: skip
    ship_status.carry(res)
    c = res["consumer_pins"]
    assert c["committed"] == ["ruffy"] and c["not_committed"] == {"busy": "set aside: on hold"}
    assert c["sweep"] == str(tmp_path / "ship.sweep.json"), c
    line = consumer_pins.line(res)
    assert line and "1 committed" in line and "busy (set aside: on hold)" in line, line


def _stubbed_push(monkeypatch, tmp_path: Path, script: str, timeout: int) -> tuple[str, str]:
    """push() with its git reads stubbed and its push run as SCRIPT under a TIMEOUT-second bound."""
    real = consumer_pins.bounded_run.run_step
    monkeypatch.setattr(consumer_pins, "ahead", lambda repo, fetch=False: ("o/main", [("a", "s")]))
    monkeypatch.setattr(consumer_pins, "is_pin_commit", lambda *a: True)
    monkeypatch.setattr(consumer_pins.estate_commit, "git",
                        lambda *a: subprocess.CompletedProcess(a, 0, "0", ""))  # fmt: skip
    monkeypatch.setattr(consumer_pins.bounded_run, "run_step",
                        lambda argv, _t, grace, label, out: real(
                            ["sh", "-c", script], timeout, grace, label, out))  # fmt: skip
    return consumer_pins.push(tmp_path, "pinned", tmp_path / "push.log")


def test_a_push_the_budget_killed_is_committed_not_refused(monkeypatch, tmp_path):
    killed_job = "echo 'printf %s GOH_CI_STEPS 2>&1 | tee log'; sleep 30"
    status, said = _stubbed_push(monkeypatch, tmp_path, killed_job, 1)
    assert status == "committed", said
    assert "the sweep budget ended its push after " in said and "for a slot" in said, said
    assert "GOH_CI_STEPS" not in said and "refused" not in said, said


def test_a_push_its_gate_refused_stays_refused_with_its_own_lines(monkeypatch, tmp_path):
    status, said = _stubbed_push(monkeypatch, tmp_path, "echo 'ruff: 3 errors'; exit 1", 30)
    assert status == "refused" and "ruff: 3 errors" in said, said
