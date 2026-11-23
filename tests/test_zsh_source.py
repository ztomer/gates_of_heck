"""Every goh shell library refuses a non-bash shell in words and RETURNS (GOH.ZSHSOURCE).

goh's libraries are bash. Sourced from zsh, `${BASH_SOURCE[0]}` is empty, so the bash 5 guard's
`. "$_goh_d/../lib/bash5.sh" || exit` found no file and EXITED THE CALLING SHELL with 127 -- a
user's interactive zsh, closed by one `.` (lib/house_python.sh, scan r6, 2026-11-15).
gates/swift_toolchain.sh alone carried the refusal (GOH.GITKILLLOCKFREE); this holds every library
to it: sourced from `zsh -f`, each returns non-zero, names itself and the shell, and the caller
runs on.

THE LIST IS DERIVED, never kept by hand: every shell file another goh shell file sources
(lib/source_closure.py), plus every `.sh` under lib/ and tui/ and every gates/_*.sh (the places a
library lives, so one only a consumer or a test sources is in too).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _own_files import own_source_files
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

from lib import source_closure

ZSH = shutil.which("zsh")
# What the derived list reads: every shell file goh tracks, and what each sources.
TREE_READS = ("lib/", "tui/", "gates/", "scripts/", "tools/", "checks/", "hooks/", ".github/")


def libraries() -> list[str]:
    """Every sourceable goh shell library, repo-relative."""
    files = own_source_files()
    out = {
        f
        for f in files
        if f.endswith(".sh")
        and (f.startswith(("lib/", "tui/")) or (f.startswith("gates/_") and "/" not in f[6:]))
    }
    for f in files:
        path = REPO_ROOT / f
        if f.startswith("tests/") or not source_closure._is_shell(path):
            continue
        for dep in source_closure.direct(path, REPO_ROOT)[0]:
            rel = dep.resolve().relative_to(REPO_ROOT).as_posix()
            if rel.endswith(".sh"):
                out.add(rel)
    return sorted(out)


def test_the_list_reads_its_subject():
    libs = libraries()
    # 69 on 2026-11-23: a derivation that stopped reading is red, not clean.
    assert len(libs) >= 60, libs
    assert {"lib/house_python.sh", "tui/lib.sh", "gates/_common.sh", "lib/bash5.sh"} <= set(libs)


def source_from_zsh(rel: str, tmp: Path) -> subprocess.CompletedProcess[str]:
    script = f'. "{REPO_ROOT / rel}" </dev/null >/dev/null; print -r -- "caller alive rc=$?"'
    return subprocess.run(
        [ZSH, "-f", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp,
        env=hermetic_env(drop_git=True),
        stdin=subprocess.DEVNULL,
        timeout=patience(60),
    )


@pytest.mark.skipif(ZSH is None, reason="no zsh on this machine")
def test_every_library_sourced_from_zsh_refuses_by_name_and_returns(tmp_path: Path):
    bad = []
    for rel in libraries():
        r = source_from_zsh(rel, tmp_path)
        alive = "caller alive rc=" in r.stdout
        rc = r.stdout.rsplit("rc=", 1)[-1].strip() if alive else None
        named = f"{rel} is bash" in r.stderr and "zsh" in r.stderr
        if not (alive and rc not in (None, "0") and named):
            bad.append(
                f"{rel}: exit {r.returncode}, caller rc {rc}, stderr {r.stderr.strip()[:120]!r}"
            )
    assert not bad, "\n".join(bad)


def test_a_library_sourced_from_bash_still_loads(tmp_path: Path):
    """The refusal is zsh's alone: bash sources the same file and gets its functions."""
    r = subprocess.run(
        ["bash", "-c", f'. "{REPO_ROOT}/tui/lib.sh"; declare -F ok >/dev/null && echo loaded'],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=hermetic_env(drop_git=True),
        stdin=subprocess.DEVNULL,
        timeout=patience(60),
    )
    assert r.stdout.strip() == "loaded", r.stderr
