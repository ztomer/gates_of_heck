"""A hook's GIT_DIR must never reach git run on ANOTHER repository.

zinc, 2026-09-27: git hands a hook GIT_DIR (and GIT_INDEX_FILE). Under a LINKED worktree
GIT_DIR is absolute -- `<main>/.git/worktrees/<name>` -- so any child `git init <tmpdir>` that
inherits it re-initialises the REAL repository instead, and with extensions.worktreeConfig on
writes `core.bare = true` into the shared config. Every checkout of the repo then fails with
"this operation must be run in a work tree". From the main checkout (GIT_DIR=.git, relative) it
does not flip, which is why it hid.

Each test builds that exact shape -- a scratch main, a linked worktree, worktreeConfig on -- runs
one route by which gates_of_heck spawns git on a foreign repo with the hook-like environment, and
asserts the scratch main's core.bare stayed unset. `test_the_fixture_reproduces_the_class` is the
calibration: the raw inheritance DOES flip it, so a green run elsewhere means the route is
scrubbed, not that the fixture is inert.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "checks"))
from _gitutil import local_env_vars  # noqa: E402

CHILD_FLAG = "GOH_HOOK_ENV_CHILD"

# A consumer gate that exercises both routes: under --probe it builds a fixture repo (the
# check_probes_pass route); swept, it says whether its git resolved the hook's repo (the
# empty-scope route). A short verdict, not the path: the sweep truncates what it echoes to 100
# characters, and --show-toplevel is blind (with GIT_DIR set and no work tree it answers the cwd).
GATE = """import os, subprocess, sys, tempfile
if "--probe" in sys.argv:
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["git", "init", "-q", td], check=True)
    sys.exit(0)
