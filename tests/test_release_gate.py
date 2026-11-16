"""lib/release_gate.py: a release land re-runs only the checks that read the release files.

GOH.SHIPRELREUSE R3. The release commit differs from the land-gated tip only in CHANGELOG.md and
the changelog/ fragments it folds; its land runs the structural gate and the tests reading them.
Calibrated both ways: one non-release file in the diff forces the full gate, and so do an empty diff
and a base that is no ancestor; the probe finds the real readers and not a test that reads neither.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

LIB = REPO_ROOT / "lib" / "release_gate.py"
KIT = REPO_ROOT / "tools" / "release_goh.sh"
sys.path.insert(0, str(REPO_ROOT / "lib"))
import release_gate  # noqa: E402


def _commit(repo: Path, msg: str = "x") -> str:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


def _fixture(repo: Path) -> str:
    (repo / "tests").mkdir()
    (repo / "tests" / "test_reads.py").write_text('TEXT = (ROOT / "CHANGELOG.md").read_text()\n')
    (repo / "tests" / "test_other.py").write_text("def test_x():\n    assert True\n")
    (repo / "CHANGELOG.md").write_text("# CHANGELOG\n\n## Unreleased\n")
    (repo / "changelog").mkdir()
    (repo / "changelog" / "X.md").write_text("* **X.** x\n")
    (repo / "src.py").write_text("A = 1\n")
    return _commit(repo, "base")


def _run(repo: Path, base: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log = repo.parent / "ran.txt"
    log.unlink(missing_ok=True)
    r = subprocess.run(
        [sys.executable, str(LIB), "run", "--root", str(repo), "--base", base,
         "--full", f'echo full >>"{log}"', "--structural", f'echo structural >>"{log}"',
         "--runner", f'echo pytest >>"{log}"; echo'],
        env=hermetic_env(drop_git=True), capture_output=True, text=True,
        timeout=patience(60), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    return r, log.read_text().split() if log.exists() else []


def test_a_release_only_diff_runs_the_structural_gate_and_the_named_set(repo: Path) -> None:
    base = _fixture(repo)
    (repo / "CHANGELOG.md").write_text("# CHANGELOG\n\n## v0.2.0\n\n* **X.** x\n")
    (repo / "changelog" / "X.md").unlink()
    _commit(repo, "release: v0.2.0")
    assert release_gate.decide(repo, base) == "release"
    r, ran = _run(repo, base)
    assert r.returncode == 0, r.stdout + r.stderr
    assert ran == ["structural", "pytest"], (ran, r.stdout + r.stderr)
    assert "tests/test_reads.py" in r.stdout and "test_other" not in r.stdout, r.stdout


def test_one_non_release_file_in_the_diff_forces_the_full_gate(repo: Path) -> None:
    base = _fixture(repo)
    (repo / "CHANGELOG.md").write_text("# CHANGELOG\n\n## v0.2.0\n\n* **X.** x\n")
    (repo / "src.py").write_text("A = 2\n")
    _commit(repo, "release: v0.2.0, and a source edit with it")
    assert release_gate.decide(repo, base) == "full"
    r, ran = _run(repo, base)
    assert ran == ["full"], (ran, r.stdout + r.stderr)


def test_an_empty_diff_or_a_base_that_is_no_ancestor_runs_the_full_gate(repo: Path) -> None:
    base = _fixture(repo)
    assert release_gate.decide(repo, base) == "full"
    (repo / "CHANGELOG.md").write_text("# CHANGELOG\n\n## v0.2.0\n")
    _commit(repo, "release")
    git(repo, "checkout", "-q", "--detach", base)
    (repo / "CHANGELOG.md").write_text("# CHANGELOG\n\n## other\n")
    elsewhere = _commit(repo, "a sibling, so the release is no descendant of it")
    git(repo, "checkout", "-q", "-")
    assert release_gate.decide(repo, elsewhere) == "full"


def test_the_full_gates_verdict_is_the_release_gates_and_it_never_sees_the_key(repo: Path) -> None:
    """GOH_LAND_GATE chose this gate; in the gate's environment it would be one more GOH_* in every
    proven step's cache identity, and the push after the land would prove the tree again."""
    base = _fixture(repo)
    (repo / "src.py").write_text("A = 3\n")
    _commit(repo)
    r = subprocess.run(
        [sys.executable, str(LIB), "run", "--root", str(repo), "--base", base,
         "--full", 'test -z "${GOH_LAND_GATE+set}" && exit 7; exit 8'],
        env=hermetic_env(drop_git=True, GOH_LAND_GATE="x"), capture_output=True, text=True,
        timeout=patience(60), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.returncode == 7, r.stdout + r.stderr


def test_the_probe_names_the_real_readers_and_nothing_else() -> None:
    found = release_gate.names(REPO_ROOT)
    for reader in ("test_changelog_fragments.py", "test_changelog_cut.py", "test_release_goh.py",
                   "test_check_tag_version_changelog.py", "test_release_gate.py"):  # fmt: skip
        assert f"tests/{reader}" in found, found
    assert "tests/test_wiring.py" not in found, found
    assert len(found) < 40, f"the probe reads more than the release files' readers: {found}"


def test_the_release_kit_hands_its_land_this_gate_with_the_tip_it_cut_from(repo: Path) -> None:
    seen = repo.parent / "land-gate.txt"
    tools = repo / "tools"
    tools.mkdir()
    (tools / "land.sh").write_text(f'#!/bin/bash\nprintf %s "$GOH_LAND_GATE" >"{seen}"\nexit 1\n')
    (tools / "land.sh").chmod(0o755)
    (repo / "CHANGELOG.md").write_text("# CHANGELOG\n\n## Unreleased\n\n* **Two.** b\n")
    tip = _commit(repo, "feat: two")
    import loaded_verdict  # the tip's loaded verdict (R2), green: this test is about the land

    loaded_verdict.write(loaded_verdict.record_path(repo, "HEAD"), {"state": "green", "ended": 0})
    r = subprocess.run(["bash", str(KIT), "0.2.0"], cwd=repo, env=hermetic_env(drop_git=True),
                       capture_output=True, text=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert seen.is_file(), r.stdout + r.stderr
    gate = seen.read_text()
    assert "lib/release_gate.py" in gate and f"--base {tip}" in gate, gate
    assert "gate_profile.sh" in gate, f"the full gate it falls back to is land's own: {gate}"
