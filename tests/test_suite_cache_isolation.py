"""goh's suite never writes the user's push caches (G11, 2026-10-26).

THE CLASS. A test ran gates/push_gate.sh with its GOH_* scrubbed, so the gate fell back to the
REAL ~/.cache/goh/push-logs and left a zero-byte `repo-<sha>-<time>.log` there at every suite run.
Closed three ways: the suite runs in its own HOME and XDG_CACHE_HOME (tests/suite_home.py, G12), every
test gets private push roots that every env scrub keeps (tests/host_locks.py, `scrubbed`), and each
cache writer refuses the user's ~/.cache/goh under the suite (lib/suite_cache_guard.sh) -- so a new
test that slips past the first two is red, not a quiet write to the user's cache.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from host_locks import CACHE_ROOTS, scrubbed

REPO_ROOT = Path(__file__).resolve().parent.parent
PUSH_GATE = REPO_ROOT / "gates" / "push_gate.sh"


def test_every_test_gets_private_push_roots() -> None:
    home_cache = os.path.realpath(os.path.expanduser("~/.cache/goh"))
    for name in CACHE_ROOTS:
        value = os.environ.get(name, "")
        assert value, f"{name} is not set for this test"
        assert not os.path.realpath(value).startswith(home_cache + "/push"), (name, value)


def test_an_env_scrub_keeps_the_private_roots() -> None:
    env = scrubbed(os.environ, ("GOH_", "GIT_"))
    for name in CACHE_ROOTS:
        assert env.get(name) == os.environ[name], name


# G12: the suite's own HOME, and every cache writer refusing the user's cache under the suite. Each
# writer is driven at a STAND-IN "real" home under tmp_path (HOME and the marker both name it), so a
# guard that does not fire writes into tmp_path, never into the user's cache.
REAL_MARKER = "GATES_OF_HECK_SUITE_REAL_HOME"


def _as_if_real(tmp_path: Path, *drop: str) -> tuple[Path, dict[str, str]]:
    real = tmp_path / "real-home"
    real.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in drop and k != "XDG_CACHE_HOME"}
    env.update(HOME=str(real), **{REAL_MARKER: str(real)})
    return real, env


def test_the_suite_runs_in_its_own_home() -> None:
    real = os.environ[REAL_MARKER]
    assert os.path.realpath(os.path.expanduser("~")) != real
    assert os.environ["XDG_CACHE_HOME"].startswith(f"{real}/.cache/goh/suite/")
    ident = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True,
                           text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    assert ident == "suite@goh.test", "the suite's git identity is not its own minimal one"


def test_the_push_gate_refuses_the_users_push_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast_init(repo, "main")
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    real, env = _as_if_real(tmp_path, *CACHE_ROOTS)
    env["GOH_DIR"] = str(REPO_ROOT)
    proc = subprocess.run(["bash", str(PUSH_GATE)], cwd=repo, env=env, capture_output=True,
                          input=f"refs/heads/main {'1' * 40} refs/heads/main {'0' * 40}\n",
                          text=True, timeout=60, check=False)  # fmt: skip
    assert proc.returncode != 0 and "the push log" in proc.stderr, proc.stderr
    assert not (real / ".cache" / "goh" / "push-logs").exists()


def test_the_head_export_refuses_the_users_head_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast_init(repo, "main")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "c"],
                   check=True, stdin=subprocess.DEVNULL)  # fmt: skip
    real, env = _as_if_real(tmp_path, "GOH_LIVE", "GOH_HEAD_CACHE", "PYTEST_CURRENT_TEST")
    script = f'. "{REPO_ROOT}/gates/_from_head.sh"; _goh_head_dir "$1"; echo "rc=$?"'
    proc = subprocess.run(["bash", "-c", script, "_", str(repo / "x.sh")], env=env,
                          capture_output=True, text=True, timeout=60, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip
    assert proc.returncode == 2 and "the head export cache" in proc.stderr, (
        proc.stdout + proc.stderr
    )
    assert not (real / ".cache" / "goh" / "head").exists()


def test_the_store_refuses_the_users_shared_store(tmp_path: Path) -> None:
    binary = tmp_path / "goh"
    binary.write_text("#!/bin/sh\necho stamp-g12\n")
    binary.chmod(0o755)
    real, env = _as_if_real(tmp_path, "GOH_BIN_STORE")
    store = tmp_path / "root" / "bin" / "by-stamp" / "x" / "goh"
    script = f'. "{REPO_ROOT}/gates/_goh_bin.sh"; goh_store_adopt "$1" "$2"; echo "rc=$?"'
    proc = subprocess.run(["bash", "-c", script, "_", str(binary), str(store)], env=env,
                          capture_output=True, text=True, timeout=60, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip
    assert proc.returncode == 2 and "the shared goh store" in proc.stderr, proc.stdout + proc.stderr
    assert not (real / ".cache" / "goh" / "by-stamp").exists()


def test_agent_guard_refuses_the_users_spawn_ledger(tmp_path: Path) -> None:
    real, env = _as_if_real(tmp_path)
    code = (f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'hooks' / 'claude')!r}); "
            "import agent_guard; print(agent_guard._append({'event': 't'}))")  # fmt: skip
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          timeout=60, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert proc.returncode != 0 and "user's ledger" in proc.stderr, proc.stdout + proc.stderr
    assert not (real / ".cache" / "goh" / "agent-spawns.jsonl").exists()
