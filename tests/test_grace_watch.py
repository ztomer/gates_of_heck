"""scripts/grace_watch.py: a grace end is judged on a schedule, with no release (GOH.GRACEWATCH).

The release canary refuses a release within 7 days of a grace end that turns a consumer red
(GOH.CANARYMISS), but only a release runs it: with no goh release in a grace's last week the end
still turned consumers red with nobody told. The watch is the daily estate audit's (its launchd
job): the goh consumers run now, under the day and at GOH_GRACE=over, named per consumer.

The fixture: a grace ending in 3 days, and a consumer that declares no scanner floor -- green under
the day (pinned `within`), red at the end with the undeclared `GOH_<X>_MIN_FILES=N` named.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from _small_store import estate_state
from _tree_clone import from_template
from conftest import REPO_ROOT, hermetic_env

SCRIPT = REPO_ROOT / "scripts" / "grace_watch.py"
HOOK = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"
DAY = 86400


def _git(d: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=d,
                   capture_output=True, text=True, check=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip


def _build_consumer(path: Path) -> None:
    """A consumer that declares no scanner floor, one commit; built once per run."""
    fast_init(path)
    write_gatesrc(path, "GOH_MAX_LINES=500\nGOH_ROADMAP=none\n")  # no GOH_*_MIN_FILES
    (path / "run.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "first")


def _setup(tmp_path: Path, goh: Path, factory) -> dict:
    env = hermetic_env(GOH_CACHE_DB=str(tmp_path / "verdicts" / "goh-cache.db"),
                       GOH_SEED_DIR=str(tmp_path / "no-seeds"))  # fmt: skip
    consumer = tmp_path / "consumers" / "unfloored"
    consumer.parent.mkdir()
    from_template(factory, "grace-consumer", _build_consumer, consumer)
    state = tmp_path / "state" / "estate_audit.json"
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text(f"{consumer}\n")
    (tmp_path / "estate").mkdir()
    pinned = tmp_path / "pinned-goh"  # the day's pass at `within` whatever the date
    pinned.write_text(f'#!/usr/bin/env bash\nGOH_GRACE="${{GOH_GRACE:-within}}" exec {goh} "$@"\n')
    pinned.chmod(0o755)
    return {"env": env, "state": state, "bin": pinned, "root": tmp_path}


def _watch(e: dict, over_from: int) -> tuple[subprocess.CompletedProcess, dict]:
    argv = [sys.executable, str(SCRIPT), "--root", str(e["root"] / "estate"), "--state",
            str(e["state"]), "--bin", str(e["bin"]), "--goh", str(REPO_ROOT),
            "--over-from", str(over_from)]  # fmt: skip
    r = subprocess.run(argv, capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL, env=e["env"])  # fmt: skip
    return r, estate_state(e["state"])["grace_watch"]


@pytest.mark.load_sensitive
def test_a_grace_end_in_three_days_is_red_with_no_release(tmp_path, goh, tmp_path_factory):
    e = _setup(tmp_path, goh, tmp_path_factory)
    r, rec = _watch(e, int(time.time()) + 3 * DAY)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert rec["ran"] and rec["count"] == 1, rec
    [(where, flips)] = rec["turned_red"].items()
    assert where.endswith("consumers/unfloored"), rec
    found = [line for f in flips for line in f["found"]]
    assert any("_MIN_FILES=" in line for line in found), flips  # the undeclared KEY=N, named
    assert "grace watch" in out and "unfloored" in out, out
    # Said at session start, where the owner looks (hooks/claude/estate_advisories.py).
    spec = importlib.util.spec_from_file_location("estate_advisories_watch", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    said = [ln for ln in hook.report(e["state"], dt.datetime.now(dt.UTC)) if "grace watch" in ln]
    assert said and "unfloored" in said[0] and "_MIN_FILES=" in said[0], said


@pytest.mark.load_sensitive
def test_an_end_beyond_the_window_sweeps_nothing(tmp_path, goh, tmp_path_factory):
    e = _setup(tmp_path, goh, tmp_path_factory)
    r, rec = _watch(e, int(time.time()) + 10 * DAY)
    assert r.returncode == 0, r.stdout + r.stderr
    assert rec["ran"] is False and "within 7 days" in rec["why"], rec


@pytest.mark.load_sensitive
def test_every_grace_end_of_the_tree_is_read(tmp_path):
    spec = importlib.util.spec_from_file_location("cg_watch", REPO_ROOT / "lib" / "canary_grace.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = tmp_path / "crates" / "goh" / "src"
    (src / "sub").mkdir(parents=True)
    (src / "grace.rs").write_text("pub const OVER_FROM: u64 = 1_000;\n")
    (src / "sub" / "scan.rs").write_text("/// x\npub const OVER_FROM: u64 = 3_000;\n")
    (src / "other.rs").write_text("const OVER_FROM_NOT: u64 = 9;\n")
    assert mod.ends(tmp_path) == [1000, 3000]
    assert mod.next_end(tmp_path, 1500) == 3000 and mod.next_end(tmp_path, 3000) is None
    assert mod.next_end(REPO_ROOT, time.time()) is not None or mod.ends(REPO_ROOT)
