"""swift_gate.sh xcode mode: a cold build reads Xcode's compilation cache (GOH.SWIFTCASPROBE).

The gate wipes derived data because an incremental build reports only what it recompiled. A CAS
hit is a different thing: every compile job still runs, keyed on its inputs and flags, and the
hit REPLAYS the job's diagnostics. Probed 2026-11-22 (Xcode 27B52, swift 6.4.2) on an xcodegen
project with a planted unused value, through this gate twice with .build wiped between: run 2 had
142 cache hits, 0 misses, printed the warning and the gate failed on it, as run 1 did. So cold
mode keeps the cache, outside .build, capped by Xcode's own COMPILATION_CACHE_LIMIT_SIZE.

SwiftPM mode takes no cache: swift 6.4's default build system (swiftbuild) has no caching option,
and `-Xswiftc -cache-compile-job -Xswiftc -cas-path` fails every compile ("stat cache file ...
not found"); the native build system that might take it is deprecated (engineering rule 17).
"""

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env
from test_swift_gate_project_selection import _mk_repo

SWIFT_GATE = REPO_ROOT / "gates" / "swift_gate.sh"


def _run(tmp_path: Path, gatesrc_extra: str = "", **extra):
    repo, bin_ = _mk_repo(tmp_path, ["App.xcodeproj"])
    with (repo / ".gatesrc").open("a") as f:
        f.write(gatesrc_extra)
    argv = tmp_path / "xcodebuild.argv"
    (bin_ / "xcodebuild").write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{argv}"\nexit 0\n')
    env = hermetic_env(XDG_CACHE_HOME=str(tmp_path / "xdg"), **extra)
    env["PATH"] = f"{bin_}:{env['PATH']}"
    r = subprocess.run(
        ["/bin/bash", str(SWIFT_GATE), str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    args = argv.read_text().splitlines() if argv.exists() else []
    return r, args


def test_a_cold_xcode_build_reads_the_compilation_cache_outside_the_wiped_tree(tmp_path):
    r, args = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COMPILATION_CACHE_ENABLE_CACHING=YES" in args, args
    cas = str(tmp_path / "xdg" / "goh" / "swift-cas")
    assert f"COMPILATION_CACHE_CAS_PATH={cas}" in args, args
    assert any(a.startswith("COMPILATION_CACHE_LIMIT_SIZE=") for a in args), args


def test_the_cache_dir_and_cap_are_the_repos_to_choose(tmp_path):
    gatesrc = f"GOH_SWIFT_CAS_DIR={tmp_path / 'elsewhere'}\nGOH_SWIFT_CAS_LIMIT=3G\n"
    r, args = _run(tmp_path, gatesrc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"COMPILATION_CACHE_CAS_PATH={tmp_path / 'elsewhere'}" in args, args
    assert "COMPILATION_CACHE_LIMIT_SIZE=3G" in args, args


def test_the_cache_can_be_turned_off(tmp_path):
    r, args = _run(tmp_path, "GOH_SWIFT_CAS=0\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert args and not any(a.startswith("COMPILATION_CACHE") for a in args), args


def test_a_warm_build_is_left_as_it_was(tmp_path):
    r, args = _run(tmp_path, "GOH_SWIFT_COLD=0\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert args and not any(a.startswith("COMPILATION_CACHE") for a in args), args
