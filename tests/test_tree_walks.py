"""Tree walks read the tree git can name, never the build output beside it (BACKLOG P1a).

Measured 2026-10-05: `goh.sh lints` on media_server's `mediaops-rs` took 2.4 s, 2.27 of them
system time, because `rglob("Cargo.toml")` descended 50,950 directories of `target/` (452 MB)
before filtering the results. The same walk ran in four checkers and once per crate, 29 times
per push. It was also WRONG, not only slow: a manifest a build script vendors into an ignored
directory is not part of the repository, yet a disk walk counts it.

So the class test plants exactly that: a crate, and an IGNORED build tree holding many
directories and a stray `Cargo.toml` that would change the answer if read. Every manifest
finder, in both tiers, must ignore it. The source scan below keeps new disk walks out.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "checks"))

CRATE = '[package]\nname = "real"\nversion = "0.1.0"\nedition = "2021"\n'
# A workspace with a lints policy and a member that does NOT inherit it: if this is read, the
# lints checker reports a finding, so reading it is visible in the verdict.
STRAY = '[workspace]\nmembers = ["m"]\n[workspace.lints.rust]\nunsafe_code = "forbid"\n'
STRAY_MEMBER = '[package]\nname = "m"\nversion = "0.1.0"\n[lints.rust]\nunsafe_code = "allow"\n'


def _git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text(CRATE)
    (repo / "src" / "lib.rs").write_text("")
    # `target/` is the measured cost; `.cargo-out/` is a build dir under any other name (a
    # `build.build-dir` setting, a coverage dir), where only git knows the files are not ours.
    (repo / ".gitignore").write_text("/target/\n/.cargo-out/\n")
    for vend in (
        repo / "target" / "debug" / "build" / "x-123" / "out",
        repo / ".cargo-out" / "x-123" / "out",
    ):
        (vend / "m").mkdir(parents=True)
        (vend / "Cargo.toml").write_text(STRAY)
        (vend / "m" / "Cargo.toml").write_text(STRAY_MEMBER)
    for i in range(300):
        (repo / "target" / "debug" / "deps" / f"d{i}" / "x").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    return repo


def test_dep_tree_manifests_skip_the_ignored_build_tree(planted: Path) -> None:
    from _dep_tree import manifests

    assert [p.relative_to(planted).as_posix() for p in manifests(planted)] == ["Cargo.toml"]


def test_rust_package_roots_skip_the_ignored_build_tree(planted: Path) -> None:
    from _rust_crates import rust_package_roots

    assert rust_package_roots(planted) == [planted]


@pytest.mark.parametrize("tier", ["python", "native"])
def test_lints_optin_never_reads_the_ignored_build_tree(planted: Path, tier: str, goh) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "GIT_"))}
    env["GOH_DIR"] = str(ROOT)
    if tier == "python":
        cmd = [sys.executable, str(ROOT / "checks" / "check_lints_optin.py")]
    else:
        cmd = [str(goh), "lints"]
    r = subprocess.run(cmd, cwd=planted, capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "unsafe_code" not in r.stdout + r.stderr


@pytest.mark.parametrize("finder", ["dep_tree", "rust_crates", "lints_optin"])
def test_no_finder_descends_into_the_build_tree(planted: Path, finder: str, monkeypatch) -> None:
    """Filtering `target/` out of the RESULTS is not enough: the 2.27 s of system time was the
    descent itself. Count every directory listing opened under `target/`; there must be none."""
    opened: list[str] = []
    real = os.scandir

    def counting(path=".", *a, **k):
        opened.append(os.fspath(path))
        return real(path, *a, **k)

    monkeypatch.setattr(os, "scandir", counting)
    if finder == "dep_tree":
        from _dep_tree import manifests as find
    elif finder == "rust_crates":
        from _rust_crates import rust_package_roots as find
    else:
        import check_lints_optin

        def find(root):
            return check_lints_optin.audit(root)

    find(planted)
    assert not [p for p in opened if "/target" in p], [p for p in opened if "/target" in p][:5]


def test_outside_git_the_walk_still_prunes_the_build_tree(tmp_path: Path) -> None:
    """A tree git cannot name (a fixture, a skills corpus) falls back to a walk -- one that
    PRUNES build directories rather than descending them and filtering afterwards."""
    from _gitutil import tree_files

    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "Cargo.toml").write_text(CRATE)
    (tmp_path / "target" / "x").mkdir(parents=True)
    (tmp_path / "target" / "x" / "Cargo.toml").write_text(STRAY)
    assert tree_files(str(tmp_path)) == ["a/Cargo.toml"]


# Disk walks allowed to stay, each with the reason it is not a tree walk of a repository.
WALK_ALLOWED = {
    "checks/_gitutil.py": "the fallback walk itself, pruned, for trees git cannot name",
    "checks/_empty_scope_probe.py": "fixture source text for the empty-tree probe",
    "checks/check_empty_scope.py": "builds the empty skeleton's directory shape, pruned",
    "checks/check_no_screen_presentation.py": "walks paths the CALLER named explicitly",
    "checks/check_tests_registered.py": "no shared gate runs it (BACKLOG); pruned walk",
    "gates/coverage_engines.py": "finds .xctest bundles IN the build dir, on purpose",
    "gates/coverage_markers.py": "swift coverage markers under the project's own sources",
}
WALK = re.compile(r"\brglob\(|\bos\.walk\(|glob\([^)]*\*\*")


def test_no_new_disk_walks_in_the_checkers() -> None:
    """The class gate: a new `rglob` / `os.walk` / `**` glob under checks/ or gates/ must
    either use `_gitutil.tree_files` or be added above with its reason."""
    found = set()
    for base in ("checks", "gates"):
        for path in sorted((ROOT / base).glob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if WALK.search(path.read_text(errors="replace")):
                found.add(rel)
    assert found <= set(WALK_ALLOWED), sorted(found - set(WALK_ALLOWED))
