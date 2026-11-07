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
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
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
        stdin=subprocess.DEVNULL,
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


def _fake_shellcheck(tmp: Path, version: str) -> dict:
    """A `shellcheck` first on PATH that reports `version` and finds nothing: the floor is the only
    thing that can fail the clean script below."""
    bin_ = tmp / "fake-sc"
    bin_.mkdir(parents=True, exist_ok=True)
    sc = bin_ / "shellcheck"
    sc.write_text(
        "#!/bin/bash\n"
        f'[ "${{1:-}}" = --version ] && {{ printf "ShellCheck\\nversion: {version}\\n"; exit 0; }}\n'
        "exit 0\n"
    )
    sc.chmod(0o755)
    return {"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}


def test_a_shellcheck_below_its_floor_is_refused_naming_the_floor(repo, tmp_path):
    """G2c: the floor (gates/required_tools.tsv) was enforced only by `required_tools.py --check`,
    so a too-old shellcheck passed `shell_lint` itself. The gate refuses it and names the floor; a
    newer one is green (a floor, never a pin)."""
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    stage(repo, "ok.sh")
    old = run_lint(repo, "--staged", env=_fake_shellcheck(tmp_path / "old", "0.10.0"))
    assert old.returncode == 1, old.stdout + old.stderr
    assert "0.10.0" in old.stderr and "floor 0.11.0" in old.stderr, old.stdout + old.stderr
    new = run_lint(repo, "--staged", env=_fake_shellcheck(tmp_path / "new", "99.0.0"))
    assert new.returncode == 0, new.stdout + new.stderr


def test_the_gate_floor_is_the_manifest_floor():
    """One fact: the floor shell_lint.rs refuses below is the one required_tools.tsv publishes."""
    import re

    from conftest import REPO_ROOT

    rows = [
        line.split("\t")
        for line in (REPO_ROOT / "gates" / "required_tools.tsv").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    tsv = {r[5] for r in rows if r[1] == "shellcheck"}
    src = (REPO_ROOT / "crates" / "goh" / "src" / "shell_lint.rs").read_text()
    const = re.findall(r'const SHELLCHECK_FLOOR: &str = "([^"]+)";', src)
    assert const and tsv == set(const), (tsv, const)


# ---- GOH.SHSEV: the gate judges warnings, and holds the debt in a ratchet ----

WARN = "#!/usr/bin/env bash\nunused=1\necho hi\n"  # SC2034 (warning): bash -n accepts it
WARN2 = "#!/usr/bin/env bash\nunused=1\nalso_unused=2\necho hi\n"  # two SC2034
NOTE = "#!/usr/bin/env bash\necho '$HOME'\n"  # SC2016 (info)
SEED = "shell_lint_seed.json"


def _commit_all(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def test_a_planted_warning_is_red_at_staged_scope(repo):
    # The gate ran --severity=error, so an unused variable never reached any repo. SC2034 is a
    # WARNING: this is the red-first proof that the gate now reads that far.
    write(repo, "warn.sh", WARN)
    stage(repo, "warn.sh")
    r = run_lint(repo, "--staged")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "warn.sh#SC2034" in out and "SC2034" in out, out


def test_a_planted_warning_is_red_at_full_scope(repo):
    write(repo, "warn.sh", WARN)
    _commit_all(repo)
    r = run_lint(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "warn.sh#SC2034" in r.stdout + r.stderr


def test_a_seeded_warning_passes_and_the_report_says_how_many_are_held(repo):
    write(repo, "warn.sh", WARN)
    import json

    write(repo, SEED, json.dumps({"why": "t", "files": {"warn.sh#SC2034": 1}}))
    _commit_all(repo)
    r = run_lint(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 seeded below error" in r.stdout, r.stdout


def test_a_finding_over_its_seeded_count_is_red_with_its_sites(repo):
    import json

    write(repo, "warn.sh", WARN2)
    write(repo, SEED, json.dumps({"why": "t", "files": {"warn.sh#SC2034": 1}}))
    _commit_all(repo)
    r = run_lint(repo)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "seeded with 1; it has 2" in out, out


def test_a_new_code_in_a_seeded_file_is_red(repo):
    # The key carries the CODE: fixing the SC2034 must not leave room for an SC2164 in the file.
    import json

    write(repo, "mix.sh", "#!/usr/bin/env bash\nunused=1\ncd /tmp\necho hi\n")
    write(repo, SEED, json.dumps({"why": "t", "files": {"mix.sh#SC2034": 1}}))
    _commit_all(repo)
    r = run_lint(repo)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "mix.sh#SC2164" in out, out


def test_a_stale_seed_entry_fails_when_the_finding_was_fixed(repo):
    # The ratchet property: an entry whose finding is gone is red until the seed is lowered, so a
    # fixed site cannot leave headroom for a new one.
    import json

    write(repo, "warn.sh", "#!/usr/bin/env bash\necho hi\n")
    write(repo, SEED, json.dumps({"why": "t", "files": {"warn.sh#SC2034": 1}}))
    _commit_all(repo)
    r = run_lint(repo)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "warn.sh#SC2034 is seeded with 1 and has 0" in out, out
    assert "goh shell-lint --seed" in out, out


def test_a_stale_seed_entry_fails_when_the_file_is_gone(repo):
    import json

    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    write(repo, SEED, json.dumps({"why": "t", "files": {"deleted.sh#SC2034": 2}}))
    _commit_all(repo)
    r = run_lint(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "deleted.sh#SC2034 is seeded with 2 and has 0" in r.stdout + r.stderr


def test_staged_scope_ignores_entries_for_files_the_commit_does_not_bring(repo):
    import json

    write(repo, "untouched.sh", WARN)
    write(repo, SEED, json.dumps({"why": "t", "files": {"untouched.sh#SC2034": 1}}))
    _commit_all(repo)
    write(repo, "new.sh", "#!/usr/bin/env bash\necho hi\n")
    stage(repo, "new.sh")
    r = run_lint(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_error_cannot_be_seeded(repo):
    import json

    write(repo, "noshebang.sh", "echo hi\n")  # SC2148, an ERROR
    write(repo, SEED, json.dumps({"why": "t", "files": {"noshebang.sh#SC2148": 1}}))
    _commit_all(repo)
    r = run_lint(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "SC2148" in r.stdout + r.stderr


def test_seed_writes_today_and_then_the_tree_is_green(repo):
    write(repo, "warn.sh", WARN2)
    _commit_all(repo)
    seeded = run_lint(repo, "--seed")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    import json

    doc = json.loads((repo / SEED).read_text())
    assert doc["files"] == {"warn.sh#SC2034": 2}, doc
    _commit_all(repo)
    again = run_lint(repo)
    assert again.returncode == 0, again.stdout + again.stderr


def test_seed_refuses_to_write_over_an_error(repo):
    write(repo, "noshebang.sh", "echo hi\n")
    _commit_all(repo)
    r = run_lint(repo, "--seed")
    assert r.returncode == 1, r.stdout + r.stderr
    assert not (repo / SEED).exists()


def test_a_corrupt_seed_is_an_error_never_an_empty_seed(repo):
    write(repo, "warn.sh", WARN)
    write(repo, SEED, "{ not json")
    _commit_all(repo)
    r = run_lint(repo)
    # == 2 and "not JSON", as test_fixed_sleep's sibling: `!= 0` also passed a corrupt seed read
    # as EMPTY, whose unseeded SC2034 is exit 1 (GOH.REV2 finding 4).
    assert r.returncode == 2 and "not JSON" in r.stderr, r.stdout + r.stderr
    assert SEED in r.stderr


def test_a_corrupt_seed_is_an_error_even_with_no_findings(repo):
    """The seed is read even when the tree is clean -- else the corrupt file is only found the
    day a finding appears."""
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    write(repo, SEED, "{ not json")
    _commit_all(repo)
    r = run_lint(repo)
    assert r.returncode == 2 and "not JSON" in r.stderr, r.stdout + r.stderr


def test_a_seed_whose_files_are_all_gone_is_stale(repo):
    """Finding 1: with no shell file left in scope the gate returned OK before the seed was read,
    so an entry for a deleted script never failed stale."""
    write(repo, "warn.sh", WARN)
    _commit_all(repo)
    assert run_lint(repo, "--seed").returncode == 0
    (repo / "warn.sh").unlink()
    _commit_all(repo)
    r = run_lint(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "warn.sh#SC2034" in r.stdout + r.stderr


def test_a_seed_whose_files_are_all_excluded_is_stale(repo):
    write(repo, "warn.sh", WARN)
    _commit_all(repo)
    assert run_lint(repo, "--seed").returncode == 0
    _commit_all(repo)
    r = run_lint(repo, "--exclude", "warn")
    assert r.returncode == 1, r.stdout + r.stderr


def test_a_floor_is_judged_when_no_shell_file_is_in_scope(repo):
    """Finding 1: GOH_SHELL_LINT_MIN_FILES was never judged over an empty scope -- exactly the
    scan that stopped reading its subject the floor exists to catch."""
    write(repo, ".gatesrc", "GOH_SHELL_LINT_MIN_FILES=1\n")
    write(repo, "a.py", "x = 1\n")
    _commit_all(repo)
    r = run_lint(repo)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "GOH_SHELL_LINT_MIN_FILES" in r.stdout + r.stderr


def test_a_malformed_gatesrc_is_exit_2_naming_the_key(repo):
    """Finding 2: `.ok()` read a malformed .gatesrc as 'no floor'."""
    write(repo, ".gatesrc", "GOH_SHELL_LINT_MIN_FILES=lots\n")
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    _commit_all(repo)
    r = run_lint(repo)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "GOH_SHELL_LINT_MIN_FILES" in r.stderr


def test_staged_seed_is_refused_naming_why(repo):
    """Finding 3: `--staged --seed` wrote the repo-wide seed from the staged subset, dropping
    every entry for a file the commit does not touch. Refused, exit 2, and nothing written."""
    write(repo, "warn.sh", WARN)
    _commit_all(repo)
    write(repo, "other.sh", WARN2)
    stage(repo, "other.sh")
    r = run_lint(repo, "--staged", "--seed")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--staged" in r.stderr and "--seed" in r.stderr
    assert not (repo / SEED).exists()


def test_severity_error_is_a_written_opt_out(repo):
    write(repo, ".gatesrc", "GOH_SHELL_LINT_SEVERITY=error\n")
    write(repo, "warn.sh", WARN)
    stage(repo, "warn.sh")
    r = run_lint(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_severity_info_judges_notes_too(repo):
    write(repo, "note.sh", NOTE)
    stage(repo, "note.sh")
    assert run_lint(repo, "--staged").returncode == 0  # an info is below the default
    write(repo, ".gatesrc", "GOH_SHELL_LINT_SEVERITY=info\n")
    r = run_lint(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "note.sh#SC2016" in r.stdout + r.stderr


def test_a_severity_shellcheck_has_no_such_level_for_is_refused_naming_the_key(repo):
    write(repo, ".gatesrc", "GOH_SHELL_LINT_SEVERITY=warn\n")
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    stage(repo, "ok.sh")
    r = run_lint(repo, "--staged")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "GOH_SHELL_LINT_SEVERITY" in r.stdout + r.stderr


def test_the_verdict_cache_is_keyed_on_the_severity(repo, tmp_path):
    # A clean verdict recorded at `error` must not answer for `warning`: same file, same bytes.
    env = dict(os.environ, GOH_VERDICT_DIR=str(tmp_path / "cache"))
    write(repo, ".gatesrc", "GOH_SHELL_LINT_SEVERITY=error\n")
    write(repo, "warn.sh", WARN)
    stage(repo, "warn.sh")
    assert run_lint(repo, "--staged", env=env).returncode == 0
    write(repo, ".gatesrc", "GOH_SHELL_LINT_SEVERITY=warning\n")
    assert run_lint(repo, "--staged", env=env).returncode == 1


def test_a_files_findings_do_not_depend_on_which_files_share_the_run(repo):
    # shellcheck given several files follows a `source` of another file in the batch, so a.sh read
    # SC2128 beside b.sh and SC2154 alone: the full tree and a staged subset disagreed, and a seed
    # that must be exact (a stale entry fails) flapped between them. One shellcheck per file.
    write(repo, "b.sh", "#!/usr/bin/env bash\narr=(1 2)\n")
    write(repo, "a.sh", "#!/usr/bin/env bash\n# shellcheck source=b.sh\n. ./b.sh\necho $arr\n")
    _commit_all(repo)
    both = run_lint(repo)
    alone = run_lint(repo, "--exclude", r"^b\.sh$")
    for r in (both, alone):
        out = r.stdout + r.stderr
        assert "a.sh#SC2154" in out and "SC2128" not in out, out
