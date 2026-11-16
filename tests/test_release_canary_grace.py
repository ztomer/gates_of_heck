"""The release canary judges the estate once more at GOH_GRACE=over (GOH.CANARYGRACE).

`crate::grace` holds a new scanner shape back until 2026-11-23, and the canary judged only under
the day, so a release could ship a red that appears only when the grace ends: nothing named the
consumers that would refuse every push on the 23rd. HEAD's pass now runs once more at
GOH_GRACE=over, and every step red there and not under the day is NAMED -- a warning, never the
release's refusal before the date -- in the record the ship result carries.

The plant is a shape the grace holds back: `$(open -a ...)`, a bare open inside a command
substitution (bareopen.rs, GOH.BAREOPEN2), unseeded. The head binary here pins the day's pass to
`within` (unset GOH_GRACE), so the test reads the same after the date as before it.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, hermetic_env

SCRIPT = REPO_ROOT / "scripts" / "release_canary.py"
HOOK = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"
PLANTED = '#!/usr/bin/env bash\nout="$(open -a "$APP")"\necho "$out"\n'
CLEAN = '#!/usr/bin/env bash\nout="$("$GOH_DIR/bin/goh" launch-app "$APP")"\necho "$out"\n'
RUN = "#!/usr/bin/env bash\necho hi\n"
FLOOR = re.compile(r"^\s+(GOH_\w+_MIN_FILES)=\d+\s+--", re.MULTILINE)


def _git(d: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=d,
                   capture_output=True, text=True, check=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip


def _consumer(at: Path, launch: str, goh: Path, env: dict) -> Path:
    """A consumer green at the grace's end but for `launch`: every scanner floor the end of the
    grace requires is declared (at 1, read off goh's own over-the-grace advisory), so the only
    step the end can turn red is the planted one."""
    fast_init(at)
    write_gatesrc(at, "GOH_MAX_LINES=500\nGOH_ROADMAP=none\n")
    for name, text in (("run.sh", RUN), ("launch.sh", launch)):
        (at / name).write_text(text)
        (at / name).chmod(0o755)
    _git(at, "add", "-A")
    _git(at, "commit", "-qm", "first")
    r = subprocess.run([str(goh), "structural", "--full", "--keep-going"], cwd=at,
                       env={**env, "GOH_GRACE": "over", "GOH_BIN": str(goh)}, capture_output=True,
                       text=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    floors = sorted(set(FLOOR.findall(r.stdout + r.stderr)))
    assert floors, r.stdout + r.stderr
    rc = at / ".gatesrc"
    rc.write_text(rc.read_text() + "".join(f"{k}=1\n" for k in floors))
    _git(at, "commit", "-qam", "floors")
    return at


def _setup(tmp_path: Path, goh: Path) -> dict:
    env = hermetic_env(GOH_VERDICT_DIR=str(tmp_path / "verdicts"),
                       GOH_SEED_DIR=str(tmp_path / "no-seeds"))  # fmt: skip
    planted = _consumer(tmp_path / "consumers" / "planted", PLANTED, goh, env)
    clean = _consumer(tmp_path / "consumers" / "clean", CLEAN, goh, env)
    state = tmp_path / "state" / "estate_audit.json"
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text(f"{planted}\n{clean}\n")
    (tmp_path / "estate").mkdir()
    # The day's pass at `within` whatever the date; the grace pass's GOH_GRACE=over is kept.
    pinned = tmp_path / "pinned-goh"
    pinned.write_text(f'#!/usr/bin/env bash\nGOH_GRACE="${{GOH_GRACE:-within}}" exec {goh} "$@"\n')
    pinned.chmod(0o755)
    return {"env": env, "state": state, "planted": planted, "bin": pinned, "root": tmp_path}


def _canary(e: dict, *extra: str) -> tuple[subprocess.CompletedProcess, dict]:
    argv = [sys.executable, str(SCRIPT), "--root", str(e["root"] / "estate"), "--state",
            str(e["state"]), "--base-bin", str(e["bin"]), "--head-bin", str(e["bin"]),
            "--goh", str(REPO_ROOT), *extra]  # fmt: skip
    r = subprocess.run(argv, capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL, env=e["env"])  # fmt: skip
    return r, json.loads(e["state"].read_text())["release_canary"]


@pytest.mark.load_sensitive
def test_the_grace_end_names_the_consumer_it_turns_red_and_refuses_nothing(tmp_path, goh):
    e = _setup(tmp_path, goh)
    r, rec = _canary(e, "--grace", "on")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out  # named, never the release's refusal before the date
    assert rec["verdict"] == "clear" and not rec["turned_red"] and not rec["already_red"], rec
    g = rec["grace_over"]
    assert g["ran"] and g["count"] == 1, g
    [(where, flips)] = g["turned_red"].items()
    assert where.endswith("consumers/planted"), g
    assert [f["step"] for f in flips] == ["no bare open of an app"], g
    assert flips[0]["found"] and "[bare_open]" in flips[0]["found"][0], g
    assert g["seconds"] >= 0 and g["head_seconds"] > 0, g
    assert "grace end" in out and "consumers/planted" in out, out
    # Said at session start too, where the owner looks (hooks/claude/estate_advisories.py).
    spec = importlib.util.spec_from_file_location("estate_advisories_grace", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    said = [ln for ln in hook.report(e["state"], dt.datetime.now(dt.UTC)) if "grace end" in ln]
    assert said and "planted" in said[0] and "no bare open of an app" in said[0], said
    # The instrument bites only on the plant: the clean spelling leaves the list empty.
    (e["planted"] / "launch.sh").write_text(CLEAN)
    _git(e["planted"], "commit", "-qam", "clean")
    r, rec = _canary(e, "--grace", "on")
    assert r.returncode == 0, r.stdout + r.stderr
    g = rec["grace_over"]
    assert g["ran"] and g["count"] == 0 and g["turned_red"] == {}, g


@pytest.mark.load_sensitive
def test_off_runs_no_grace_pass_and_says_why(tmp_path, goh):
    e = _setup(tmp_path, goh)
    r, rec = _canary(e, "--grace", "off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert rec["grace_over"]["ran"] is False and rec["grace_over"]["why"], rec
    assert rec["grace_over"]["count"] is None, rec


def test_auto_runs_the_pass_only_while_the_grace_runs():
    spec = importlib.util.spec_from_file_location(
        "canary_grace", REPO_ROOT / "lib" / "canary_grace.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.grace_pass("auto", 100, 99) == (True, "")
    ran, why = mod.grace_pass("auto", 100, 100)
    assert not ran and "ended" in why  # from the date the day's pass judges it
    ran, why = mod.grace_pass("auto", None, 0)
    assert not ran and "no grace" in why
    assert mod.grace_pass("on", 100, 200) == (True, "")
    assert mod.grace_pass("off", 100, 0)[0] is False
