"""A ship runs every step with the goh it started under, however goh moves (GOH.SHIPPINNED).

THE DEFECT. A consumer's v5.22.12 was shipped under goh v0.109 (08:23Z, 2026-11-13). While its
release step ran, v0.110.0 landed in the live checkout and removed `tools/ship.py --ci-wait`; the
worker, still running v0.109's code, then started its verify step as `ship.py --ci-wait RESULT`
against the LIVE ship.py, which answered `unrecognized arguments: --ci-wait` -- with push, release
and install already done. A running ship read its later steps from whatever goh was checked out at
the time.

THE MECHANISM is the gates' own (C4, gates/_from_head.sh): `tools/ship.py` run from a checkout
re-runs itself from the export of HEAD (`~/.cache/goh/head/<sha>/`), so the worker, its verify
step and every step's GOH_DIR name ONE immutable tree. The export now carries `bin/goh`, a shim
to the native build of the export's own commit: a step (a release kit, a pre-push gate) that runs
`$GOH_DIR/bin/goh` found nothing in an export before -- the export with no bin/ that leaked into
the coordinator's GOH_DIR (GOH.CLEANOPEN).

Planted here: a scratch checkout of the gates; a ship whose release step waits; a commit to the
checkout that renames the verify step's flag; the release let go; the ship must still finish.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import git
from test_gate_environment import _clean_checkout_of_todays_gates
from test_ship import _env, make_shipped
from timing_bounds import GiveUp

# The release kit: says it started, waits for the test's go-ahead, records what goh it was given.
KIT = """\
set -e
seen="$SHIP_T/kit.seen"
printf '%s\\n' "$GOH_DIR" > "$seen"
cat "$GOH_DIR/.goh-head" >> "$seen" 2>/dev/null || echo NO-PIN >> "$seen"
[ -x "$GOH_DIR/bin/goh" ] && echo HAS-BIN >> "$seen" || echo NO-BIN >> "$seen"
: > "$SHIP_T/kit.started"
for _ in $(seq 1200); do [ -f "$SHIP_T/kit.go" ] && break; sleep 0.1; done
git tag "v$GOH_SHIP_VERSION" && git push -q origin "v$GOH_SHIP_VERSION"
"""


ASK_EXPORT = '. "$1/gates/_from_head.sh" && _goh_head_dir "$1/tools/ship.py" && echo "$GOH_DIR"'


def _pinned_env(tmp_path: Path, gates: Path) -> dict[str, str]:
    """A consumer's `goh ship`: GOH_DIR at the shared checkout, no GOH_LIVE, outside this suite."""
    env = _env(tmp_path, GOH_SHIP_RELEASE=f"bash {tmp_path / 'kit.sh'}", GOH_SHIP_TIMEOUT="300")
    for k in [k for k in env if k == "GOH_LIVE" or k.startswith("PYTEST_")]:
        del env[k]
    env.update(
        GOH_DIR=str(gates), GOH_HEAD_CACHE=str(tmp_path / "head-cache"), SHIP_T=str(tmp_path)
    )
    return env


def _wait(path: Path, seconds: float) -> None:
    give_up = GiveUp(seconds)
    while not path.exists():
        assert not give_up.passed(), f"never appeared: {path}"
        time.sleep(0.1)


def test_a_ship_finishes_with_its_own_goh_when_the_live_checkout_renames_a_step_flag(
    repo: Path, tmp_path: Path
) -> None:
    gates = _clean_checkout_of_todays_gates(tmp_path / "g")
    started_at = git(gates, "rev-parse", "HEAD").strip()
    shipped = make_shipped(repo, tmp_path)
    (tmp_path / "kit.sh").write_text(KIT)
    env = _pinned_env(tmp_path, gates)
    out = tmp_path / "ship.json"
    started = subprocess.run(
        [sys.executable, str(gates / "tools" / "ship.py"), "--repo", str(shipped), "--out",
         str(out), "--item", "X.PIN", "--summary", "pinned"],
        env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
        check=False,
    )  # fmt: skip
    assert started.returncode == 0, started.stdout + started.stderr
    _wait(tmp_path / "kit.started", 60)

    # The live goh moves on mid-ship: the verify step's flag is renamed and committed.
    ship = gates / "tools" / "ship.py"
    text = ship.read_text(encoding="utf-8")
    # Not a longer spelling (`--verify-x`): argparse takes `--verify` as its abbreviation.
    renamed = (
        text.replace('"--verify", metavar', '"--step-verify", metavar')
        .replace("args.verify", "args.step_verify")
        .replace("--verify {shlex", "--step-verify {shlex")
    )
    assert renamed != text
    ship.write_text(renamed, encoding="utf-8")
    git(gates, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "rename --verify")

    (tmp_path / "kit.go").touch()
    give_up = GiveUp(120)
    while json.loads(out.read_text())["status"] == "running":
        assert not give_up.passed(), out.read_text()
        time.sleep(0.2)
    res = json.loads(out.read_text())
    assert res["status"] == "ok", json.dumps(res, indent=1)
    assert [(s["name"], s["status"]) for s in res["steps"]] == [
        ("push", "ok"), ("release", "ok"), ("install", "skipped"), ("verify", "ok"), ("done", "ok"),
    ]  # fmt: skip
    seen = (tmp_path / "kit.seen").read_text().split()
    assert seen[1:] == [started_at, "HAS-BIN"], (
        f"the kit's GOH_DIR is not the pinned export: {seen}"
    )
    assert seen[0] != str(gates), seen


def test_an_export_carries_a_bin_goh_that_runs_its_own_commit(repo: Path, tmp_path: Path) -> None:
    """`$GOH_DIR/bin/goh` in an export: the native build of the export's commit, GOH_DIR the export.

    GOH_BIN stands in for the build (the resolver trusts a named binary), so no cargo runs here."""
    gates = _clean_checkout_of_todays_gates(tmp_path / "g")
    fake = tmp_path / "fake-goh"
    fake.write_text('#!/usr/bin/env bash\necho "GOH_DIR=$GOH_DIR ARGS=$*"\n')
    fake.chmod(0o755)
    env = _pinned_env(tmp_path, gates)
    asked = subprocess.run(
        ["bash", "-c", ASK_EXPORT,
         "_", str(gates)],
        env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert asked.returncode == 0, asked.stdout + asked.stderr
    export = Path(asked.stdout.strip().splitlines()[-1])
    shim = export / "bin" / "goh"
    assert os.access(shim, os.X_OK), f"the export has no runnable bin/goh: {export}"
    env_bin = {k: v for k, v in env.items() if k not in ("GOH_DIR", "GOH_LIVE_ROOT")}
    ran = subprocess.run(
        [str(shim), "ship", "--status", "x"], env={**env_bin, "GOH_BIN": str(fake)},
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout.strip() == f"GOH_DIR={export} ARGS=ship --status x", ran.stdout
