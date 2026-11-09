"""swift_gate.sh — the TEST targets build under warnings-as-errors too.

`swift build -Xswiftc -warnings-as-errors` compiles only the product targets; `swift test` then
compiles the test targets with warnings as warnings. Four actor-isolation warnings sat in
ZoneWM's Tests/ through every green push until 2026-09-21, surfaced only when an unrelated change
recompiled the files. The gate's build step must pass --build-tests.
"""

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, mk_xcrun_find_shim, write

SWIFT_GATE = REPO_ROOT / "gates" / "swift_gate.sh"

# A swift shim that records every invocation and fails a build that carries the two flags the
# gate must pass together — the shape of a test target with a warning.
SWIFT = """#!/bin/bash
echo "$@" >> "$SWIFT_CALLS"
if [ "$1" = build ] && [[ "$*" == *--build-tests* ]] && [[ "$*" == *warnings-as-errors* ]] \\
   && [ -n "${TEST_WARNING:-}" ]; then
    echo "Tests/Foo.swift:1:1: error: something is main actor-isolated" >&2; exit 1
fi
# What swift 6.4 really does with a Swift 6 isolation warning under -warnings-as-errors (probed
# 2026-11-09 on token-pool's pre-fix LoginItemTests shape): prints it, coloured, and exits 0.
if [ "$1" = build ] && [ -n "${WARN_AT:-}" ]; then
    printf '%s:46:9: \033[1;33mwarning: \033[1;39mmain actor-isolated property '"'"'defaults'"'"' can not be mutated from a nonisolated context\033[0;0m\n' "$WARN_AT"
    echo "Build complete! (1.00s)"
fi
exit 0
"""


def _repo(tmp_path: Path):
    r = tmp_path / "pkg"
    r.mkdir()
    write(r, "Package.swift", "// swift-tools-version:6.0\n")
    write(r, ".gatesrc", "GOH_SWIFT_MODE=spm\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("swift", SWIFT), ("swiftlint", "#!/bin/bash\nexit 0\n")):
        f = bin_ / name
        f.write_text(body)
        f.chmod(0o755)
    mk_xcrun_find_shim(bin_, bin_ / "swift")
    return r, bin_


def _run(repo: Path, bin_dir: Path, tmp_path: Path, **extra):
    env = dict(os.environ, SWIFT_CALLS=str(tmp_path / "calls.txt"), **extra)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    r = subprocess.run(
        ["/bin/bash", str(SWIFT_GATE), str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    calls = (tmp_path / "calls.txt").read_text() if (tmp_path / "calls.txt").exists() else ""
    return r, calls


def test_the_build_step_compiles_the_test_targets_as_errors(tmp_path):
    repo, bin_ = _repo(tmp_path)
    r, calls = _run(repo, bin_, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    build = [c for c in calls.splitlines() if c.startswith("build")]
    assert build and "--build-tests" in build[0] and "-warnings-as-errors" in build[0], calls


def test_a_warning_in_a_test_target_is_red(tmp_path):
    repo, bin_ = _repo(tmp_path)
    r, _ = _run(repo, bin_, tmp_path, TEST_WARNING="1")
    assert r.returncode != 0, "a test-target warning passed the gate"
    assert "main actor-isolated" in r.stdout + r.stderr


# GOH.GATEHONEST (2): the step passed while token-pool's LoginItemTests.swift:46-47 carried two
# main-actor isolation warnings, because swift 6.4's -warnings-as-errors does not promote that
# diagnostic. The step judges what the compiler SAID, not only its exit code.
def test_a_warning_the_compiler_did_not_promote_is_red(tmp_path):
    repo, bin_ = _repo(tmp_path)
    at = repo / "Tests" / "PoolWidgetCoreTests" / "LoginItemTests.swift"
    r, _ = _run(repo, bin_, tmp_path, WARN_AT=str(at))
    out = r.stdout + r.stderr
    assert r.returncode != 0, f"a planted warning at exit 0 passed the gate:\n{out}"
    assert "LoginItemTests.swift:46:9" in out, out
    assert "build + tests (warnings as errors) failed" in out, out


def test_a_dependency_warning_stays_green(tmp_path):
    repo, bin_ = _repo(tmp_path)
    at = repo / ".build" / "checkouts" / "Dep" / "Sources" / "Dep" / "D.swift"
    r, _ = _run(repo, bin_, tmp_path, WARN_AT=str(at))
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_package_in_a_subdirectory_judges_its_own_sources(tmp_path):
    top = tmp_path / "top"
    top.mkdir()
    subprocess.run(["git", "init", "-q", str(top)], check=True, stdin=subprocess.DEVNULL)
    repo, bin_ = _repo(tmp_path)
    pkg = top / "widget"
    repo.rename(pkg)
    at = pkg / "Tests" / "T" / "LoginItemTests.swift"
    r, _ = _run(pkg, bin_, tmp_path, WARN_AT=str(at))
    assert r.returncode != 0, r.stdout + r.stderr
    assert "LoginItemTests.swift:46:9" in r.stdout + r.stderr
