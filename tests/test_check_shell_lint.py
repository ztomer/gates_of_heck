"""checks/check_shell_lint.sh — bash -n + shellcheck --severity=error.

Bash is the most-edited language in this repo and was the only unlinted one
(Python has ruff on every run). Red-proofed both stages: a syntax error is
caught by bash -n; a syntactically VALID script with no shebang is caught by
shellcheck (SC2148) — proving the second stage runs rather than free-riding
on the first. Missing shellcheck degrades to syntax-only with a NAMED
warning (swiftlint precedent), never a silent pass.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import stage, write
from tier_kit import both_tiers, run_tiered  # noqa: F401  # both_tiers: a fixture

CHECK = "checks/check_shell_lint.sh"


pytestmark = pytest.mark.usefixtures("both_tiers")


def run_lint(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """The checker over `repo`, on the current tier (the bash checker, or `goh shell-lint`)."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return run_tiered(repo, CHECK, "shell-lint", *args, env=merged)


def test_clean_script_passes(repo):
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    stage(repo, "ok.sh")
    r = run_lint(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_syntax_error_fails_named(repo):
    # Unclosed if: bash -n rejects it before shellcheck ever runs.
    write(repo, "bad.sh", "#!/usr/bin/env bash\nif [ -n x ]; then\n")
    stage(repo, "bad.sh")
    r = run_lint(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "bad.sh" in (r.stdout + r.stderr)


def test_shellcheck_stage_catches_what_bash_n_accepts(repo):
    # Syntactically VALID (bash -n passes) but no shebang: SC2148 is
    # error-severity, so only the shellcheck stage can fail this.
    write(repo, "noshebang.sh", "echo hi\n")
    stage(repo, "noshebang.sh")
    r = run_lint(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "noshebang.sh" in (r.stdout + r.stderr)
    assert "SC2148" in (r.stdout + r.stderr)


def test_prose_starting_with_shellcheck_word_is_a_directive(repo):
    # Found 2026-09-04 by the gate itself: `# shellcheck degrades ...` in a
    # comment is parsed AS a directive (SC1072), so prose must never lead
    # with that word. Otherwise-valid script proves the stage sees it.
    write(repo, "prose.sh", "#!/usr/bin/env bash\n# shellcheck degrades gracefully\necho hi\n")
    stage(repo, "prose.sh")
    r = run_lint(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "prose.sh" in (r.stdout + r.stderr)


def test_missing_shellcheck_fails_naming_the_tool(repo):
    # It used to degrade to `bash -n` and exit 0 -- "shell lint" green having checked syntax only.
    # That was this test's assertion; it pinned the defect (tests/test_required_tools.py).
    no_sc = {"PATH": "/usr/bin:/bin"}
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    stage(repo, "ok.sh")
    r = run_lint(repo, "--staged", env=no_sc)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "shellcheck is not installed" in r.stdout + r.stderr


def test_staged_mode_reads_the_index(repo):
    # Stage the violation, then repair the worktree: the gate must report
    # the INDEX version (house index-truth rule, same as the emoji gate).
    write(repo, "race.sh", "#!/usr/bin/env bash\nif [ -n x ]; then\n")
    stage(repo, "race.sh")
    write(repo, "race.sh", "#!/usr/bin/env bash\necho fixed\n")
    r = run_lint(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "race.sh" in (r.stdout + r.stderr)


def test_exclude_skips_matching_paths(repo):
    write(repo, "vendor/bad.sh", "#!/usr/bin/env bash\nif [ -n x ]; then\n")
    stage(repo, "vendor/bad.sh")
    r = run_lint(repo, "--staged", "--exclude", "vendor/")
    assert r.returncode == 0, r.stdout + r.stderr


def test_full_mode_scans_tracked_files(repo):
    write(repo, "tool.sh", "#!/usr/bin/env bash\necho hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    r = run_lint(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_hooks_without_extension_are_linted(repo):
    # hooks/pre-commit has no .sh suffix and must still be scanned.
    os.makedirs(repo / "hooks", exist_ok=True)
    write(repo, "hooks/pre-commit", "echo hi\n")
    stage(repo, "hooks/pre-commit")
    r = run_lint(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "SC2148" in (r.stdout + r.stderr)


def test_shellcheck_binary_is_used_when_present():
    assert shutil.which("shellcheck"), "red-proof needs shellcheck on PATH"


def test_a_listing_git_refuses_is_an_error_not_zero_files(repo):
    """The class of `goh commit-class --range` reading zero commits from a git that failed: a
    corrupt index lists nothing, and a lint over nothing passed (2026-10-08)."""
    write(repo, "bad.sh", "#!/usr/bin/env bash\nif [ -n x ]; then\n")
    stage(repo, "bad.sh")
    (repo / ".git" / "index").write_bytes(b"not an index")
    for args in (("--staged",), ()):
        r = run_lint(repo, *args)
        assert r.returncode != 0, (args, r.stdout + r.stderr)


def test_a_hooks_file_in_another_language_is_not_shell(repo):
    # `hooks/` holds extensionless git hooks, which is why the whole directory is in scope. A
    # Python hook beside them (`hooks/claude/*.py`, 2026-10-10) is not shell: `bash -n` failed it on
    # its first parenthesis and shellcheck refused it (SC1071), so no Python could live there.
    write(repo, "hooks/claude/nudge.py", "#!/usr/bin/env python3\nprint(len([1]))\n")
    write(repo, "hooks/pre-commit", "#!/usr/bin/env bash\nif [ -n x ]; then\n")
    stage(repo, "hooks/claude/nudge.py", "hooks/pre-commit")
    r = run_lint(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "hooks/pre-commit" in out, out  # an extensionless hook is still judged
    assert "nudge.py" not in out, out
