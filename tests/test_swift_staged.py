"""swift_staged.sh -- swiftlint --strict at commit time, on staged .swift only (GOH.STAGEDPARITY).

Both directions: a staged finding is red, a clean staged file is green; an UNSTAGED bad file is the
push's business; a file the config excludes is green (swiftlint itself refuses "no lintable files");
a file under a subdirectory's .swiftlint.yml is judged by THAT config, as `swift_gate.sh ./ui` judges
it; and a baselined violation is tolerated while a new one is not.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, stage, write

GATE = REPO_ROOT / "gates" / "swift_staged.sh"

pytestmark = pytest.mark.skipif(shutil.which("swiftlint") is None, reason="swiftlint not on PATH")

BAD = "let value = 1 ;\n"  # trailing_semicolon
CLEAN = "let value = 1\n"


def run(repo):
    r = subprocess.run(
        ["/bin/bash", str(GATE), str(repo)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return r.returncode, r.stdout + r.stderr


def test_nothing_swift_staged_passes(repo):
    write(repo, "notes.txt", "hi\n")
    stage(repo, "notes.txt")
    rc, text = run(repo)
    assert rc == 0 and "no staged .swift files" in text, text


def test_a_staged_finding_is_red_and_a_clean_file_is_green(repo):
    write(repo, "A.swift", CLEAN)
    stage(repo, "A.swift")
    rc, text = run(repo)
    assert rc == 0, text
    write(repo, "B.swift", BAD)
    stage(repo, "B.swift")
    rc, text = run(repo)
    assert rc != 0 and "trailing_semicolon" in text and "B.swift" in text, text


def test_an_unstaged_bad_file_is_not_this_gates_business(repo):
    write(repo, "Bad.swift", BAD)
    write(repo, "Ok.swift", CLEAN)
    stage(repo, "Ok.swift")
    rc, text = run(repo)
    assert rc == 0, text


def test_a_file_the_config_excludes_is_green(repo):
    write(repo, ".swiftlint.yml", "excluded:\n  - Vendor\n")
    write(repo, "Vendor/V.swift", BAD)
    stage(repo, ".swiftlint.yml", "Vendor/V.swift")
    rc, text = run(repo)
    assert rc == 0, text  # swiftlint alone exits 1 here: "No lintable files found"


def test_a_subdirectory_config_judges_its_own_files(repo):
    write(repo, "ui/.swiftlint.yml", "disabled_rules:\n  - trailing_semicolon\n")
    write(repo, "ui/A.swift", BAD)
    stage(repo, "ui/.swiftlint.yml", "ui/A.swift")
    rc, text = run(repo)
    assert rc == 0 and "in ui" in text, text
    write(repo, "B.swift", BAD)  # the root has no such config: default rules
    stage(repo, "B.swift")
    rc, text = run(repo)
    assert rc != 0 and "B.swift" in text, text


def _baseline_of(repo, rel):
    """swiftlint's own --write-baseline for one file, as a repo records it."""
    out = repo / ".swiftlint-baseline.json"
    subprocess.run(
        ["swiftlint", "lint", "--quiet", "--write-baseline", str(out), rel],
        cwd=repo,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert json.loads(out.read_text()), "the baseline recorded nothing"


def test_a_baselined_violation_passes_and_a_new_one_does_not(repo):
    write(repo, "Old.swift", BAD)
    _baseline_of(repo, "Old.swift")
    write(repo, ".gatesrc", "GOH_SWIFT_LINT_BASELINE=.swiftlint-baseline.json\n")
    stage(repo, "Old.swift")
    rc, text = run(repo)
    assert rc == 0, text
    write(repo, "New.swift", BAD)
    stage(repo, "New.swift")
    rc, text = run(repo)
    assert rc != 0 and "NEW lint violation" in text and "New.swift" in text, text


def test_a_scoped_reconcile_judges_only_the_files_linted(tmp_path):
    """--scope: the baseline's entries for files a staged run never read did not vanish."""
    entry = {
        "violation": {"location": {"file": "Other.swift"}, "ruleIdentifier": "r", "reason": "x"}
    }
    (tmp_path / "b.json").write_text(json.dumps([entry]))
    (tmp_path / "r.json").write_text("[]")
    helper = REPO_ROOT / "gates" / "swift_lint_baseline.py"
    args = [
        sys.executable,
        str(helper),
        "--baseline",
        "b.json",
        "--report",
        "r.json",
        "--root",
        ".",
    ]

    def says(*extra):
        r = subprocess.run(
            [*args, *extra],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    assert "no longer occur" in says()
    assert "no longer occur" not in says("--scope", "Staged.swift")


def test_a_nested_config_the_root_excludes_is_not_linted(repo):
    """goh's own tests/fixtures/swift_baseline_project: a .swiftlint.yml under an excluded path."""
    write(repo, ".swiftlint.yml", "excluded:\n  - fixtures\n")
    write(repo, "fixtures/proj/.swiftlint.yml", "opt_in_rules:\n  - explicit_self\n")
    write(repo, "fixtures/proj/A.swift", BAD)
    stage(repo, ".swiftlint.yml", "fixtures/proj/.swiftlint.yml", "fixtures/proj/A.swift")
    rc, text = run(repo)
    assert rc == 0 and "excluded by ./.swiftlint.yml" in text, text
    write(repo, ".swiftlint.yml", "excluded:\n  - elsewhere\n")  # the same tree, not excluded: red
    stage(repo, ".swiftlint.yml")
    rc, text = run(repo)
    assert rc != 0 and "A.swift" in text, text
