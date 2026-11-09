"""A repo whose main moves only through tools/land.sh refuses a COMMIT made on main (GOH.LANDONLY).

THE INCIDENT. 2026-11-08: SRV.NTFYROTATE committed 5c4936f straight onto gates_of_heck's main, in
the primary checkout, with the rule ("goh lands ONLY through tools/land.sh from a worktree") in
prose and nothing refusing it. `GOH_LANDED_ONLY=main` already refused the PUSH of a tip land.sh had
not gated -- but the commit was pushed anyway, because a later landed tip carried it.

So the commit hook refuses it where it is made: `gates/commit_gate.sh` asks `lib/landed_only.sh`
first. The refusal is a commit in the PRIMARY checkout (the one whose git dir is the common dir) on
the branch `.gatesrc` names; a linked worktree, another branch and a repo that names no branch
are untouched. The one caller that is let through is land.sh (`GOH_LAND_CALLER=land.sh`, set on
its merge only); the human escape is `GOH_MAIN_COMMIT_OK=<reason>`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import test_release_kit as rk
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env, native_goh_path
from test_release_kit import TAG, VERSION, run_release, sh

LAND = REPO_ROOT / "tools" / "land.sh"
STRUCTURAL_GATE = (
    '#!/usr/bin/env bash\nset -euo pipefail\nexec bash "$GOH_DIR/gates/structural.sh" --staged\n'
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _install_hook(main: Path) -> None:
    """The stock pre-commit, as install.sh gives it, wired as the repo's hooks path."""
    (main / ".githooks").mkdir(exist_ok=True)
    hook = main / ".githooks" / "pre-commit"
    hook.write_bytes((REPO_ROOT / "hooks" / "pre-commit").read_bytes())
    hook.chmod(0o755)
    _git(main, "config", "core.hooksPath", ".githooks")


def _landed_only_repo(tmp_path: Path, rc: str = "GOH_LANDED_ONLY=main\n") -> tuple[Path, Path]:
    """(main, wt): a primary checkout on `main` declaring GOH_LANDED_ONLY, and a linked worktree."""
    main = tmp_path / "main"
    (main / "tools").mkdir(parents=True)
    (main / "tools" / "gate.sh").write_text(STRUCTURAL_GATE)
    (main / "tools" / "gate.sh").chmod(0o755)
    (main / "README.md").write_text("repo\n")
    (main / ".gatesrc").write_text(rc)
    fast_init(main, "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "--no-verify", "-m", "one")
    _install_hook(main)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "--no-verify", "-m", "hook")
    _git(main, "worktree", "add", "-q", "-b", "feature", str(tmp_path / "wt"))
    return main, tmp_path / "wt"


