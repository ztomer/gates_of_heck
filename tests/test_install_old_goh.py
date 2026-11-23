"""An install.sh older than the goh that last installed into a consumer refuses and writes nothing
(GOH.OLDINSTALL).

The goh_dir_repair block GOH.CLASS.ROLLOUT took out of every consumer's tools/gate.sh came back as
an uncommitted insert in four repos (2026-11-14/15): a session whose GOH_DIR named a HEAD export cut
before the rollout, under an agent HOME whose export cache was never pruned, ran that export's
install.sh. Old code cannot be taught to stop, so every install records its release in the
consumer's git config (`goh.installedRelease`) and an install.sh whose own release (the newest
`## vX.Y.Z` in its CHANGELOG.md) is older refuses before its first write, naming the newer release.

Red proof: with the refusal block deleted from install.sh, the stale copy below exits 0 and
rewrites the consumer's hooks and git config.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env

from lib import source_closure

# What _goh_copy joins from the tree (lib/declared_reads.py): a change to any re-runs this file.
TREE_READS = (
    "tui/",
    "hooks/",
    "gates/_hash.sh",
    "lib/bash5.sh",
    "install.sh",
    "retired_hooks.sha256",
)


def _goh_copy(dest: Path, release: str) -> Path:
    """A goh checkout copy (what an export holds of install.sh's inputs) at `release`: install.sh
    and what it sources (source_closure), the stock hooks and the retired list it reads."""
    hooks = tuple(f"hooks/{p.name}" for p in (REPO_ROOT / "hooks").iterdir() if p.is_file())
    source_closure.copy_closure(dest, "install.sh", extra=(*hooks, "retired_hooks.sha256"))
    (dest / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## Unreleased\n\n## {release} -- a release\n\n## v0.0.1 -- the first\n"
    )
    return dest


def _install(goh: Path, target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(goh / "install.sh"), str(target)],
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_SKIP_BUILD="1"),
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _snapshot(root: Path) -> dict[str, str]:
    """Every file under root (.git/config included) with its bytes' digest."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "objects" not in p.parts
    }


def _release(repo: Path) -> str:
    return git(repo, "config", "--get", "goh.installedRelease").strip()


def test_an_install_records_its_release_in_the_consumer(repo, tmp_path):
    new = _goh_copy(tmp_path / "goh-new", "v0.150.0")
    r = _install(new, repo)
    assert r.returncode == 0, r.stderr
    assert _release(repo) == "v0.150.0", "the release an older install.sh is refused against"


def test_a_stale_install_refuses_and_writes_nothing(repo, tmp_path):
    """THE case: a newer goh installed, then an export cut before it runs its install.sh."""
    new = _goh_copy(tmp_path / "goh-new", "v0.150.0")
    stale = _goh_copy(tmp_path / "goh-stale", "v0.118.3")
    assert _install(new, repo).returncode == 0
    (stale / "hooks" / "pre-commit").write_text("#!/bin/bash\n# what the old goh shipped\nexit 0\n")
    before = _snapshot(repo)

    r = _install(stale, repo)

    assert r.returncode != 0, "an older goh's install.sh wrote a consumer a newer one installed"
    assert "v0.150.0" in r.stderr and "v0.118.3" in r.stderr, "the refusal names both releases"
    assert "nothing written" in r.stderr
    assert _snapshot(repo) == before, "a refused install changed the consumer"


def test_the_same_or_a_newer_release_installs(repo, tmp_path):
    """Only OLDER refuses: a re-run of the same release, and an upgrade, both write."""
    a = _goh_copy(tmp_path / "goh-a", "v0.9.0")
    b = _goh_copy(tmp_path / "goh-b", "v0.10.0")  # numeric order, not string order
    assert _install(a, repo).returncode == 0
    assert _install(a, repo).returncode == 0
    r = _install(b, repo)
    assert r.returncode == 0, r.stderr
    assert _release(repo) == "v0.10.0"


def test_a_downgrade_is_allowed_once_the_record_is_unset(repo, tmp_path):
    new = _goh_copy(tmp_path / "goh-new", "v0.150.0")
    old = _goh_copy(tmp_path / "goh-old", "v0.140.0")
    assert _install(new, repo).returncode == 0
    git(repo, "config", "--unset", "goh.installedRelease")
    r = _install(old, repo)
    assert r.returncode == 0, r.stderr
    assert _release(repo) == "v0.140.0"
