"""coverage_swift.py — binary/profdata pairing and crash discipline.

Contracts pinned here:
  - the .xctest binary is chosen by mtime NEAREST the chosen profdata, never
    Debug-first glob order: a stale Debug binary next to a fresh profdata
    used to be paired silently (llvm-cov reporting against mismatched code)
  - genuine pairing ambiguity is a named exit 2 (cpp-mode precedent)
  - engine=xcodebuild RUN mode actually reaches report processing: it used to
    return a (binary, profdata) tuple straight out of main, so sys.exit got a
    tuple and the mode never produced a verdict
  - ANY uncaught exception exits 2 naming the error — an uncaught traceback
    exits 1, which consumers read as "below floor"
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT, mk_fake_swift_toolchain

SWIFT_HELPER = REPO_ROOT / "gates" / "coverage_swift.py"

DD_BINARIES = {
    "Debug": "dd/Build/Products/Debug/AppTests.xctest/Contents/MacOS/AppTests",
    "Release": "dd/Build/Products/Release/AppTests.xctest/Contents/MacOS/AppTests",
}
PROFDATA = "dd/Build/ProfileData/FE-DEADBEEF/Coverage.profdata"


def _load_helper():
    import importlib.util
    spec = importlib.util.spec_from_file_location("coverage_swift_sel", SWIFT_HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_pick_binary_chooses_mtime_nearest_not_glob_order(tmp_path):
    mod = _load_helper()
    root = _root(tmp_path / "a")
    bin_ = mk_fake_swift_toolchain(root, second_config=True)
    profdata = root / PROFDATA
    dbg = root / DD_BINARIES["Debug"]
    rel = root / DD_BINARIES["Release"]
    # Glob order (Debug first) would pick the stale binary; truth is Release.
    assert os.path.getmtime(dbg) < os.path.getmtime(profdata)
    assert Path(mod.pick_binary(profdata, [dbg, rel])) == rel


def test_pick_binary_ambiguity_is_named_exit_2(tmp_path):
    mod = _load_helper()
    root = _root(tmp_path / "b")
    bin_ = mk_fake_swift_toolchain(root, second_config=True)
    profdata = root / PROFDATA
    rel = root / DD_BINARIES["Release"]
    twin = root / DD_BINARIES["Debug"]
    os.utime(twin, (profdata.stat().st_mtime,) * 2)  # make both nearest
    with pytest.raises(SystemExit) as exc:
        mod.pick_binary(profdata, [twin, rel])
    assert exc.value.code == 2


def _run_gate(bin_dir: Path, *args: str):
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    for k in ("GOH_COV_FLOOR_SWIFT", "GOH_COV_SWIFT_ENGINE",
              "GOH_COV_SCHEME", "GOH_COV_XCRESULT"):
        env.pop(k, None)
    env["GOH_COV_SCHEME"] = "App"
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), *args],
        cwd=str(bin_dir), capture_output=True, text=True, env=env,
    )


def test_xcodebuild_engine_pairs_fresh_release_binary(tmp_path):
    """End-to-end through the real gate: two configs on disk, Release is the
    mtime twin of the profdata — llvm-cov must be invoked against Release,
    and the run must reach a real verdict (50% vs the floor)."""
    log = tmp_path / "xcrun-args.log"
    root = _root(tmp_path / "c")
    bin_ = mk_fake_swift_toolchain(root, second_config=True,
                                   xcrun_log=log)
    env_dd = str(root / "dd")
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"),
         "--lang", "swift", "--engine", "xcodebuild",
         "--floor", "40", str(root / "proj")],
        cwd=str(root), capture_output=True, text=True,
        env={**dict(os.environ),
             "PATH": f"{bin_}:/usr/bin:/bin",
             "GOH_COV_SCHEME": "App", "GOH_COV_DD": env_dd},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "50." in r.stdout + r.stderr
    invoked = log.read_text()
    assert DD_BINARIES["Release"].split("/")[-2] == "MacOS"
    assert str(root / DD_BINARIES["Release"]) in invoked, (
        f"xcrun was not given the fresh binary; argv log:\n{invoked}")
    assert str(root / DD_BINARIES["Debug"]) not in invoked


def test_uncaught_exception_exits_2_naming_error_not_below_floor(tmp_path):
    """A Latin-1 (invalid UTF-8) source raised UnicodeDecodeError inside
    find_exclusions: the traceback exited 1 — indistinguishable from a
    coverage miss. It must exit 2 naming the error."""
    bin_ = mk_fake_swift_toolchain(_root(tmp_path / "d"))
    src = tmp_path / "d" / "proj" / "Sources" / "pkg" / "lib.swift"
    src.write_bytes(b'let bad = "\xe9"\n' + b"\n" * 9)
    r = subprocess.run(
        [sys.executable, str(SWIFT_HELPER), "--floor", "40",
         "--proj", str(tmp_path / "d" / "proj")],
        capture_output=True, text=True,
        env={**dict(os.environ), "PATH": f"{bin_}:/usr/bin:/bin"},
    )
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    combined = r.stdout + r.stderr
    assert "UnicodeDecodeError" in combined or "'utf-8'" in combined
    assert "Traceback" not in r.stderr