def _env(**extra: str) -> dict[str, str]:
    env = hermetic_env(drop_git=True, GOH_BIN=str(native_goh_path()), **extra)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"})
    env.update({"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    return env


def _commit(where: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    (where / "ours.txt").write_text(f"{os.urandom(4).hex()}\n")
    _git(where, "add", "ours.txt")
    return subprocess.run(
        ["git", "-C", str(where), "commit", "-q", "-m", "ours"],
        env=_env(**extra),
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_a_commit_on_main_in_the_primary_checkout_is_refused(tmp_path: Path) -> None:
    main, _ = _landed_only_repo(tmp_path)
    before = _git(main, "rev-parse", "HEAD")
    got = _commit(main)
    text = got.stdout + got.stderr
    assert got.returncode != 0, f"a direct commit on main went through: {text}"
    assert "land.sh" in text and "GOH_MAIN_COMMIT_OK" in text, text
    assert _git(main, "rev-parse", "HEAD") == before, "main moved on a refused commit"


def test_the_refusal_comes_before_the_gate_runs(tmp_path: Path) -> None:
    """A commit that can never be accepted is not worth a gate run (the staged gate takes seconds)."""
    main, _ = _landed_only_repo(tmp_path)
    (main / "tools" / "gate.sh").write_text("#!/bin/sh\necho GATE-RAN >&2\nexit 0\n")
    _git(main, "commit", "-q", "--no-verify", "-am", "loud gate")
    got = _commit(main)
    assert got.returncode != 0 and "GATE-RAN" not in got.stdout + got.stderr, got.stderr


def test_a_commit_in_a_linked_worktree_passes(tmp_path: Path) -> None:
    main, wt = _landed_only_repo(tmp_path)
    got = _commit(wt)
    assert got.returncode == 0, got.stdout + got.stderr
    assert _git(main, "log", "--format=%s", "feature").splitlines()[0] == "ours"


def test_a_commit_in_the_primary_checkout_on_another_branch_passes(tmp_path: Path) -> None:
    main, wt = _landed_only_repo(tmp_path)
    _git(wt, "checkout", "-q", "--detach")  # frees `feature` for the primary checkout
    _git(main, "checkout", "-q", "feature")
    got = _commit(main)
    assert got.returncode == 0, got.stdout + got.stderr


def test_a_repo_that_names_no_landed_branch_is_untouched(tmp_path: Path) -> None:
    main, _ = _landed_only_repo(tmp_path, rc="# a consumer: no GOH_LANDED_ONLY\n")
    got = _commit(main)
    assert got.returncode == 0, got.stdout + got.stderr


def test_the_branch_comes_from_gatesrc_not_the_callers_environment(tmp_path: Path) -> None:
    """An exported GOH_LANDED_ONLY must not turn the rule on (or off) in a repo's own say-so."""
    main, _ = _landed_only_repo(tmp_path, rc="# none\n")
    got = _commit(main, GOH_LANDED_ONLY="main")
    assert got.returncode == 0, got.stdout + got.stderr


def test_land_sh_is_let_through_by_its_token(tmp_path: Path) -> None:
    main, _ = _landed_only_repo(tmp_path)
    got = _commit(main, GOH_LAND_CALLER="land.sh")
    assert got.returncode == 0, got.stdout + got.stderr


@pytest.mark.parametrize("token", ["", "yes", "LAND.SH"])
def test_any_other_token_is_not_land_sh(tmp_path: Path, token: str) -> None:
    main, _ = _landed_only_repo(tmp_path)
    assert _commit(main, GOH_LAND_CALLER=token).returncode != 0


def test_the_documented_escape_needs_a_reason(tmp_path: Path) -> None:
    main, _ = _landed_only_repo(tmp_path)
    assert _commit(main, GOH_MAIN_COMMIT_OK="").returncode != 0, "an empty reason is no reason"
    got = _commit(main, GOH_MAIN_COMMIT_OK="a hotfix the gate cannot run for")
    assert got.returncode == 0, got.stdout + got.stderr


def test_land_sh_still_lands_through_the_installed_hook(tmp_path: Path) -> None:
    """The end to end of the claim: land.sh from a worktree, with the refusing hook installed,
    moves main -- and says so to the merge's own hooks (a post-merge hook reads the token)."""
    main, wt = _landed_only_repo(tmp_path)
    seen = tmp_path / "caller.txt"
    post = main / ".githooks" / "post-merge"
    post.write_text(f'#!/bin/sh\nprintf %s "${{GOH_LAND_CALLER-}}" > "{seen}"\n')
    post.chmod(0o755)
    _git(main, "add", ".githooks/post-merge")
    _git(main, "commit", "-q", "--no-verify", "-m", "post-merge probe")
    _git(wt, "rebase", "-q", "main")
    assert _commit(wt).returncode == 0
    tip = _git(wt, "rev-parse", "HEAD")
    env = _env(GOH_LAND_GATE="true")
    got = subprocess.run(
        ["bash", str(LAND)],
        cwd=wt,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    assert _git(main, "rev-parse", "HEAD") == tip
    assert seen.read_text() == "land.sh", "land.sh must name itself to the merge it makes"


# --- release.sh and the other worktrees --------------------------------------------------------


def _release_kit_with_hook(kit: dict) -> Path:
    proj: Path = kit["proj"]
    (proj / ".gatesrc").write_text("GOH_LANDED_ONLY=main\n")
    (proj / "tools").mkdir(exist_ok=True)
    (proj / "tools" / "gate.sh").write_text("#!/bin/sh\nexit 0\n")
    (proj / "tools" / "gate.sh").chmod(0o755)
    _install_hook(proj)
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "--no-verify", "-m", "hook, gatesrc")
    return proj


kit = rk.kit  # the release fixtures, by assignment (an import would be shadowed by each test's arg)


def test_release_sh_tags_a_tip_that_already_carries_its_stanza(kit: dict) -> None:
    """The goh release flow: the version commit and its stanza land through land.sh, then release.sh
    only tags and pushes. It makes no commit of its own, so the hook has nothing to refuse."""
    proj = _release_kit_with_hook(kit)
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sh(proj, "tag", "-l", TAG).strip() == TAG


def test_release_sh_may_not_cut_a_stanza_into_main_past_land_sh(kit: dict) -> None:
    """The cut makes a commit on main in the primary checkout: for a landed-only repo that is the
    very thing the rule refuses (the push gate would refuse the tip later). It is refused HERE,
    naming land.sh, with nothing tagged."""
    proj = _release_kit_with_hook(kit)
    (proj / "CHANGELOG.md").write_text("# C\n\n## Unreleased\n\n* new\n\n## v1.1.0\n\n- older\n")
    _git(proj, "commit", "-q", "--no-verify", "-am", "feat: something")
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "land.sh" in out, out
    assert sh(proj, "tag", "-l", TAG).strip() == ""


def test_release_sh_cuts_in_a_worktree(kit: dict) -> None:
    """The same cut, from a linked worktree of the repo (its branch is not main): passes."""
    proj = _release_kit_with_hook(kit)
    (proj / "CHANGELOG.md").write_text("# C\n\n## Unreleased\n\n* new\n\n## v1.1.0\n\n- older\n")
    _git(proj, "commit", "-q", "--no-verify", "-am", f"chore: version {VERSION}")
    wt = proj.parent / "relwt"
    _git(proj, "worktree", "add", "-q", "-b", "rel", str(wt))
    r = run_release({**kit, "proj": wt}, "--no-push")
    out = r.stdout + r.stderr
    assert "land.sh" not in out, out
    assert "## v1.2.3" in (wt / "CHANGELOG.md").read_text(), out
