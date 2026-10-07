"""Everything we put in the SHARED temp dir is ours to claim, and something claims it (ZoneWM, 2026-10-07).

ZoneWM found ~1,500 `goh-*` entries stranded in `$(getconf DARWIN_USER_TEMP_DIR)`. A trap or an
atexit removes a run's own files, but neither runs on SIGKILL, and the backstop in local_ci.sh
(`prune_kept.py <tmp> goh- 0 43200`) only sees what is NAMED `goh-*` in the TMPDIR of a push.
Three holes: temps that carried no prefix at all (release.sh's bare `mktemp`, a test stub's, every
`TemporaryDirectory()`), so no sweep could ever claim them; a suite that made `goh-test-git.*`
seeds and never removed them; and a suite run that never swept, so its residue waited for a push.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from conftest import REPO_ROOT

SHELL = re.compile(r"\$\(\s*mktemp\b([^)]*)\)")
PY = re.compile(r"tempfile\.(mkdtemp|mkstemp|NamedTemporaryFile|TemporaryDirectory)\(([^)]*)\)")
SHARED = ("${TMPDIR", "$TMPDIR", "/tmp")


def _shell_unclaimable(args: str) -> bool:
    """A `mktemp` call lands in the shared dir without our prefix."""
    words = [w.strip("\"'") for w in args.split()]
    if "-t" in words:  # BSD `-t name`: always the shared dir
        return not words[words.index("-t") + 1].startswith("goh-")
    paths = [w for w in words if not w.startswith("-")]
    if not paths:
        return True  # bare `mktemp` / `mktemp -d`: tmp.XXXXXX, nobody's
    template = paths[-1]
    if not template.startswith(SHARED):
        return False  # a directory the caller owns (a cache, a test's tmp_path)
    return not template.rsplit("/", 1)[-1].startswith("goh-")


def _py_unclaimable(args: str) -> bool:
    return "dir=" not in args and not re.search(r"prefix=[\"']goh-", args)


def _offenders() -> list[str]:
    files = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "*.sh", "*.py", "hooks/*"],
                           capture_output=True, text=True, check=True).stdout.split()  # fmt: skip
    bad = []
    for rel in files:
        if rel == "tests/test_temp_claimable.py":
            continue  # this file names the patterns
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if any(_shell_unclaimable(m.group(1)) for m in SHELL.finditer(line)) or any(
                _py_unclaimable(m.group(2)) for m in PY.finditer(line)
            ):
                bad.append(f"{rel}:{n}: {line.strip()}")
    return bad


def test_the_scanner_tells_claimable_from_not() -> None:
    assert _shell_unclaimable("")
    assert _shell_unclaimable(" -d")
    assert _shell_unclaimable(' "${TMPDIR:-/tmp}/release-x.XXXXXX"')
    assert _shell_unclaimable(" -t rustgate")
    assert not _shell_unclaimable(' -d "${TMPDIR:-/tmp}/goh-local-ci.XXXXXX"')
    assert not _shell_unclaimable(' "$cache/.build.XXXXXX"')
    assert _py_unclaimable("") and _py_unclaimable('prefix="x."')
    assert not _py_unclaimable('prefix="goh-x."') and not _py_unclaimable("dir=d")


def test_every_temp_in_the_shared_dir_carries_the_claimable_prefix() -> None:
    bad = _offenders()
    assert len(bad) == 0, (
        "a temp in the shared dir with no `goh-` prefix can never be swept when its run is killed:\n"
        + "\n".join(bad)
    )


def test_a_test_git_seed_is_removed_when_its_process_exits(tmp_path: Path) -> None:
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); from _fast_git import fast_init; "
        "fast_init(sys.argv[2]); fast_init(sys.argv[3], 'main')"
    )
    subprocess.run([sys.executable, "-c", code, str(REPO_ROOT / "tests"), str(tmp_path / "a"),
                    str(tmp_path / "b")], check=True, env={**os.environ, "TMPDIR": str(tmpdir)})  # fmt: skip
    assert (tmp_path / "a" / ".git").is_dir() and (tmp_path / "b" / ".git").is_dir()
    assert sorted(p.name for p in tmpdir.iterdir()) == []


def test_a_suite_run_sweeps_what_killed_runs_left(tmp_path: Path) -> None:
    """The controller sweeps its TMPDIR at session start: stale `goh-*` goes, young and foreign stay."""
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import _tree_guard

    old = time.time() - 13 * 3600
    for name in ("goh-rust-scope.abc", "goh-drift-cargo.def", "other.ghi"):
        (tmp_path / name).mkdir()
        os.utime(tmp_path / name, (old, old))
    (tmp_path / "goh-test-git.live").mkdir()
    _tree_guard.sweep_temp(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["goh-test-git.live", "other.ghi"]


def test_the_session_start_runs_the_sweep(tmp_path: Path) -> None:
    """Wired, not just written: a real (one-test) session in a TMPDIR holding a stale entry."""
    stale = tmp_path / "goh-rust-scope.stale"
    stale.mkdir()
    old = time.time() - 13 * 3600
    os.utime(stale, (old, old))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:xdist", "-p", "no:randomly",
         "tests/test_temp_claimable.py::test_the_scanner_tells_claimable_from_not"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        env={**os.environ, "TMPDIR": str(tmp_path)}, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert not stale.exists()
