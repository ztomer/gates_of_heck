"""tui/lib.sh <-> gates/_common.sh integration: the styled lib must actually
engage (review finding #3 — the _lib_info gate could never be true), and the
_common.sh contract properties must hold at runtime.

Red-proof note: the sentinel-routing tests were red against HEAD because
lib.sh published no _lib_* names, so _common.sh silently used its hardcoded
plain fallbacks.
"""

from typing import Optional

import subprocess
import textwrap
from pathlib import Path

from conftest import REPO_ROOT

BASH = "/bin/bash"


def _bash(script: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", script], cwd=cwd, capture_output=True, text=True
    )


def _sandbox(tmp_path: Path) -> Path:
    """A dir whose vendored tui/ carries a SENTINEL icon in stylerc."""
    s = tmp_path / "sandbox"
    tui = s / "tui"
    tui.mkdir(parents=True)
    src_stylerc = (REPO_ROOT / "tui" / "stylerc").read_text()
    # Replace the start icon with a sentinel token the fallbacks never print.
    sent = src_stylerc.replace('ICON_START="→"', 'ICON_START="SENTINEL-A"')
    assert "SENTINEL-A" in sent
    (tui / "stylerc").write_text(sent)
    (tui / "lib.sh").write_bytes((REPO_ROOT / "tui" / "lib.sh").read_bytes())
    return s


def test_lib_sh_defines_private_aliases():
    """The contract _common.sh relies on: sourcing lib.sh must publish the
    _lib_info/_lib_ok/_lib_warn/_lib_err names."""
    r = _bash(
        f"source '{REPO_ROOT}/tui/lib.sh' && "
        "for fn in _lib_info _lib_ok _lib_warn _lib_err; do "
        'command -v "$fn" >/dev/null || { echo "missing $fn"; exit 1; }; done'
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_common_sh_routes_through_styled_lib(tmp_path):
    s = _sandbox(tmp_path)
    script = (
        f"cd '{s}' && "
        f". '{REPO_ROOT}/gates/_common.sh' && info hello"
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "SENTINEL-A" in r.stdout, (
        "_common.sh info() did not route through the styled lib — "
        f"got: {r.stdout!r}"
    )


def test_common_sh_fallbacks_when_no_lib(tmp_path):
    """Without any vendored lib, plain fallbacks still work (never undefined)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _bash(
        f"cd '{empty}' && . '{REPO_ROOT}/gates/_common.sh' && info hi && ok y && warn w",
    )
    assert r.returncode == 0
    assert "hi" in r.stdout and "y" in r.stdout


def test_die_exits_nonzero():
    """failure-path-no-op guard: die() must actually stop the script."""
    r = _bash(
        f". '{REPO_ROOT}/gates/_common.sh' && die boom; echo UNREACHABLE"
    )
    assert r.returncode != 0
    assert "UNREACHABLE" not in r.stdout
    assert "boom" in r.stderr


def test_goh_step_dumps_failing_output_and_exits(tmp_path):
    """Contract property 2: on failure the captured output is PRINTED, not
    buried. A gate that says 'failed' without the why is unactionable."""
    s = tmp_path / "sb2"
    s.mkdir()
    marker = "MARKER-OF-FAILURE-4173"
    script = textwrap.dedent(f"""
        cd '{s}'
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init demo
        goh_step "failing thing" /bin/bash -c 'echo {marker}; exit 3'
        echo STEP-UNREACHABLE
    """)
    r = _bash(script)
    assert r.returncode != 0
    assert marker in r.stderr, "failing step's output was withheld"
    assert "STEP-UNREACHABLE" not in r.stdout


def test_goh_optional_step_skips_cleanly_when_guard_absent(tmp_path):
    s = tmp_path / "sb3"
    s.mkdir()
    script = (
        f"cd '{s}' && . '{REPO_ROOT}/gates/_common.sh' && "
        'goh_init demo && goh_optional_step "opt" nope-missing.marker true '
        "&& echo SKIPPED-OK"
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "SKIPPED-OK" in r.stdout
    assert "skipped" in r.stdout.lower()


def test_no_color_disables_color_everywhere():
    for lib in ("tui/lib.sh",):
        r = _bash(f"NO_COLOR=1 source '{REPO_ROOT}/{lib}' && info x")
        assert r.returncode == 0
        assert "\x1b[" not in r.stdout, f"{lib} emitted ANSI under NO_COLOR"
