"""At a land the release canary asks what the pre-step asks: does a consumer READ a changed path
(GOH.GATEFLOOR, lib/consumer_reads.py)?

The canary cost a median 100 s (362 cpu-s) of every land whose gates changed, though the pre-step
already ran it only when a consumer names a changed path. Over a fixture goh tree whose land changes
gates/x.sh: no consumer naming it -> the land's canary sweeps nothing and says why; one naming
`$GOH_DIR/gates/x.sh` -> it sweeps (here it then refuses: the fixture tree has no goh to export,
which is the proof that it went on). Not at a land -> it sweeps as before.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

SCRIPT = REPO_ROOT / "scripts" / "release_canary.py"
pytestmark = [pytest.mark.load_sensitive]


def _git(d: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=d, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout.strip()  # fmt: skip


def _estate(tmp: Path, reads: bool) -> tuple[Path, Path, str]:
    goh = tmp / "goh"
    fast_init(goh)
    (goh / "gates").mkdir()
    (goh / "gates" / "x.sh").write_text("echo one\n")
    (goh / "lib").mkdir()
    (goh / "lib" / "y.py").write_text("Y = 1\n")
    _git(goh, "add", "-A")
    _git(goh, "commit", "-qm", "base")
    base = _git(goh, "rev-parse", "HEAD")
    (goh / "gates" / "x.sh").write_text("echo two\n")
    _git(goh, "commit", "-qam", "the land")
    consumer = tmp / "consumers" / "c"
    fast_init(consumer)
    write_gatesrc(consumer, "GOH_MAX_LINES=500\n")
    (consumer / "run.sh").write_text(
        '"$GOH_DIR/gates/x.sh"\n' if reads else '"$GOH_DIR/lib/y.py"\n'
    )
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-qm", "first")
    state = tmp / "state" / "estate_audit.json"
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text(f"{consumer}\n")
    return goh, state, base


def _canary(tmp: Path, goh: Path, state: Path, base: str, **env: str) -> tuple[str, dict]:
    (tmp / "root").mkdir(exist_ok=True)
    argv = [sys.executable, str(SCRIPT), "--root", str(tmp / "root"), "--state", str(state)]
    argv += ["--goh", str(goh), "--base", base, "--grace", "off"]
    r = subprocess.run(argv, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                       env=hermetic_env(**env), timeout=patience(120))  # fmt: skip
    return r.stdout + r.stderr, json.loads(state.read_text())["release_canary"]


def test_a_land_no_consumer_reads_sweeps_nothing_and_says_why(tmp_path: Path) -> None:
    goh, state, base = _estate(tmp_path, reads=False)
    out, rec = _canary(tmp_path, goh, state, base, GOH_GATE_CLASS="land", GOH_CHANGE_BASE=base)
    assert rec["verdict"] == "no-consumer-read", out
    assert "no consumer names a path this land changes" in out, out


def test_a_land_a_consumer_reads_is_swept(tmp_path: Path) -> None:
    goh, state, base = _estate(tmp_path, reads=True)
    out, rec = _canary(tmp_path, goh, state, base, GOH_GATE_CLASS="land", GOH_CHANGE_BASE=base)
    assert rec["verdict"] != "no-consumer-read", out
    assert "1 consumer name gates/x.sh -- swept" in out, out


def test_outside_a_land_the_canary_sweeps_as_before(tmp_path: Path) -> None:
    goh, state, base = _estate(tmp_path, reads=False)
    out, rec = _canary(tmp_path, goh, state, base, GOH_CHANGE_BASE=base)
    assert rec["verdict"] != "no-consumer-read", out
