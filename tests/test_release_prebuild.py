"""The release's tagged goh is built before its tag is pushed (GOH.TAGPUSHGATE).

A tagged commit's source stamp names its tag (GOH.SHIPRELREUSE), so its goh is a build of its own.
v0.125.0's ship made the tag and pushed it, and the tag push's version check was the first to ask
for that build: "bin/goh was not built from HEAD's source -- rebuilding it", 98.6 s at load 190 on
the ship's critical path. The push gated nothing (the remote had the commit); the cost was the
build. `build-goh.sh --rev SHA --as-release vX.Y.Z` builds the commit as the tag it will carry,
into the store only, and tools/release_goh.sh starts it beside its land and push: the resolver
asked after the tag answers from the store and builds nothing. A cargo stand-in counts the builds.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from _tree_clone import run_head_cache
from host_locks import scrubbed  # the one env scrub: it keeps the private roots
from test_binary_source_identity import _no_store, private_store
from test_gate_environment import _clean_checkout_of_todays_gates

KIT = Path(__file__).resolve().parent.parent / "tools" / "release_goh.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout.strip()  # fmt: skip


class Box:
    """A clean checkout of today's gates, its stores private, and a cargo that counts builds."""

    def __init__(self, tmp_path: Path) -> None:
        self.checkout = _clean_checkout_of_todays_gates(tmp_path, rust=False)
        # The tree a build exports to compile holds what cargo reads; this cargo reads none of it.
        unread = ("checks", "gates", "hooks", "lib", "tools", "tui", ".github", "launchagents")
        (self.checkout / ".git" / "info").mkdir(exist_ok=True)
        sparse = self.checkout / ".git" / "info" / "sparse-checkout"
        sparse.write_text("/*\n" + "".join(f"!/{d}/\n" for d in unread))
        _git(self.checkout, "config", "core.sparseCheckout", "true")
        _no_store(self.checkout)
        (self.checkout / "bin" / "goh").unlink(missing_ok=True)
        self.calls = tmp_path / "cargo.calls"
        target = tmp_path / "target"
        fake = tmp_path / "fakebin" / "cargo"
        fake.parent.mkdir()
        fake.write_text(
            "#!/bin/bash\n"
            f'case "$1" in\n  metadata) echo \'{{"target_directory": "{target}"}}\' ;;\n'
            f'  build) echo "$*" >> "{self.calls}"; mkdir -p "{target}/release"\n'
            f'    printf \'#!/bin/bash\\n[ "$1" = --version ] && {{ echo "goh %s"; exit 0; }}\\n'
            f'[ "$1" = source-tree ] && {{ echo "%s"; exit 0; }}\\nexit 0\\n\' '
            f'"$GOH_VERSION" "$GOH_SOURCE_STAMP" > "{target}/release/goh"\n'
            f'    chmod +x "{target}/release/goh" ;;\nesac\n'
        )
        fake.chmod(0o755)
        self.env = scrubbed(os.environ, ("GOH_", "PYTEST_"))
        self.env.pop("GATES_OF_HECK_SUITE", None)
        self.env.update(
            PATH=f"{fake.parent}:{os.environ['PATH']}",
            GOH_BIN_STORE=str(private_store(self.checkout)),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            GOH_RUN_DIR=str(tmp_path / "run"),
            GOH_HEAD_CACHE=str(run_head_cache()),  # HEAD is the run's: one export
        )

    def builds(self) -> int:
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def build(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.checkout / "scripts" / "build-goh.sh"), *args],
            cwd=self.checkout, env=self.env, capture_output=True, text=True, check=False,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip

    def resolve(self) -> subprocess.CompletedProcess:
        """What the tag push's check asks: the binary for HEAD (gates/_goh_bin.sh)."""
        script = '. "$1/gates/_goh_bin.sh"; goh_resolve_native; "$goh_native" --version'
        return subprocess.run(
            ["bash", "-c", script, "_", str(self.checkout)], cwd=self.checkout, env=self.env,
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip


def test_a_release_built_before_its_tag_is_not_built_again_when_tagged(tmp_path):
    box = Box(tmp_path)
    head = _git(box.checkout, "rev-parse", "HEAD")
    pre = box.build("--rev", head, "--as-release", "v9.9.9")
    assert pre.returncode == 0, pre.stdout + pre.stderr
    assert box.builds() == 1, "the prebuild did not build"
    assert not (box.checkout / "bin" / "goh").exists(), "a prebuild wrote bin/goh before the tag"
    _git(box.checkout, "tag", "-a", "v9.9.9", "-m", "v9.9.9")
    got = box.resolve()
    assert got.returncode == 0, got.stdout + got.stderr
    assert got.stdout.split() == ["goh", "9.9.9"], got.stdout + got.stderr
    assert box.builds() == 1, f"the tagged commit was built again:\n{got.stderr}"


def test_a_tag_nothing_prebuilt_is_still_built_when_asked(tmp_path):
    """The other direction: a prebuild for another tag is not this one, so the tag is built."""
    box = Box(tmp_path)
    head = _git(box.checkout, "rev-parse", "HEAD")
    assert box.build("--rev", head, "--as-release", "v9.9.8").returncode == 0
    _git(box.checkout, "tag", "-a", "v9.9.9", "-m", "v9.9.9")
    got = box.resolve()
    assert got.stdout.split() == ["goh", "9.9.9"], got.stdout + got.stderr
    assert box.builds() == 2, "the tag's own build never ran"
    assert "rebuilding it" in got.stderr, got.stderr


def test_a_commit_tagged_with_another_release_is_never_built_as_this_one(tmp_path):
    box = Box(tmp_path)
    _git(box.checkout, "tag", "-a", "v9.9.7", "-m", "v9.9.7")
    got = box.build("--rev", "HEAD", "--as-release", "v9.9.9")
    assert got.returncode == 2 and "tagged v9.9.7" in got.stderr, got.stdout + got.stderr
    assert box.builds() == 0


def test_the_release_kit_starts_the_tagged_build_before_its_land_and_waits_before_the_tag():
    text = KIT.read_text()
    start = text.index('--as-release "$TAG"')
    land = text.index('timed "release: land')
    assert start < land, "the tagged build must overlap the land and push"
    assert text.index('wait "$prebuild"', start) < text.index('push origin "refs/tags/$TAG"')


def test_a_literal_head_built_as_a_release_still_reaches_only_the_store(tmp_path):
    """`--rev HEAD` took the ordinary path (bin/goh) and stamped bin/goh with a tag HEAD lacked."""
    box = Box(tmp_path)
    got = box.build("--rev", "HEAD", "--as-release", "v9.9.9")
    assert got.returncode == 0, got.stdout + got.stderr
    assert not (box.checkout / "bin" / "goh").exists(), "bin/goh claims a tag HEAD does not carry"
    assert "ready in the store" in got.stdout, got.stdout + got.stderr
