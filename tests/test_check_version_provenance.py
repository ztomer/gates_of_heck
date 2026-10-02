"""check_version_provenance.py — a shipped `--version` must carry its build.

The defect class: a version string that cannot distinguish two different
binaries. A number answers "is this current?"; only a commit answers "what am
I actually running?" -- and the gap between the two is invisible in exactly
the situation that matters, an installed copy lagging the source with no
outward sign, because the build lands in a shared target directory that
nothing refreshes.

Every case here is a property, not an instance. A commit alone is not
provenance (app_updates' build.rs documents the incident: a freshly installed
binary printed `1c957b278d0b`, was judged stale, and was not -- HEAD cannot
see uncommitted work, so a dirty build quoted the previous commit's hash and
was byte-identical to a clean build of it). So the checks below are: a commit,
a date, AND something that sees the working tree. The last one is the half
that was silently optional, and it is what the `+dirty` marker and the
`git write-tree` field in the contract exist to carry.
"""

import json
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, commit_all, git, run_check, write

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_version_provenance as gate  # noqa: E402

CHECKER = "checks/check_version_provenance.py"

# A build script that derives all three, in the honest spellings. This is the
# shape app_updates' crates/cli/build.rs has, reduced to what the check reads.
GOOD_BUILD = """\
fn main() {
    println!("cargo:rustc-env=BUILD_GIT_HASH={}", git(&["rev-parse", "HEAD"]));
    println!("cargo:rustc-env=BUILD_DATE={}", date());
    println!("cargo:rustc-env=APP_TREE={}", git(&["write-tree"]));
}
"""


def _package(repo, version="1.2.3", build=None, main=None, lib=None):
    write(repo, "Cargo.toml", f'[package]\nname = "app"\nversion = "{version}"\n')
    if build is not None:
        write(repo, "build.rs", build)
    if main is not None:
        write(repo, "src/main.rs", main)
    if lib is not None:
        write(repo, "src/lib.rs", lib)


def _patch_env() -> dict:
    """The patched copy sits in tmp, so it cannot find `_gitutil` beside itself.

    PYTHONPATH rather than a second copy of the lib: a vendored duplicate in a
    calibration fixture is one more thing that can drift from the real one, and
    this test's whole job is to prove what the REAL checker does.
    """
    import os

    return dict(os.environ, PYTHONPATH=str(REPO_ROOT / "checks"))


def _cli(version_line, extra=""):
    return (
        "use clap::Parser;\n"
        "#[derive(Parser)]\n"
        f'#[command(name = "app", {version_line}{extra})]\n'
        "struct Args;\n"
        "fn main() { let _ = Args::parse(); }\n"
    )


# ── the defect, both ways ─────────────────────────────────────────────────────


def test_a_version_with_no_commit_or_date_is_red(repo):
    """The plain case: a binary that says only what version it is."""
    _package(repo, build=GOOD_BUILD, main=_cli("version,"))
    r = run_check(repo, CHECKER)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "commit hash" in r.stdout and "build date" in r.stdout, r.stdout


def test_a_compliant_binary_is_green(repo):
    _package(
        repo,
        build=GOOD_BUILD,
        main=_cli(
            "version,", ' long_version = concat!(env!("BUILD_GIT_HASH"), env!("BUILD_DATE"))'
        ),
    )
    r = run_check(repo, CHECKER)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 version-declaring source file(s)" in r.stdout, r.stdout


def test_a_binary_that_declares_no_version_is_not_our_business(repo):
    """Population is the files that DECLARE a version. A binary with no
    `version` in its clap attribute is not a finding -- and is not counted as
    population either, or `examined` would claim coverage it has not got."""
    _package(repo, build=GOOD_BUILD, main="fn main() {}\n")
    r = run_check(repo, CHECKER)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no version-declaring source files" in r.stdout, r.stdout


def test_no_rust_at_all_is_a_named_non_run(repo):
    """Zero examined files is BLIND, not clean -- so it says so and says it is
    not applicable. A silent 0 here is indistinguishable from having checked
    every file and found nothing wrong."""
    write(repo, "README.md", "# docs\n")
    r = run_check(repo, CHECKER)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "nothing examined" in r.stdout and "not applicable" in r.stdout, r.stdout


# ── a workspace, and the file the checker used to skip ───────────────────────


def test_a_workspace_crate_is_reached(repo):
    """The first version looked only at `<root>/src/main.rs` and found nothing
    in either real repository: both are WORKSPACES. A gate that never reaches
    the code reports compliance over zero files."""
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/cli"]\n')
    write(repo, "crates/cli/Cargo.toml", '[package]\nname = "cli"\nversion = "1.0.0"\n')
    write(repo, "crates/cli/build.rs", GOOD_BUILD)
    write(repo, "crates/cli/src/main.rs", _cli("version,"))
    r = run_check(repo, CHECKER)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "crates/cli/src/main.rs" in r.stdout, r.stdout


