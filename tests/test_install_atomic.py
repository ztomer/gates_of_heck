"""install.sh is all-or-nothing: an install that refuses or fails changes NOTHING in the target.

THE CLASS: "write-as-you-go, then refuse". The hook loop checked each hook just before copying it,
so a refusal on `pre-commit` came AFTER `commit-msg` (which sorts first) had been copied and
recorded: monitor (2026-10-08) was left with a stray stock `.githooks/commit-msg` and a
`.githooks/.goh-installed/` beside the customised hook it refused to touch -- half an install,
reported as a refusal. Its siblings in the same script: `mkdir -p "$target"` ran BEFORE the
"is it a git repo" check (a refused bad path was created), and the bin/goh build -- the last
step that can `die` -- ran after every target write. The fix orders the script as
validate everything → build (touches only this checkout) → write the target.

Red proof: five of the six failed against the pre-fix install.sh (a stray hook and record, one
hook named of two, a created directory, hooks written before the build died). The sixth, a plain
non-repo directory, is a guard: the old script happened to refuse it before writing.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, git, hermetic_env


def _snapshot(root: Path) -> dict[str, str]:
    """Every path under root (directories too: an empty `.goh-installed/` is a change) with its
    bytes' digest and mode, so 'unchanged' means byte-for-byte AND permission-for-permission."""
    snap = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            snap[rel] = "link:" + str(p.readlink())
        elif p.is_dir():
            snap[rel] = "dir"
        else:
            snap[rel] = f"{oct(p.stat().st_mode)}:{hashlib.sha256(p.read_bytes()).hexdigest()}"
    return snap


def _install(goh: Path, target: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(goh / "install.sh"), str(target)],
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_SKIP_BUILD="1", **env),
    )


def _stock_hooks() -> list[str]:
    return sorted(p.name for p in (REPO_ROOT / "hooks").iterdir() if p.is_file())


def test_a_customised_later_hook_refuses_before_any_earlier_hook_is_written(repo):
    """THE monitor case: the repo's own `.githooks/pre-commit`, never a goh install. Every stock
    hook sorting before it (commit-msg) was copied and recorded before the refusal."""
    hooks = _stock_hooks()
    assert "commit-msg" in hooks and "pre-commit" in hooks
    assert hooks.index("commit-msg") < hooks.index("pre-commit"), "the case needs an earlier hook"
    custom = repo / ".githooks" / "pre-commit"
    custom.parent.mkdir()
    custom.write_text("#!/bin/bash\n# monitor's own gate\nexit 0\n")
    custom.chmod(0o755)
    git(repo, "config", "core.hooksPath", ".githooks")
    before = _snapshot(repo)

    r = _install(REPO_ROOT, repo)

    assert r.returncode != 0, "install overwrote a customised hook"
    assert "pre-commit" in r.stdout + r.stderr, "the refused hook must be named"
    assert _snapshot(repo) == before, (
        "a refused install changed the tree: "
        f"{sorted(set(_snapshot(repo).items()) ^ set(before.items()))}"
    )


def test_a_refused_reinstall_restores_no_missing_hook_and_writes_no_record(repo):
    """A goh-installed repo that lost an early hook (and its record) and customised the LAST one:
    the refusal must not re-create the lost hook on its way to the refusal."""
    assert _install(REPO_ROOT, repo).returncode == 0
    hooks = _stock_hooks()
    first, last = hooks[0], hooks[-1]
    (repo / ".githooks" / first).unlink()
    (repo / ".githooks" / ".goh-installed" / f"{first}.sha256").unlink()
    (repo / ".githooks" / last).write_text("#!/bin/bash\n# extended locally\nexit 0\n")
    before = _snapshot(repo)

    r = _install(REPO_ROOT, repo)

    assert r.returncode != 0
    assert last in r.stdout + r.stderr
    assert _snapshot(repo) == before
    assert not (repo / ".githooks" / first).exists(), f"{first} was installed by a refused run"


def test_every_customised_hook_is_named_not_just_the_first(repo):
    """Checking every target first means the refusal can list them all: one run, one fix, instead
    of a refuse → fix → refuse loop that names one hook per run."""
    assert _install(REPO_ROOT, repo).returncode == 0
    hooks = _stock_hooks()
    for h in (hooks[0], hooks[-1]):
        (repo / ".githooks" / h).write_text(f"#!/bin/bash\n# {h} replaced\nexit 0\n")
    r = _install(REPO_ROOT, repo)
    assert r.returncode != 0
    for h in (hooks[0], hooks[-1]):
        assert f".githooks/{h}" in r.stdout + r.stderr, f"{h} was not named"


def test_a_target_that_is_not_a_repo_is_refused_without_being_created(tmp_path):
    target = tmp_path / "no" / "such" / "dir"
    r = _install(REPO_ROOT, target)
    assert r.returncode != 0
    assert "not" in (r.stdout + r.stderr)
    assert not (tmp_path / "no").exists(), "the refused target path was created"


def test_a_target_dir_outside_any_repo_is_left_untouched(tmp_path):
    target = tmp_path / "plain"
    target.mkdir()
    r = _install(REPO_ROOT, target)
    assert r.returncode != 0
    assert list(target.iterdir()) == []


@pytest.fixture
def goh_with_failing_build(tmp_path):
    """A goh checkout copy whose bin/goh build fails, with a `cargo` on PATH so it is attempted."""
    src = tmp_path / "goh-src"
    for d in ("tui", "hooks"):
        shutil.copytree(REPO_ROOT / d, src / d)
    (src / "gates").mkdir()
    shutil.copy2(REPO_ROOT / "gates" / "_hash.sh", src / "gates" / "_hash.sh")
    for f in ("install.sh", "retired_hooks.sha256"):
        shutil.copy2(REPO_ROOT / f, src / f)
    (src / "scripts").mkdir()
    build = src / "scripts" / "build-goh.sh"
    build.write_text("#!/bin/bash\necho 'build broke' >&2\nexit 1\n")
    build.chmod(0o755)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "cargo").write_text("#!/bin/bash\nexit 0\n")
    (fakebin / "cargo").chmod(0o755)
    return src, f"{fakebin}:/usr/bin:/bin"


def test_a_failed_build_leaves_the_target_untouched(repo, goh_with_failing_build):
    """The build is the last step that can die; it touches only the goh checkout, so it runs
    before any target write -- a dead build must not leave hooks wired to a binary-less checkout
    while the message says the install failed."""
    src, path = goh_with_failing_build
    before = _snapshot(repo)
    r = subprocess.run(
        ["/bin/bash", str(src / "install.sh"), str(repo)],
        capture_output=True,
        text=True,
        env=hermetic_env(PATH=path),
    )
    assert r.returncode != 0, r.stdout + r.stderr
    assert "build" in r.stdout + r.stderr
    assert _snapshot(repo) == before, "a failed build left a half-installed target"
