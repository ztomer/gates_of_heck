"""No code after an `exec` that replaced the shell (`crates/goh/src/deadexec/`).

app_updates' tools/gate.sh ran two `exec`s in one case arm; the second never ran, so the full
gate never judged ROADMAP.md, only its self-test fixtures -- a gate that could not fail (fixed in
app_updates 5a4b33c). shellcheck 0.11.0 exits 0 on it. The shape table lives beside the code
(`deadexec/tests.rs`, every rule mutation-proven); this file is the whole-repo behaviour: scope,
exclusion, the dispatcher, index truth, and the structural step at both scopes (a hard gate --
the estate sweep that preceded it found no site to migrate).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no code after exec"
# The incident, as app_updates' tools/gate.sh carried it before 5a4b33c.
DEAD = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'case "${1:-}" in\n'
    "  --full)\n"
    "    exec python3 tools/check_roadmap.py --self-test\n"
    "    exec python3 tools/check_roadmap.py\n"
    "    ;;\n"
    "esac\n"
)
FIXED = DEAD.replace(
    "    exec python3 tools/check_roadmap.py --self-test",
    "    python3 tools/check_roadmap.py --self-test",
)
# The house parse-guard: exec, a bare exit, the group's close. Must never fire.
GUARD = '{ # parse-guard\nset -e\nexec bash "$0" "$@"\nexit\n} # parse-guard\n'


def _structural(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = hermetic_env(GOH_DIR=str(REPO_ROOT))
    return subprocess.run(
        [str(goh), "structural", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _failed(r: subprocess.CompletedProcess) -> bool:
    return r.returncode == 1 and f"structural: {LABEL}" in r.stderr


# -- the command --------------------------------------------------------------------------------


def test_the_command_names_the_dead_line_and_the_exec(repo: Path) -> None:
    write(repo, "tools/gate.sh", DEAD)
    commit_all(repo)
    r = run_goh(repo, "dead-after-exec")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tools/gate.sh:6: exec python3 tools/check_roadmap.py " in r.stderr, r.stderr
    assert "at line 5 replaced the shell" in r.stderr, r.stderr


def test_the_fixed_shape_and_the_parse_guard_are_green(repo: Path) -> None:
    write(repo, "tools/gate.sh", FIXED)
    write(repo, "gates/x.sh", GUARD)
    write(repo, "logs.sh", 'exec >"$log" 2>&1\necho logged\nexec cmd || echo failed\necho next\n')
    commit_all(repo)
    r = run_goh(repo, "dead-after-exec")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 tracked shell source(s)" in r.stdout, r.stdout


def test_a_shebang_script_without_an_extension_is_in_scope(repo: Path) -> None:
    write(repo, ".githooks/pre-push", "#!/bin/sh\nexec a\nb\n")
    write(repo, "notes.txt", "exec a\nb\n")
    commit_all(repo)
    r = run_goh(repo, "dead-after-exec")
    assert r.returncode == 1 and ".githooks/pre-push:3:" in r.stderr, r.stderr
    assert "notes.txt" not in r.stderr, "a text file is not a script"


def test_bash_and_sh_tmpl_sources_are_read(repo: Path) -> None:
    write(repo, "lib/x.bash", "exec a\nb\n")
    write(repo, "release/install.sh.tmpl", "exec a\nb\n")
    commit_all(repo)
    r = run_goh(repo, "dead-after-exec")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "lib/x.bash:2:" in r.stderr and "release/install.sh.tmpl:2:" in r.stderr, r.stderr


def test_exclude_is_honoured_flag_and_gatesrc(repo: Path) -> None:
    write(repo, "vendor/x.sh", "exec a\nb\n")
    commit_all(repo)
    assert run_goh(repo, "dead-after-exec", "--exclude", "^vendor/").returncode == 0
    write(repo, ".gatesrc", "GOH_EXCLUDE='^vendor/'\n")
    commit_all(repo)
    assert run_goh(repo, "dead-after-exec").returncode == 0, "the repo's own GOH_EXCLUDE"
    assert run_goh(repo, "dead-after-exec", "--exclude", "").returncode == 1, "'' opts out"


def test_the_dispatcher_knows_the_check(repo: Path) -> None:
    write(repo, "tools/gate.sh", DEAD)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "dead-after-exec"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
    )
    assert r.returncode == 1 and "tools/gate.sh:6:" in r.stderr, r.stdout + r.stderr


def test_this_repo_has_no_code_after_exec() -> None:
    """Every parse-guard here ends `exec ...`, `exit`, `}`: the allowance is exercised for real."""
    r = run_goh(REPO_ROOT, "dead-after-exec")
    assert r.returncode == 0, r.stdout + r.stderr


# -- the structural step: a hard gate at both scopes ------------------------------------------


def test_a_dead_line_is_refused_at_commit(goh: Path, repo: Path) -> None:
    write(repo, "tools/gate.sh", DEAD)
    git(repo, "add", "tools/gate.sh")
    r = _structural(goh, repo, "--staged")
    assert _failed(r), r.stdout + r.stderr
    assert "tools/gate.sh:6:" in r.stderr, r.stderr


def test_staged_judges_the_index_not_the_worktree(goh: Path, repo: Path) -> None:
    write(repo, "tools/gate.sh", DEAD)
    git(repo, "add", "tools/gate.sh")
    write(repo, "tools/gate.sh", FIXED)
    assert _failed(_structural(goh, repo, "--staged")), "the COMMIT holds the dead line"
    git(repo, "add", "tools/gate.sh")
    write(repo, "tools/gate.sh", DEAD)
    assert _structural(goh, repo, "--staged").returncode == 0, "the commit is clean"


def test_an_existing_dead_line_fails_at_full_scope(goh: Path, repo: Path) -> None:
    """Not a ratchet: there is no migration to protect, so an old finding is not let stand."""
    write(repo, "tools/gate.sh", DEAD)
    commit_all(repo, "an old dead line")
    assert _failed(_structural(goh, repo, "--full"))