def test_a_version_declared_in_lib_rs_is_reached(repo):
    """THE widening. A clap derive on a struct in the library, with a thin
    `main.rs` calling `parse()`, is the ordinary way to write this -- and it is
    how app_updates does it (`crates/cli/src/lib.rs` declares `long_version`,
    `crates/cli/src/main.rs` is 20 lines of `parse()` and a call).

    The gap was invisible for the worst possible reason: the declaring file was
    the one file the checker never read, and the baseline listed it anyway, so
    the entry suppressed nothing while looking like it suppressed something.
    """
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/cli"]\n')
    write(
        repo,
        "crates/cli/Cargo.toml",
        '[package]\nname = "cli"\nversion = "1.0.0"\n\n[[bin]]\nname = "app"\n',
    )
    write(repo, "crates/cli/build.rs", GOOD_BUILD)
    write(repo, "crates/cli/src/main.rs", "fn main() { app_updates_cli::run(); }\n")
    write(repo, "crates/cli/src/lib.rs", _cli('long_version = concat!(env!("BUILD_GIT_HASH"),),'))
    r = run_check(repo, CHECKER)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "crates/cli/src/lib.rs" in r.stdout, (
        "src/lib.rs was not widened into scope; this is the app_updates shape"
    )


def test_a_library_only_crate_is_skipped(repo):
    """A crate with no binary has no `--version` flag. Widening to `lib.rs`
    must not turn every library in the estate into a finding."""
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/core"]\n')
    write(repo, "crates/core/Cargo.toml", '[package]\nname = "core"\nversion = "1.0.0"\n')
    write(repo, "crates/core/src/lib.rs", _cli("version,"))
    r = run_check(repo, CHECKER)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not applicable" in r.stdout, r.stdout


def test_a_binary_in_src_bin_is_reached(repo):
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/backup"]\n')
    write(repo, "crates/backup/Cargo.toml", '[package]\nname = "backup"\nversion = "1.0.0"\n')
    write(repo, "crates/backup/build.rs", GOOD_BUILD)
    write(repo, "crates/backup/src/lib.rs", "pub fn clean() {}\n")
    write(repo, "crates/backup/src/bin/back-dl.rs", _cli("version,"))
    r = run_check(repo, CHECKER)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "crates/backup/src/bin/back-dl.rs" in r.stdout, r.stdout


# ── a claim with nothing behind it ───────────────────────────────────────────


def test_a_referenced_field_no_build_script_sets_is_red(repo):
    """The name is a claim; something has to make it true."""
    _package(repo, main=_cli('long_version = concat!(env!("BUILD_GIT_HASH"), env!("BUILD_DATE")),'))
    r = run_check(repo, CHECKER)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no build script sets it" in r.stdout, r.stdout


def test_a_library_with_a_binary_is_still_in_scope_for_the_script(repo):
    """`build.rs` lives at the PACKAGE root, next to `src/`, not inside it."""
    _package(
        repo,
        build=GOOD_BUILD,
        main=_cli('long_version = concat!(env!("BUILD_GIT_HASH"), env!("BUILD_DATE")),'),
    )
    assert run_check(repo, CHECKER).returncode == 0, "build.rs beside src/ was not found"


def test_the_build_script_is_found_in_the_crates_own_package(repo):
    """THE attribution bug this suite found, and it was invisible until the
    scope widened.

    The repository root is itself a package root (it has a Cargo.toml), the
    roots are sorted, and `path.is_relative_to(cand)` is true for the root on
    every workspace member -- so `build.rs` was looked for at the workspace top
    and every finding came out "no build script sets it": true, and about the
    wrong directory.

    It stayed invisible because the only two files reaching that code path in
    the one real consumer sat in its baseline, which skips them BEFORE the
    lookup. A finding suppressed upstream hides the defect downstream, and the
    widening is what exposed it.
    """
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/cli"]\n\n[workspace.package]\nversion = "1.0.0"\n',
    )
    write(repo, "crates/cli/Cargo.toml", '[package]\nname = "cli"\nversion.workspace = true\n')
    write(repo, "crates/cli/build.rs", GOOD_BUILD)
    write(
        repo,
        "crates/cli/src/main.rs",
        _cli('long_version = concat!(env!("BUILD_GIT_HASH"), env!("BUILD_DATE")),'),
    )
    r = run_check(repo, CHECKER)
    assert r.returncode == 0, (
        "the workspace's OWN build script was not found for a member crate:\n" + r.stdout
    )


# ── an OPAQUE clause: the name claims nothing, so the script must ────────────


