"""No early-exit consumer on a pipe under pipefail (`crates/goh/src/earlypipe/`).

`producer | grep -q PAT` with pipefail on is a race: grep exits at its first match, the
producer's next write dies of SIGPIPE (141), and pipefail makes 141 the pipeline's status -- a
match reads as a miss, and only on a loaded host (media_server's deploy.sh read a RUNNING service
as absent, 2026-10-08). The consumer table lives beside the code (`earlypipe/tests.rs`, every
form red and every fixed form green); this file is the whole-repo behaviour: the ratchet at
`--staged`, the naming at full scope, the opt-in that closes it, and index truth.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no early-exit pipe under pipefail"
HEAD = "#!/usr/bin/env bash\nset -euo pipefail\n"
RACY = HEAD + 'docker compose ps --services | grep -qxF "$svc"\n'
FIXED = HEAD + 'out="$(docker compose ps --services)"\ngrep -qxF "$svc" <<< "$out"\n'


def _structural(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = hermetic_env(GOH_DIR=str(REPO_ROOT))
    return subprocess.run(
        [str(goh), "structural", *args], cwd=repo, capture_output=True, text=True, env=env
    )


def _failed(r: subprocess.CompletedProcess) -> bool:
    return r.returncode == 1 and f"structural: {LABEL}" in r.stderr


# ── the command: always strict ────────────────────────────────────────────


def test_the_command_names_file_line_and_the_fix(repo: Path) -> None:
    write(repo, "deploy.sh", RACY)
    commit_all(repo)
    r = run_goh(repo, "early-exit-pipe")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "deploy.sh:3: grep -qxF" in r.stderr, r.stderr
    assert 'grep -q PAT <<< "$out"' in r.stderr, "the fix shape is printed"


def test_the_fixed_shape_is_green(repo: Path) -> None:
    write(repo, "deploy.sh", FIXED)
    commit_all(repo)
    r = run_goh(repo, "early-exit-pipe")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 tracked shell source(s)" in r.stdout, r.stdout


def test_a_shebang_script_without_an_extension_is_in_scope(repo: Path) -> None:
    write(repo, "bin/tool", RACY.replace("#!/usr/bin/env bash", "#!/bin/zsh"))
    write(repo, "notes.txt", RACY.replace("#!/usr/bin/env bash\n", ""))
    commit_all(repo)
    r = run_goh(repo, "early-exit-pipe")
    assert r.returncode == 1 and "bin/tool:3:" in r.stderr, r.stderr
    assert "notes.txt" not in r.stderr, "a text file is not a script"


def test_a_sourced_library_with_no_pipefail_line_is_read(repo: Path) -> None:
    """install-lib.sh has no pipefail line; install.sh sources it, so it runs under one."""
    write(repo, "install.sh", HEAD + '. "$(dirname "$0")/install-lib.sh"\n')
    write(repo, "install-lib.sh", 'has_svc() { docker compose ps | grep -qxF "$1"; }\n')
    commit_all(repo)
    r = run_goh(repo, "early-exit-pipe")
    assert r.returncode == 1 and "install-lib.sh:1:" in r.stderr, r.stdout + r.stderr


def test_a_sh_tmpl_template_is_read(repo: Path) -> None:
    """The source an installer is regenerated from: missing it let a release restore a fix."""
    write(repo, "release/install.sh.tmpl", RACY)
    commit_all(repo)
    r = run_goh(repo, "early-exit-pipe")
    assert r.returncode == 1 and "release/install.sh.tmpl:3:" in r.stderr, r.stdout + r.stderr


def test_exclude_is_honoured(repo: Path) -> None:
    write(repo, "vendor/x.sh", RACY)
    commit_all(repo)
    assert run_goh(repo, "early-exit-pipe", "--exclude", "^vendor/").returncode == 0


def test_the_dispatcher_knows_the_check(repo: Path) -> None:
    write(repo, "deploy.sh", RACY)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "early-exit-pipe"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
    )
    assert r.returncode == 1 and "deploy.sh:3:" in r.stderr, r.stdout + r.stderr


def test_this_repo_has_no_early_exit_pipe() -> None:
    """The sites fixed when the gate landed (doctor, rust_gate, structural, release kit, profiling,
    screen linkage) stay fixed: the strict command over gates_of_heck's own tree."""
    r = run_goh(REPO_ROOT, "early-exit-pipe")
    assert r.returncode == 0, r.stdout + r.stderr


# ── the structural step: the ratchet ──────────────────────────────────────


def test_a_new_racy_pipe_is_refused_at_commit(goh: Path, repo: Path) -> None:
    write(repo, "deploy.sh", RACY)
    git(repo, "add", "deploy.sh")
    r = _structural(goh, repo, "--staged")
    assert _failed(r), r.stdout + r.stderr
    assert "NEW in this commit" in r.stderr and "deploy.sh:3:" in r.stderr, r.stderr


def test_an_existing_one_does_not_block_an_unrelated_edit(goh: Path, repo: Path) -> None:
    write(repo, "deploy.sh", RACY)
    commit_all(repo, "an old racy pipe")
    write(repo, "deploy.sh", RACY + "# a new comment, below the old line\n")
    git(repo, "add", "deploy.sh")
    r = _structural(goh, repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "predate this commit" in r.stderr, "the old one is still named"


def test_editing_the_racy_line_makes_it_new(goh: Path, repo: Path) -> None:
    write(repo, "deploy.sh", RACY)
    commit_all(repo, "an old racy pipe")
    write(repo, "deploy.sh", RACY.replace('"$svc"', '"$service"'))
    git(repo, "add", "deploy.sh")
    assert _failed(_structural(goh, repo, "--staged"))


def test_staged_judges_the_index_not_the_worktree(goh: Path, repo: Path) -> None:
    write(repo, "deploy.sh", RACY)
    git(repo, "add", "deploy.sh")
    write(repo, "deploy.sh", FIXED)
    assert _failed(_structural(goh, repo, "--staged")), "the COMMIT holds the racy pipe"
    write(repo, "deploy.sh", FIXED)
    git(repo, "add", "deploy.sh")
    write(repo, "deploy.sh", RACY)
    assert _structural(goh, repo, "--staged").returncode == 0, "the commit is clean"


def test_full_scope_names_without_failing(goh: Path, repo: Path) -> None:
    write(repo, "deploy.sh", RACY)
    commit_all(repo)
    r = _structural(goh, repo, "--full")
    assert f"✓ {LABEL}" in r.stdout, r.stdout + r.stderr
    assert "deploy.sh:3:" in r.stderr and "GOH_NO_EARLY_EXIT_PIPE=1" in r.stderr, r.stderr


def test_the_opt_in_closes_the_ratchet(goh: Path, repo: Path) -> None:
    write(repo, ".gatesrc", "GOH_NO_EARLY_EXIT_PIPE=1\n")
    write(repo, "deploy.sh", RACY)
    commit_all(repo)
    assert _failed(_structural(goh, repo, "--full"))
    write(repo, "deploy.sh", RACY + "# touched\n")
    git(repo, "add", "deploy.sh")
    assert _failed(_structural(goh, repo, "--staged")), "an OLD one fails too once opted in"
