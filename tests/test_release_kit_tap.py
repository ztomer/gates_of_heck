"""A tap bump goes through the TAP's own gate before it is pushed (BACKLOG 6.8).

Five products' release scripts pushed homebrew-tap bumps straight to GitHub: a fresh clone
installs no hooks, so the tap's gate never ran. tools/release-kit/tap_bump.sh runs the tap's
`tools/gate.sh --full` in its clone itself, and a red gate pushes nothing. release.sh's tap step
calls it, and so can any product's own release script.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT
from test_release_kit import commit_all, kit, run_release, sh, tap  # noqa: F401  (fixtures)

BUMP = REPO_ROOT / "tools" / "release-kit" / "tap_bump.sh"


def _gated(tap: tuple[Path, Path], gate: str) -> Path:
    """The tap with `gate` as its tools/gate.sh, pushed to its bare remote."""
    seed, bare = tap
    (seed / "tools").mkdir()
    (seed / "tools" / "gate.sh").write_text(gate)
    commit_all(seed, "a gate")
    sh(seed, "push", "-q", str(bare), "main")
    return bare


def _head(bare: Path) -> str:
    return sh(bare, "rev-parse", "refs/heads/main").strip()


def _artifact(kit: dict) -> Path:
    a = kit["proj"] / "artifact.tar.gz"
    a.write_bytes(b"payload-bytes")
    return a


def test_a_bump_the_taps_gate_refuses_is_never_pushed(kit, tap):
    bare = _gated(tap, '#!/usr/bin/env bash\necho "formula lint: RED" >&2\nexit 1\n')
    before = _head(bare)
    r = run_release(kit, "--tap", str(bare), "--cask", "foo", "--artifact", str(_artifact(kit)))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "formula lint: RED" in out and "nothing pushed" in out, out
    assert _head(bare) == before


def test_a_bump_the_taps_gate_passes_is_pushed_pinned(kit, tap):
    bare = _gated(tap, '#!/usr/bin/env bash\necho "tap gate ran: $1"\n')
    r = run_release(kit, "--tap", str(bare), "--cask", "foo", "--artifact", str(_artifact(kit)))
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "tap gate ran: --full" in out, out
    assert "the tap's gate passed" in out and "pushed" in out, out


def test_a_tap_without_a_gate_says_so(kit, tap):
    _, bare = tap
    r = run_release(kit, "--tap", str(bare), "--cask", "foo", "--artifact", str(_artifact(kit)))
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "has no tools/gate.sh" in out, out


def test_a_products_own_release_script_calls_the_step_directly(kit, tap):
    bare = _gated(tap, "#!/usr/bin/env bash\nexit 1\n")
    args = [
        "--tap",
        str(bare),
        "--cask",
        "foo",
        "--tag",
        "v1.2.3",
        "--artifact",
        str(_artifact(kit)),
    ]
    env = dict(os.environ, GOH_DIR=str(REPO_ROOT), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")  # fmt: skip
    r = subprocess.run(
        ["bash", str(BUMP), *args], capture_output=True, text=True, env=env, check=False
    )
    assert r.returncode != 0 and "nothing pushed" in r.stdout + r.stderr, r.stdout + r.stderr
