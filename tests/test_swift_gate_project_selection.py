"""swift_gate.sh — xcode-mode project resolution.

Contract: with GOH_SWIFT_PROJECT unset, a single .xcodeproj is used as
before, but MULTIPLE candidates are a named refusal listing them — the old
`ls | head -1` picked an arbitrary, locale-dependent project and just built
it.
"""

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, write

SWIFT_GATE = REPO_ROOT / "gates" / "swift_gate.sh"


def _mk_repo(tmp_path: Path, projects: list[str]) -> Path:
    """A repo whose PATH-shimmed swift/xcodebuild succeed end to end."""
    r = tmp_path / "proj"
    r.mkdir()
    write(r, ".gatesrc", "GOH_SWIFT_MODE=xcode\nGOH_SWIFT_SCHEME=App\n")
    for p in projects:
        (r / p).mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("swift", "xcodebuild", "swiftlint"):
        f = bin_ / name
        f.write_text("#!/bin/bash\nexit 0\n")
        f.chmod(0o755)
    return r, bin_


def _run(repo: Path, bin_dir: Path):
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        ["/bin/bash", str(SWIFT_GATE), str(repo)],
        cwd=repo, capture_output=True, text=True, env=env,
    )


def test_single_project_still_selected_unchanged(tmp_path):
    repo, bin_ = _mk_repo(tmp_path, ["App.xcodeproj"])
    r = _run(repo, bin_)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all swift gates passed" in r.stdout


def test_multiple_projects_refuse_naming_candidates(tmp_path):
    repo, bin_ = _mk_repo(tmp_path, ["App.xcodeproj", "Bpp.xcodeproj"])
    r = _run(repo, bin_)
    assert r.returncode != 0, (
        f"two .xcodeproj silently built one of them: {r.stdout!r}")
    combined = r.stdout + r.stderr
    assert "multiple .xcodeproj" in combined
    assert "App.xcodeproj" in combined and "Bpp.xcodeproj" in combined, (
        "the candidates must be named")
    assert "GOH_SWIFT_PROJECT" in combined


def test_explicit_project_beats_ambiguity(tmp_path):
    repo, bin_ = _mk_repo(tmp_path, ["App.xcodeproj", "Bpp.xcodeproj"])
    write(repo, ".gatesrc",
          "GOH_SWIFT_MODE=xcode\nGOH_SWIFT_SCHEME=App\n"
          "GOH_SWIFT_PROJECT=App.xcodeproj\n")
    r = _run(repo, bin_)
    assert r.returncode == 0, r.stdout + r.stderr
