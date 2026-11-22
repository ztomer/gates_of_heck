"""`goh locale-prefix`: bash setting a locale itself, refused in every repo (EST.LOCALEPREFIX,
`crates/goh/src/localeprefix.rs`). The shape table is the module's own tests; this is the whole-repo
behaviour through the binary: a planted prefix is red naming its line, the `env` form is green, a
zsh source is not judged, the grace keeps it a warning, and a scan that cannot run is exit 2.
"""

from __future__ import annotations

from pathlib import Path

from conftest import REPO_ROOT, commit_all, hermetic_env, run_goh, write

PLANTED = "#!/usr/bin/env bash\nfind . -type f | LC_ALL=C sort\n"


def _check(repo: Path, tmp_path: Path, *args: str, grace: str = "over"):
    env = hermetic_env(GOH_DIR=str(REPO_ROOT), GOH_GRACE=grace, GOH_SEED_DIR=str(tmp_path / "s"))
    return run_goh(repo, "locale-prefix", *args, env=env)


def test_a_planted_prefix_is_red_naming_its_line_and_env_is_green(repo: Path, tmp_path: Path):
    write(repo, "build.sh", PLANTED)
    write(repo, "prompt.zsh", "LC_ALL=C sort x\n")
    commit_all(repo)
    r = _check(repo, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "build.sh:2:" in out and "env LC_ALL=C" in out and "prompt.zsh" not in out, out
    write(repo, "build.sh", PLANTED.replace("| LC_ALL", "| env LC_ALL"))
    commit_all(repo)
    r = _check(repo, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_within_the_grace_it_is_a_warning(repo: Path, tmp_path: Path):
    write(repo, "build.sh", PLANTED)
    commit_all(repo)
    r = _check(repo, tmp_path, grace="within")
    assert r.returncode == 0 and "build.sh" in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_scan_that_cannot_run_is_exit_2(repo: Path, tmp_path: Path):
    write(repo, "build.sh", PLANTED)
    commit_all(repo)
    assert _check(repo, tmp_path, "--exclude", "(").returncode == 2
    write(repo, "locale_prefix_seed.json", "{not json")
    commit_all(repo)
    assert _check(repo, tmp_path).returncode == 2
    outside = tmp_path / "plain"
    outside.mkdir()
    r = run_goh(outside, "locale-prefix", env=hermetic_env(drop_git=True, GOH_DIR=str(REPO_ROOT)))
    assert r.returncode == 2, r.stdout + r.stderr
