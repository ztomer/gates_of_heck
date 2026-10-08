"""coverage_gate.sh, C++ engine: the profile merge is bounded, and judges only THIS run.

ZoneWM (D-0245, 2026-10-08): SwiftPM's per-process `%p` profiles numbered one per test, and the
merge that passed them as arguments died at ARG_MAX ("Argument list too long") at 6,965 files. The
cpp engine had the same shape: `ctest` runs one process per test under `default-%p.profraw`, and
the merge took `$raws` unquoted as argv -- unbounded, and split on a space in the path. Reading the
code found the sibling: a previous run's `default-*.profraw` sat in the build dir and was merged
into this run's number. Shims stand in for cmake, ctest and xcrun; each records what it was given.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

COV_GATE = REPO_ROOT / "gates" / "coverage_gate.sh"

CTEST = r"""#!/bin/bash
printf '%s\n' "$LLVM_PROFILE_FILE" > "$SHIM_LOG/profile-pattern"
for i in 1 2 3 4 5; do  # one process per test, as ctest runs them
  f="${LLVM_PROFILE_FILE//%p/$((1000 + i))}"
  f="$(printf '%s' "$f" | sed -E 's/%[0-9]*m/pool/')"
  echo "run $i" >> "$f"
done
"""

XCRUN = r"""#!/bin/bash
case "$1 $2" in
  "llvm-profdata merge")
    printf '%s\n' "$@" > "$SHIM_LOG/merge-argv"
    prev=""; for a; do
      [ "$prev" = "-o" ] && : > "$a"
      case "$a" in --input-files=*) cp "${a#--input-files=}" "$SHIM_LOG/merge-list" ;; esac
      prev="$a"; done ;;
  "llvm-cov export")
    echo '{"data":[{"totals":{"lines":{"percent":90.0}}}]}' ;;
esac
"""


def _toolchain(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir, log = tmp_path / "bin", tmp_path / "log"
    bin_dir.mkdir()
    log.mkdir()
    for name, body in (("cmake", "#!/bin/bash\nexit 0\n"), ("ctest", CTEST), ("xcrun", XCRUN)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    return bin_dir, log


def _run(tmp_path: Path, proj: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir, log = _toolchain(tmp_path)
    build = proj / "build-cov"
    build.mkdir(parents=True)
    (build / "unit_test").write_text("#!/bin/sh\n")
    (build / "unit_test").chmod(0o755)
    (build / "default-stale.profraw").write_text("a previous run\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "SHIM_LOG": str(log)}
    for k in ("GOH_COV_FLOOR_CPP", "GOH_CPP_TEST_BIN", "GOH_CPP_BUILD_DIR", "GOH_CTEST_ARGS"):
        env.pop(k, None)
    r = subprocess.run(["/bin/bash", str(COV_GATE), "--lang", "cpp", "--floor", "50", str(proj)],
                       capture_output=True, text=True, env=env, timeout=60)  # fmt: skip
    return r, log


def test_the_profile_count_is_bounded_and_the_merge_reads_a_list(tmp_path: Path) -> None:
    r, log = _run(tmp_path, tmp_path / "proj")
    assert r.returncode == 0, r.stdout + r.stderr
    pattern = (log / "profile-pattern").read_text().strip()
    assert "%p" not in pattern and re.search(r"%\d+m", pattern), (
        f"{pattern}: one profile per test process is a command line sized by the test count"
    )
    argv = (log / "merge-argv").read_text().splitlines()
    assert [a for a in argv if a.endswith(".profraw")] == [], argv
    assert [a for a in argv if a.startswith("--input-files=")] != [], argv


def test_a_previous_runs_profile_is_not_merged(tmp_path: Path) -> None:
    r, log = _run(tmp_path, tmp_path / "proj")
    assert r.returncode == 0, r.stdout + r.stderr
    listed = (log / "merge-list").read_text().splitlines()
    assert len(listed) > 0
    assert not [p for p in listed if "stale" in p], listed


def test_a_build_path_with_a_space_merges_whole(tmp_path: Path) -> None:
    r, log = _run(tmp_path, tmp_path / "my proj")
    assert r.returncode == 0, r.stdout + r.stderr
    listed = (log / "merge-list").read_text().splitlines()
    assert len(listed) > 0
    assert all(Path(p).is_file() for p in listed), listed