def test_an_opaque_clause_field_set_from_all_three_is_green(repo):
    """`env!("APP_UPDATES_PROVENANCE")` is app_updates' live spelling: one
    field holding a clause the build script composes. The name asserts nothing,
    so acceptance is earned by the SCRIPT -- and this is the case that keeps the
    widened scope from inventing findings about the one real consumer."""
    _package(
        repo,
        build=GOOD_BUILD.replace("BUILD_GIT_HASH", "APP_UPDATES_PROVENANCE"),
        main=_cli(
            'long_version = concat!(env!("CARGO_PKG_VERSION"), env!("APP_UPDATES_PROVENANCE")),'
        ),
    )
    r = run_check(repo, CHECKER)
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_opaque_clause_set_without_a_commit_is_red(repo):
    _package(
        repo,
        build='println!("cargo:rustc-env=APP_UPDATES_PROVENANCE={}", date());\n',
        main=_cli('long_version = env!("APP_UPDATES_PROVENANCE"),'),
    )
    r = run_check(repo, CHECKER)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "never derives a commit" in r.stdout, r.stdout


def test_an_opaque_clause_that_cannot_see_the_working_tree_is_red(repo):
    """THE half that was silently optional, and the defect app_updates actually
    shipped: quoting `rev-parse HEAD` and a date, and never asking whether the
    tree is dirty, produces a version string byte-identical to a clean build of
    the same commit. A freshly installed binary printed the previous commit's
    hash, was judged stale, and was not.
    """
    _package(
        repo,
        build=(
            'println!("cargo:rustc-env=APP_UPDATES_PROVENANCE={}", git(&["rev-parse", "HEAD"]));\n'
            'println!("cargo:rustc-env=APP_UPDATES_PROVENANCE={}", date());\n'
        ),
        main=_cli('long_version = env!("APP_UPDATES_PROVENANCE"),'),
    )
    r = run_check(repo, CHECKER)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the working tree's state" in r.stdout, r.stdout


def test_an_opaque_clause_with_no_build_script_at_all_is_red(repo):
    _package(repo, main=_cli('long_version = env!("APP_UPDATES_PROVENANCE"),'))
    r = run_check(repo, CHECKER)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no build script sets it" in r.stdout, r.stdout


# ── the baseline is a ratchet, and a dead entry is a finding ────────────────


def test_a_baseline_entry_suppresses_a_finding_but_not_the_population(repo, tmp_path):
    """It suppresses FINDINGS, not population: a repo that has ratcheted its
    last offender still HAS a declaring file, and counting it as nothing
    examined makes a wound-down repo report itself not applicable."""
    _package(repo, build=GOOD_BUILD, main=_cli("version,"))
    baseline = write(repo, ".gates-version-baseline.json", json.dumps(["src/main.rs"]))
    r = run_check(repo, CHECKER, "--baseline", str(baseline))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all 1 version-declaring source file(s)" in r.stdout, r.stdout


def test_a_baseline_entry_naming_a_file_the_checker_never_reads_is_red(repo, tmp_path):
    """The app_updates entry, reproduced. `crates/cli/src/lib.rs` sat in that
    repo's baseline while the checker only read `src/main.rs` and
    `src/bin/*.rs` -- so the entry suppressed nothing, and read as debt paid.

    Reported, it becomes a shrinking chore. Ignored, it is a list of things
    somebody believes this gate is checking.
    """
    _package(repo, build=GOOD_BUILD, main=_cli("version,"))
    baseline = write(
        repo, ".gates-version-baseline.json", json.dumps(["src/main.rs", "src/lib.rs"])
    )
    r = run_check(repo, CHECKER, "--baseline", str(baseline))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "src/lib.rs" in r.stdout and "suppresses nothing" in r.stdout, r.stdout
    # ...and the LIVE entry still suppresses: the report is about the dead one.
    assert "src/main.rs:" not in r.stdout, r.stdout


def test_an_unreadable_baseline_is_a_usage_error_not_a_pass(repo):
    _package(repo, build=GOOD_BUILD, main=_cli("version,"))
    bad = write(repo, "bad.json", "not json at all")
    r = run_check(repo, CHECKER, "--baseline", str(bad))
    assert r.returncode == 2, r.stdout + r.stderr


def test_a_missing_directory_is_a_usage_error(repo):
    assert run_check(repo, CHECKER, str(repo / "nope")).returncode == 2


# ── staged scope is INDEX scope (contract #3) ───────────────────────────────


