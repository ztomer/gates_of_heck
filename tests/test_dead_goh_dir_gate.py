"""A consumer's tools/gate.sh carries no goh-owned code, and still runs under a dead export GOH_DIR
(EST.GOHDIRSHELL, GOH.TRANSIENTDIR; GOH.CLASS.ROLLOUT).

install.sh writes tools/gate.sh once, as a starter the repo then edits. It used to also INSERT
lib/goh_root.sh's goh_dir_repair block into any gate.sh carrying the stock GOH line, and `goh hooks
--refresh` (which runs install.sh) committed `.githooks` alone: on 2026-11-14 14 consumers were
left with an uncommitted gate.sh, each an orphan tree. Now nothing edits a gate.sh that exists,
and the dead-GOH_DIR fallback lives behind the hook shim (gates/hook.sh): the gate a hook runs
inherits the repaired GOH_DIR.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _hook_goh import hook_goh
from conftest import REPO_ROOT, hermetic_env

OLD_LINE = 'GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"\n'
OLD_GATE = (
    "#!/usr/bin/env bash\nset -euo pipefail\n" + OLD_LINE + '"$GOH/gates/structural.sh" "$@"\n'
    '# this repo\'s own layer\n"$GOH/gates/py_gate.sh" .\n'
)


def _fake_goh(root: Path) -> Path:
    """A gates_of_heck whose commit gate runs the repo's tools/gate.sh, and whose layers record the
    GOH_DIR they saw; gates/hook.sh, tui/ and lib/bash5.sh are the real ones."""
    hook_goh(root)
    (root / "gates" / "commit_gate.sh").write_text("exec bash tools/gate.sh --staged\n")
    rec = 'echo "ran $0 GOH_DIR=${GOH_DIR:-}" >> "$(dirname "$0")/../ran"\n'
    for g in ("structural.sh", "py_gate.sh"):
        (root / "gates" / g).write_text("#!/usr/bin/env bash\n" + rec)
        (root / "gates" / g).chmod(0o755)
    return root


def _install(repo: Path) -> subprocess.CompletedProcess:
    env = hermetic_env(drop_git=True, GOH_SKIP_BUILD="1")
    return subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh"), str(repo)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _hook(repo: Path, tmp_path: Path, live: Path) -> subprocess.CompletedProcess:
    """The installed pre-commit shim, as git runs it, under a pruned HEAD export GOH_DIR."""
    dead = tmp_path / "cache" / "goh" / "head" / ("c06bc794" * 5)
    env = hermetic_env(drop_git=True, HOME=str(tmp_path / "home"), GOH_DIR=str(dead))
    env["GOH_LIVE_ROOT"] = str(live)
    return subprocess.run(
        ["sh", ".githooks/pre-commit"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_starter_gate_runs_under_a_dead_export(tmp_path, repo):
    assert _install(repo).returncode == 0
    live = _fake_goh(tmp_path / "live")
    r = _hook(repo, tmp_path, live)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (live / "ran").read_text() == f"ran {live}/gates/structural.sh GOH_DIR={live}\n"
    assert "c06bc794" in r.stderr, "the fallback is silent: nobody learns the process is stale"


def test_the_starter_gate_carries_no_goh_code(repo):
    assert _install(repo).returncode == 0
    body = (repo / "tools" / "gate.sh").read_text()
    assert "goh_dir_repair" not in body, "goh-owned code stamped into a consumer's tracked file"


def test_install_never_edits_an_existing_gate(tmp_path, repo):
    """The old starter keeps every byte, its layers and its stock line alike; a hook-run gate
    still finds the live goh through the shim."""
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(OLD_GATE)
    assert _install(repo).returncode == 0
    assert (repo / "tools" / "gate.sh").read_text() == OLD_GATE
    live = _fake_goh(tmp_path / "live")
    r = _hook(repo, tmp_path, live)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (live / "ran").read_text().count(f"GOH_DIR={live}\n") == 2


def test_a_gate_without_the_stock_line_is_left_alone(repo):
    (repo / "tools").mkdir()
    own = '#!/usr/bin/env bash\nexec bash "$GOH_DIR/gates/structural.sh" "$@"\n'
    (repo / "tools" / "gate.sh").write_text(own)
    assert _install(repo).returncode == 0
    assert (repo / "tools" / "gate.sh").read_text() == own
