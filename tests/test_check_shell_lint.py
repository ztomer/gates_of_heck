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

from conftest import REPO_ROOT, stage, write

CHECK = "checks/check_shell_lint.sh"


def run_lint(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / CHECK), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=merged,
    )


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
    write(repo, "prose.sh",
          "#!/usr/bin/env bash\n# shellcheck degrades gracefully\necho hi\n")
    stage(repo, "prose.sh")
    r = run_lint(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "prose.sh" in (r.stdout + r.stderr)


def test_missing_shellcheck_warns_and_checks_syntax_only(repo):
    # shellcheck lives outside /usr/bin:/bin on this machine; stripping PATH
    # proves the degrade path. Syntax errors must STILL fail.
    no_sc = {"PATH": "/usr/bin:/bin"}
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    stage(repo, "ok.sh")
    r = run_lint(repo, "--staged", env=no_sc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "shellcheck" in (r.stdout + r.stderr).lower()

    write(repo, "bad.sh", "#!/usr/bin/env bash\nif [ -n x ]; then\n")
    stage(repo, "bad.sh")
    r = run_lint(repo, "--staged", env=no_sc)
    assert r.returncode == 1, r.stdout + r.stderr


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
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "fixture"],
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