gd = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], capture_output=True, text=True)
print("HOOK-REPO" if gd.stdout.strip() == os.environ["HOOK_GITDIR"] else "own-repo")
sys.exit(0)
"""

# A pushed repo's full gate that builds a fixture repo, like any test suite, and reports the git
# dir it runs under.
PUSH_GATE_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
git init -q "$(mktemp -d "${TMPDIR:-/tmp}/goh-hookenv.XXXXXX")"
printf 'gitdir=%s\\n' "$(git rev-parse --absolute-git-dir)" >> "$GATE_REPORT"
printf 'GIT_DIR=%s\\n' "${GIT_DIR:-unset}" >> "$GATE_REPORT"
"""


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _scratch(tmp_path, files=None):
    """(main, worktree, hook_env): the shape that flips, with `files` committed."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    for rel, body in (files or {}).items():
        (main / rel).parent.mkdir(parents=True, exist_ok=True)
        (main / rel).write_text(body)
        if rel.endswith(".sh"):
            (main / rel).chmod(0o755)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "--allow-empty", "-m", "one")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt))
    _git(main, "config", "extensions.worktreeConfig", "true")
    gitdir = (main / ".git" / "worktrees" / "wt").resolve()
    hook_env = dict(os.environ, GIT_DIR=str(gitdir), GIT_INDEX_FILE=str(gitdir / "index"))
    return main, wt, hook_env


def _bare(main):
    """core.bare as the SHARED config holds it ('' when unset)."""
    return subprocess.run(
        ["git", "config", "--file", str(main / ".git" / "config"), "core.bare"],
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_the_fixture_reproduces_the_class(tmp_path):
    main, _, hook_env = _scratch(tmp_path)
    assert _bare(main) in ("", "false")
    subprocess.run(
        ["git", "init", "-q", str(tmp_path / "other")],
        env=hook_env,
        check=True,
        capture_output=True,
    )
    assert _bare(main) == "true", "the hazard did not reproduce: every test below is vacuous"
    assert not (tmp_path / "other" / ".git").exists()


def test_empty_scope_sweep_never_touches_the_hooks_repo(tmp_path):
    main, wt, hook_env = _scratch(tmp_path, {"tools/check_where.py": GATE})
    hook_env["HOOK_GITDIR"] = hook_env["GIT_DIR"]
    head = _git(wt, "rev-parse", "HEAD")
    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "checks" / "check_empty_scope.py")],
        cwd=wt,
        env=hook_env,
        capture_output=True,
        text=True,
    )
    text = out.stdout + out.stderr
    assert _bare(main) in ("", "false"), text
    assert _git(wt, "rev-parse", "HEAD") == head, "the skeleton's commit landed in the real repo"
    assert "scope@example.invalid" not in (main / ".git" / "config").read_text()
    # The gate's git resolved the SKELETON's repo, not the checkout the hook fired in.
    assert "own-repo" in text and "HOOK-REPO" not in text, text


def test_empty_scope_probe_never_touches_the_hooks_repo(tmp_path):
    main, wt, hook_env = _scratch(tmp_path)
    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "checks" / "check_empty_scope.py"), "--probe"],
        cwd=wt,
        env=hook_env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert _bare(main) in ("", "false"), out.stdout + out.stderr


def test_probes_run_without_the_hooks_variables(tmp_path):
    main, wt, hook_env = _scratch(tmp_path, {"tools/check_where.py": GATE})
    hook_env["HOOK_GITDIR"] = hook_env["GIT_DIR"]
    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "checks" / "check_probes_pass.py")],
        cwd=wt,
        env=hook_env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert _bare(main) in ("", "false"), out.stdout + out.stderr


def test_push_gate_runs_the_export_without_the_pushers_git_dir(tmp_path):
    """pre-push from a linked worktree exports GIT_DIR (measured, git 2.55): the export's gate
    must see its OWN git dir, and its fixture repos must stay theirs."""
    main, wt, hook_env = _scratch(tmp_path, {"tools/gate.sh": PUSH_GATE_SCRIPT})
    hook_env.pop("GIT_INDEX_FILE")  # pre-push carries GIT_DIR only
    report = tmp_path / "report.txt"
    hook_env.update(
        GATE_REPORT=str(report), GOH_DIR=str(REPO_ROOT), GOH_PUSH_LOGS=str(tmp_path / "push-logs")
    )
    sha = _git(wt, "rev-parse", "HEAD")
    out = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "push_gate.sh")],
        cwd=wt,
        env=hook_env,
        text=True,
        capture_output=True,
        input=f"refs/heads/wt {sha} refs/heads/wt {'0' * 40}\n",
    )
    seen = report.read_text() if report.exists() else ""
    assert out.returncode == 0, out.stdout + out.stderr
    assert _bare(main) in ("", "false"), seen
    assert "GIT_DIR=unset" in seen, seen
    assert f"gitdir={hook_env['GIT_DIR']}\n" not in seen, seen


def test_the_suite_never_sees_hook_variables(tmp_path):
    """The child half of the conftest test below (and a plain check on every run): conftest
    dropped the variables at import, so a fixture `git init` builds its own repo."""
    leaked = [v for v in local_env_vars() if v in os.environ]
    assert not leaked, f"conftest did not scrub {leaked}"
    subprocess.run(
        ["git", "init", "-q", str(tmp_path / "fixture")], check=True, capture_output=True
    )
    assert (tmp_path / "fixture" / ".git").is_dir()


def test_conftest_scrubs_before_any_fixture_runs(tmp_path):
    main, _, hook_env = _scratch(tmp_path)
    hook_env[CHILD_FLAG] = "1"
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            f"{Path(__file__).relative_to(REPO_ROOT)}::test_the_suite_never_sees_hook_variables",
        ],
        cwd=REPO_ROOT,
        env=hook_env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert _bare(main) in ("", "false"), out.stdout + out.stderr


def test_rust_test_code_spawns_git_only_through_the_testkit():
    """Every `Command::new("git")` in crates/ is production code (which honours the hook's
    variables on purpose) or the testkit's one scrubbed helper. A new hand-rolled fixture
    `fn git` -- four existed, none scrubbed -- lands here first: use goh_testkit::git_in."""
    allowed = {
        "crates/goh/build.rs": 1,  # the source stamp (C3); strips the hook's GIT_* itself
        "crates/goh-testkit/src/lib.rs": 2,  # local_env_vars() probe + git_command()
        "crates/goh/src/gitutil.rs": 3,  # repo_root / listed_files / content_bytes
        "crates/goh/src/blobs.rs": 1,  # prefetch_staged's cat-file
        "crates/goh/src/index_view.rs": 3,  # staged view: GIT_DIR/GIT_INDEX_FILE deliberate
    }
    found = {}
    for path in sorted((REPO_ROOT / "crates").rglob("*.rs")):
        n = path.read_text(encoding="utf-8").count('Command::new("git")')
        if n:
            found[str(path.relative_to(REPO_ROOT))] = n
    assert found == allowed, (
        'Command::new("git") census changed. Fixture git in tests goes through '
        f"goh_testkit::git_in / git_command; production additions update this pin.\n{found}"
    )
