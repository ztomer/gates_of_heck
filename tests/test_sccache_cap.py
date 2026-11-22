"""sccache's disk cap has ONE owner: goh's lib/sccache_cap.py (GOH.SCCACHECAP).

sccache evicted before reuse: at its 20 GiB cap the oldest entry was 7.6 h old and the Rust hit
rate 49% (2026-11-15), 17.9% a week later (2026-11-22). The cap lived in two hand-set places, the
config file and SCCACHE_CACHE_SIZE in ~/.zshenv (which outranks the file for a server started
from zsh, and reaches no server started otherwise). goh now writes the config file from one
constant, and its gates drop an inherited SCCACHE_CACHE_SIZE so the file is the only word.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

CAP = REPO_ROOT / "lib" / "sccache_cap.py"
GIB = 1 << 30


def _cap(conf: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    full = hermetic_env(SCCACHE_CONF=str(conf), **env)
    if "SCCACHE_CACHE_SIZE" not in env:
        full.pop("SCCACHE_CACHE_SIZE", None)
    return subprocess.run([sys.executable, str(CAP), *args], env=full, capture_output=True,
                          text=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip


def test_apply_sets_the_cap_and_keeps_the_rest(tmp_path):
    conf = tmp_path / "config"
    conf.write_text("# mine\n[cache.disk]\nsize = 21474836480   # 20 GiB\n\n[dist]\nx = 1\n")
    got = _cap(conf, "apply")
    assert got.returncode == 0, got.stderr
    text = conf.read_text()
    assert f"size = {40 * GIB}" in text and "21474836480" not in text, text
    assert text.startswith("# mine\n") and "[dist]\nx = 1" in text, text
    assert _cap(conf, "check").returncode == 0


def test_apply_writes_a_missing_config(tmp_path):
    conf = tmp_path / "sub" / "config"
    assert _cap(conf, "check").returncode == 1, "a missing config passed the check"
    assert _cap(conf, "apply").returncode == 0
    assert f"[cache.disk]\nsize = {40 * GIB}" in conf.read_text()


def test_a_second_owner_in_the_environment_is_named(tmp_path):
    conf = tmp_path / "config"
    _cap(conf, "apply")
    got = _cap(conf, "check", SCCACHE_CACHE_SIZE="20G")
    assert got.returncode == 1 and "SCCACHE_CACHE_SIZE" in got.stdout + got.stderr


def test_the_gates_drop_an_inherited_cap(tmp_path):
    script = f'. "{REPO_ROOT}/lib/sccache_dir.sh"; printf "%s" "${{SCCACHE_CACHE_SIZE-unset}}"'
    env = hermetic_env(SCCACHE_CACHE_SIZE="20G")
    got = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True,
                         check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert got.stdout == "unset", got.stdout + got.stderr
