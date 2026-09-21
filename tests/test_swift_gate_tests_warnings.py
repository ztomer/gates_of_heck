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
    r = subprocess.run(["/bin/bash", str(SWIFT_GATE), str(repo)], cwd=repo,
                       capture_output=True, text=True, env=env)
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
