"""swift_gate.sh — xcode-mode project resolution.

Contract: with GOH_SWIFT_PROJECT unset, a single .xcodeproj is used as
before, but MULTIPLE candidates are a named refusal listing them — the old
`ls | head -1` picked an arbitrary, locale-dependent project and just built
it.
"""

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, mk_xcrun_find_shim, write

SWIFT_GATE = REPO_ROOT / "gates" / "swift_gate.sh"


def _mk_repo(tmp_path: Path, projects: list[str], xcodebuild_says: str = "",
             swift_source: bool = True) -> Path:
    """A git repo whose PATH-shimmed swift/xcodebuild succeed end to end. `xcodebuild_says` is
    what the stub prints (the warning step reads it); `swift_source` tracks App/Main.swift, the
    first-party source the warning step judges."""
    r = tmp_path / "proj"
    r.mkdir()
    write(r, ".gatesrc", "GOH_SWIFT_MODE=xcode\nGOH_SWIFT_SCHEME=App\n")
    for p in projects:
        (r / p).mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    if swift_source:
        write(r, "App/Main.swift", "print(1)\n")
        subprocess.run(["git", "add", "App/Main.swift"], cwd=r, check=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("swift", "xcodebuild", "swiftlint"):
        f = bin_ / name
        body = f"cat <<'OUT'\n{xcodebuild_says}\nOUT\n" if name == "xcodebuild" and xcodebuild_says else ""
        f.write_text(f"#!/bin/bash\n{body}exit 0\n")
        f.chmod(0o755)
    mk_xcrun_find_shim(bin_, bin_ / "swift")
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


def test_a_warning_in_first_party_code_fails_the_gate(tmp_path):
    # Xcode mode enforced no warnings at all until 2026-09-26 (koffee_oss carried twelve).
    repo, bin_ = _mk_repo(tmp_path, ["App.xcodeproj"],
                          xcodebuild_says=f"{(tmp_path / 'proj').resolve()}/App/Main.swift:1:1: warning: something")
    r = _run(repo, bin_)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "App/Main.swift:1:1" in r.stdout + r.stderr


def test_no_first_party_swift_is_a_named_refusal(tmp_path):
    repo, bin_ = _mk_repo(tmp_path, ["App.xcodeproj"], swift_source=False)
    r = _run(repo, bin_)
    assert r.returncode != 0
    assert "GOH_SWIFT_WARN_DIRS" in r.stdout + r.stderr
