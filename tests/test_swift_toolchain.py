"""swift_toolchain.sh — which `swift` a house script builds with.

Contract: GOH_SWIFT wins; else `xcrun --find swift` (Xcode's toolchain, the
one with the platform SDK); else PATH; else a refusal naming every rung.
PATH ORDER NEVER DECIDES when xcrun answers — a swiftly toolchain ahead of
Xcode's on PATH is exactly the incident this file exists for.
"""

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

TOOLCHAIN = REPO_ROOT / "gates" / "swift_toolchain.sh"


def _exe(path: Path, body: str = "#!/bin/bash\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _resolve(bin_dir: Path, **extra_env) -> subprocess.CompletedProcess:
    # PATH is the shim dir ALONE: /usr/bin carries a real swift on macOS,
    # which would turn the "nothing anywhere" case into a pass.
    env = {k: v for k, v in os.environ.items() if k != "GOH_SWIFT"}
    env["PATH"] = str(bin_dir)
    env.update(extra_env)
    script = f'. "{TOOLCHAIN}"; goh_swift_resolve; printf "%s|%s\\n" "$SWIFT" "$SWIFT_ORIGIN"'
    return subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, env=env)


def _xcrun_shim(bin_dir: Path, answers: Path | None) -> None:
    body = ("#!/bin/bash\n"
            + (f'[ "$1 $2" = "--find swift" ] && {{ echo "{answers}"; exit 0; }}\n' if answers else "")
            + "exit 1\n")
    _exe(bin_dir / "xcrun", body)


def test_xcrun_beats_the_swift_first_on_path(tmp_path):
    """The class: swiftly on PATH must not shadow Xcode's toolchain."""
    bin_ = tmp_path / "bin"
    shadow = _exe(bin_ / "swift")                       # first on PATH — the swiftly stand-in
    xcode = _exe(tmp_path / "xcode" / "usr" / "bin" / "swift")
    _xcrun_shim(bin_, xcode)
    r = _resolve(bin_)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"{xcode}|xcrun", (
        f"PATH's swift ({shadow}) won over xcrun's: {r.stdout!r}")


def test_goh_swift_override_wins_over_xcrun(tmp_path):
    bin_ = tmp_path / "bin"
    _exe(bin_ / "swift")
    _xcrun_shim(bin_, _exe(tmp_path / "xcode" / "swift"))
    mine = _exe(tmp_path / "mine" / "swift")
    r = _resolve(bin_, GOH_SWIFT=str(mine))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"{mine}|override"


def test_goh_swift_that_is_not_executable_is_refused(tmp_path):
    bin_ = tmp_path / "bin"
    _exe(bin_ / "swift")
    r = _resolve(bin_, GOH_SWIFT=str(tmp_path / "nowhere" / "swift"))
    assert r.returncode != 0
    assert "GOH_SWIFT names no executable" in r.stderr


def test_path_is_the_fallback_when_xcrun_cannot_find_swift(tmp_path):
    """Linux, or a fake xcrun that knows nothing (the coverage fixtures)."""
    bin_ = tmp_path / "bin"
    on_path = _exe(bin_ / "swift")
    _xcrun_shim(bin_, None)
    r = _resolve(bin_)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"{on_path}|path"


def test_nothing_anywhere_refuses_naming_every_rung(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _xcrun_shim(bin_, None)
    r = _resolve(bin_)
    assert r.returncode != 0
    for rung in ("GOH_SWIFT", "xcrun --find swift", "PATH"):
        assert rung in r.stderr, r.stderr