def test_staged_judges_the_commit_not_the_worktree(repo):
    """The class contract #3 exists for: a file staged clean then dirtied in
    the editor must not block an innocent commit, and a dirty file unstaged
    away must not slip one through."""
    _package(repo, build=GOOD_BUILD, main=_cli("version,"))
    git(repo, "add", "-A")
    r = run_check(repo, CHECKER, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "src/main.rs" in r.stdout, r.stdout

    write(
        repo,
        "src/main.rs",
        _cli('long_version = concat!(env!("BUILD_GIT_HASH"), env!("BUILD_DATE")),'),
    )
    git(repo, "add", "-A")
    assert run_check(repo, CHECKER, "--staged").returncode == 0, (
        "a clean fix staged over a violation still failed: the worktree was read"
    )

    # ...and the reverse: dirtying the worktree without staging it must not
    # invent a finding about the commit being made.
    write(repo, "src/main.rs", _cli("version,"))
    assert run_check(repo, CHECKER, "--staged").returncode == 0, (
        "an unstaged edit blocked a clean commit"
    )


def test_staged_narrows_to_the_files_being_committed(repo):
    """A pre-commit hook must judge the commit being made: an unrelated
    unstaged violation elsewhere in the tree cannot block it."""
    _package(repo, build=GOOD_BUILD, main=_cli("version,"))
    other = repo / "other"
    write(other, "Cargo.toml", '[package]\nname = "other"\nversion = "1.0.0"\n')
    write(other, "src/main.rs", _cli("version,"))
    commit_all(repo)
    git(repo, "rm", "-q", "--cached", "src/main.rs")
    git(repo, "commit", "-qm", "drop", "--allow-empty")
    assert run_check(repo, CHECKER, "--staged").returncode == 0, (
        "an unstaged violation in an unrelated file blocked a clean commit"
    )


# ── calibration: the tests above can go RED ─────────────────────────────────


def test_the_suite_is_red_when_the_checker_stops_reaching_lib_rs(tmp_path):
    """Proof the `lib.rs` case is load-bearing. Narrowing MAIN_FILES back to
    `("src/main.rs",)` -- the state this repo shipped -- must turn it red."""
    source = (REPO_ROOT / CHECKER).read_text(encoding="utf-8")
    patched = tmp_path / "checker.py"
    patched.write_text(
        source.replace(
            'MAIN_FILES = ("src/main.rs", "src/lib.rs")', 'MAIN_FILES = ("src/main.rs",)'
        ),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/cli"]\n')
    write(
        repo,
        "crates/cli/Cargo.toml",
        '[package]\nname = "cli"\nversion = "1.0.0"\n\n[[bin]]\nname = "app"\n',
    )
    write(repo, "crates/cli/build.rs", GOOD_BUILD)
    write(repo, "crates/cli/src/main.rs", "fn main() { app_updates_cli::run(); }\n")
    write(repo, "crates/cli/src/lib.rs", _cli('long_version = concat!(env!("BUILD_GIT_HASH"),),'))
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo)

    r = subprocess.run(
        [sys.executable, str(patched)], cwd=repo, capture_output=True, text=True, env=_patch_env()
    )
    # The shape the blind checker takes is the dangerous one: it does not report a
    # WRONG verdict, it reports "not applicable" over a crate that plainly builds
    # a binary declaring a version -- compliance over zero files.
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not applicable" in r.stdout, (
        "narrowing MAIN_FILES did not blind the checker -- this test proves nothing"
    )
    assert "crates/cli/src/lib.rs" not in r.stdout, r.stdout
    # ...and the real checker, on the same fixture, is red.
    assert run_check(repo, CHECKER).returncode == 1, (
        "the real checker no longer reaches lib.rs; the calibration is stale"
    )


def test_the_suite_is_red_when_the_checker_stops_seeing_a_dead_entry(tmp_path):
    """Proof the dead-baseline-entry case is load-bearing: a checker that
    trusts its baseline unconditionally must turn that test red."""
    source = (REPO_ROOT / CHECKER).read_text(encoding="utf-8")
    patched = tmp_path / "checker.py"
    patched.write_text(
        source.replace(
            "    for entry in sorted(allowlist):\n        if entry in declaring:",
            "    for entry in sorted(allowlist):\n        if True:  # patched",
        ),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "Cargo.toml", '[package]\nname = "app"\nversion = "1.2.3"\n')
    write(repo, "build.rs", GOOD_BUILD)
    write(repo, "src/main.rs", _cli("version,"))
    write(repo, ".gates-version-baseline.json", json.dumps(["src/main.rs", "src/lib.rs"]))
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo)

    r = subprocess.run(
        [sys.executable, str(patched), "--baseline", ".gates-version-baseline.json"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_patch_env(),
    )
    assert r.returncode == 0, "the blind checker still reported the dead entry"
    assert "suppresses nothing" not in r.stdout, (
        "the checker still found it -- this test proves nothing"
    )


@pytest.mark.parametrize("argv", [["--json"]])
def test_json_output_carries_the_findings_and_the_population(repo, argv):
    _package(repo, build=GOOD_BUILD, main=_cli("version,"))
    r = run_check(repo, CHECKER, *argv)
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["examined"] == 1, payload
    assert len(payload["findings"]) == 2, payload
