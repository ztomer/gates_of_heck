"""swift_gate.sh (spm) compiles the package ONCE per run (GOH.SWIFTONEBUILD).

The build step ran `swift build --build-tests -Xswiftc -warnings-as-errors`, then the test step ran
`swift test --enable-code-coverage`, whose different flags recompiled every target: token-pool's
widget push gate spent build 23.0 s + test 40.8 s (2026-11-15). Now the build step compiles with
coverage instrumentation as well, and the test step runs what it built (`--skip-build`).

The shim models the real toolchain (probed with swift 6.4.2, 2026-11-22): a `swift test` WITHOUT
--skip-build compiles again; a `--skip-build --enable-code-coverage` run writes a coverage payload
only when the build it reuses was instrumented. So the verdicts stay honest: a planted warning still
fails the build step, and a seeded coverage drop still fails the floor, read from the same payload.
"""

import os
import subprocess
from pathlib import Path

from _tool_versions import floor
from conftest import REPO_ROOT, mk_xcrun_find_shim, write

SWIFT_GATE = REPO_ROOT / "gates" / "swift_gate.sh"

SWIFT = r"""#!/bin/bash
echo "$@" >> "$SWIFT_CALLS"
payload="$PWD/.build/out/Products/Debug/codecov/Pkg.json"
case "$1" in
  package)
    printf '{"path":"%s","targets":[{"name":"Pkg","path":"Sources/Pkg","type":"library"}]}' "$PWD"
    exit 0 ;;
  build)
    echo COMPILE >> "$SWIFT_CALLS"
    mkdir -p .build
    rm -f .build/instrumented
    if [[ "$*" == *--enable-code-coverage* ]]; then touch .build/instrumented; fi
    if [ -n "${WARN_AT:-}" ]; then
        echo "$WARN_AT:3:5: warning: variable 'x' was never used"
        echo "Build complete! (1.00s)"
    fi
    exit 0 ;;
  test)
    if [[ "$*" == *--show-codecov-path* ]]; then echo "$payload"; exit 0; fi
    if [[ "$*" != *--skip-build* ]]; then
        echo COMPILE >> "$SWIFT_CALLS"
        if [[ "$*" == *--enable-code-coverage* ]]; then touch .build/instrumented; fi
    fi
    if [[ "$*" == *--enable-code-coverage* ]] && [ -f .build/instrumented ]; then
        mkdir -p "$(dirname "$payload")"
        f='{"filename":"%s/Sources/Pkg/P.swift","covered_lines":%s,"total_lines":100}'
        printf "{\"data\":[{\"files\":[$f]}]}" "$PWD" "${COVERED:-90}" > "$payload"
    fi
    exit 0 ;;
esac
exit 0
"""


def _repo(tmp_path: Path, gatesrc: str):
    r = tmp_path / "pkg"
    r.mkdir()
    write(r, "Package.swift", "// swift-tools-version:6.0\n")
    write(r, ".gatesrc", "GOH_SWIFT_MODE=spm\n" + gatesrc)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    lint = (
        "#!/bin/bash\n"
        + f'[ "$1" = version ] && {{ echo {floor("swiftlint")}; exit 0; }}\n'
        + "exit 0\n"
    )
    for name, body in (("swift", SWIFT), ("swiftlint", lint)):
        f = bin_ / name
        f.write_text(body)
        f.chmod(0o755)
    mk_xcrun_find_shim(bin_, bin_ / "swift")
    return r, bin_


def _run(tmp_path: Path, gatesrc: str = "GOH_SWIFT_COV_MIN=50\n", **extra):
    repo, bin_ = _repo(tmp_path, gatesrc)
    calls_file = tmp_path / "calls.txt"
    env = dict(os.environ, SWIFT_CALLS=str(calls_file), **extra)
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
    calls = calls_file.read_text() if calls_file.exists() else ""
    return r, calls


def test_the_coverage_run_compiles_the_package_once(tmp_path):
    r, calls = _run(tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert calls.count("COMPILE") == 1, calls
    assert "90.00%" in out, out


def test_the_run_without_a_floor_compiles_once_too(tmp_path):
    r, calls = _run(tmp_path, gatesrc="")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls.count("COMPILE") == 1, calls


def test_a_seeded_warning_still_fails_the_build_step(tmp_path):
    at = tmp_path / "pkg" / "Sources" / "Pkg" / "P.swift"
    r, _ = _run(tmp_path, WARN_AT=str(at))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "build + tests (warnings as errors) failed" in out, out
    assert "P.swift:3:5" in out, out


def test_a_seeded_coverage_drop_still_fails_the_floor(tmp_path):
    r, calls = _run(tmp_path, COVERED="10")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "10.00%" in out, out
    assert calls.count("COMPILE") == 1, calls
