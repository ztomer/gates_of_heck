"""install.sh's write discipline, split by WHAT may refuse.

Two different refusals, two different contracts, and conflating them is how CadGoose lost three
hooks it never had (6.20, 2026-10-14):

  * **Another manager owns this repo's hooks** (`.git/hooks`, a foreign `core.hooksPath`, a
    pre-commit config). Setting `core.hooksPath` would silently stop them, so the whole run is
    refused and NOTHING is written. `--replace-hooks` takes them over. See
    `tests/test_install_foreign_hooks.py`.
  * **A hook is locally modified.** That ONE hook is refused, by name, and left byte-for-byte
    alone; every other hook still installs; the exit code is non-zero so the partial install is
    never read as a complete one. Refusing the whole run here was the bug: one hand-edited
    `pre-commit` cost CadGoose, salary and zinc-core their `commit-msg`, `post-rewrite` and
    `pre-applypatch` as well.

The class the rest of this file still guards is "write-as-you-go, then refuse": the hook loop
used to check each hook just before copying it, so a refusal on `pre-commit` came AFTER
`commit-msg` (which sorts first) had been copied and recorded: monitor (2026-10-08) was left
with a stray stock `.githooks/commit-msg` and a `.goh-installed/` beside the customised hook it
refused to touch -- half an install, reported as a refusal. Its siblings in the same script:
`mkdir -p "$target"` ran BEFORE the "is it a git repo" check (a refused bad path was created),
and the bin/goh build -- the last step that can `die` -- ran after every target write.

Red proof: the two partial-install cases below failed against the pre-6.20 script in the
opposite direction -- the missing hook was NOT installed, which is the bug. The other four
still fail against the pre-2026-10-08 script, which is what they were written for.
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
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _stock_hooks() -> list[str]:
    return sorted(p.name for p in (REPO_ROOT / "hooks").iterdir() if p.is_file())


def test_a_customised_hook_is_refused_alone_while_every_other_hook_installs(repo):
    """THE monitor case, and then the CadGoose case (6.20). The repo's own `.githooks/pre-commit`
    must survive byte-for-byte -- and the three stock hooks it sorted before must NOT be lost
    with it. Before 6.20 the whole run refused and CadGoose kept no `commit-msg` at all."""
    hooks = _stock_hooks()
    assert "commit-msg" in hooks and "pre-commit" in hooks
    assert hooks.index("commit-msg") < hooks.index("pre-commit"), "the case needs an earlier hook"
    custom = repo / ".githooks" / "pre-commit"
    custom.parent.mkdir()
    custom.write_text("#!/bin/bash\n# monitor's own gate\nexit 0\n")
    custom.chmod(0o755)
    git(repo, "config", "core.hooksPath", ".githooks")

    r = _install(REPO_ROOT, repo)

    assert r.returncode != 0, "a partial install must not exit 0"
    assert "pre-commit" in r.stdout + r.stderr, "the refused hook must be named"
    assert "INCOMPLETE" in r.stdout + r.stderr, (
        "an install that left a hook alone must say it did not finish -- "
        "this is the 'partial install read as complete' lie"
    )
    assert "installed into" not in r.stdout + r.stderr, (
        "G15: an install that left a hook alone printed the success line before INCOMPLETE"
    )
    assert custom.read_text() == "#!/bin/bash\n# monitor's own gate\nexit 0\n", (
        "the refused hook was overwritten"
    )
    for h in ("commit-msg", "post-rewrite", "pre-applypatch"):
        assert (repo / ".githooks" / h).exists(), f"{h} was lost to a refusal on another hook"
        assert (repo / ".githooks" / ".goh-installed" / f"{h}.sha256").exists(), (
            f"{h} was installed with no record, so the next run cannot tell it from an edit"
        )
    assert not (repo / ".githooks" / ".goh-installed" / "pre-commit.sha256").exists(), (
        "the refused hook got an install record -- the next run would overwrite it as ours"
    )


def test_a_refused_reinstall_still_restores_the_missing_hooks_and_records_them(repo):
    """A goh-installed repo that lost an early hook and customised the LAST one: the refusal is
    about `last`, and the hook it is missing is restored with a record, so the repo ends up with
    every hook it should have rather than a silent hole."""
    assert _install(REPO_ROOT, repo).returncode == 0
    hooks = _stock_hooks()
    first, last = hooks[0], hooks[-1]
    (repo / ".githooks" / first).unlink()
    (repo / ".githooks" / ".goh-installed" / f"{first}.sha256").unlink()
    (repo / ".githooks" / last).write_text("#!/bin/bash\n# extended locally\nexit 0\n")

    r = _install(REPO_ROOT, repo)

    assert r.returncode != 0
    assert last in r.stdout + r.stderr
    assert (repo / ".githooks" / first).exists(), f"{first} was not restored by a refused run"
    assert (repo / ".githooks" / ".goh-installed" / f"{first}.sha256").exists()
    assert (repo / ".githooks" / last).read_text() == "#!/bin/bash\n# extended locally\nexit 0\n"


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
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode != 0, r.stdout + r.stderr
    assert "build" in r.stdout + r.stderr
    assert _snapshot(repo) == before, "a failed build left a half-installed target"
