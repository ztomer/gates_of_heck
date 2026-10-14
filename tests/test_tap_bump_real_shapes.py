"""A tap bump moves version, url and sha256 TOGETHER, on the tap's real shapes (BACKLOG 6.15).

Measured 2026-10-14 against a scratch copy of homebrew-tap: a cask bump moved `sha256` but left
`version "0.41.1"` (the old dmg, the new digest), and on `multitop.rb` both agent `resource`s got
the source tarball's digest and kept their v0.54.0 URLs. Both passed the tap's gate. The old
fixture's URL shape (a hard-coded tag on a cask with no `version`) matched none of the tap's casks,
so it could not see either.

The fixtures under tests/fixtures/homebrew_tap/ are COPIES of homebrew-tap at `ad99f17`. The
result is read back by RUBY, not by the rewriter's own reader: each file is evaluated against a
recording stub of the Homebrew DSL, so `url "...v#{version}..."` interpolates the way brew would,
and a resource's `sha256` is the one inside its own block. A reader shared with the rewriter would
share its defects (differential blindness).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT

BUMP = REPO_ROOT / "tools" / "release-kit" / "tap_bump.sh"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "homebrew_tap"
SHAPES = sorted(p.relative_to(FIXTURES).as_posix() for p in FIXTURES.glob("*/*.rb"))

pytestmark = pytest.mark.skipif(shutil.which("ruby") is None, reason="ruby not installed")

# The Homebrew DSL, recorded: everything the bump must move, nothing evaluated that runs.
READ = r"""
require "json"
class Rec
  attr_reader :data
  def initialize; @data = {"url" => nil, "sha256" => nil, "version" => nil, "resources" => {}}; end
  def version(v = nil); v.nil? ? @data["version"] : (@data["version"] = v); end
  def url(u, **_k); @data["url"] = u; end
  def sha256(s = nil, **_k); @data["sha256"] = s; end
  def resource(name, &blk); r = Rec.new; r.instance_eval(&blk); @data["resources"][name] = r.data; end
  def method_missing(*_a, **_k, &_b); nil; end
  def respond_to_missing?(*_a); true; end
end
class Formula
  def self.rec; @rec ||= Rec.new; end
  %w[version url sha256 resource].each do |m|
    define_singleton_method(m) { |*a, **k, &b| rec.public_send(m, *a, **k, &b) }
  end
  def self.test(*_a, &_b); nil; end  # Kernel#test would take it otherwise
  def self.method_missing(*_a, **_k, &_b); nil; end
  def self.respond_to_missing?(*_a); true; end
end
$cask = nil
def cask(_name, &blk); $cask = Rec.new; $cask.instance_eval(&blk); end
load ARGV[0]
out = $cask ? $cask.data : ObjectSpace.each_object(Class).find { |c| c < Formula }.rec.data
puts JSON.generate(out)
"""


def read(path: Path) -> dict:
    r = subprocess.run(
        ["ruby", "-e", READ, str(path)],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return json.loads(r.stdout)


def git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        stdin=subprocess.DEVNULL,
    ).stdout


def tap_with(tmp: Path, rel: str, text: str | None = None) -> Path:
    """A bare tap holding one real file (or a variant of it), at its path in homebrew-tap."""
    seed = tmp / "seed"
    (seed / os.path.dirname(rel)).mkdir(parents=True)
    (seed / rel).write_text((FIXTURES / rel).read_text() if text is None else text)
    fast_init(seed, "main")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "seed")
    bare = tmp / "tap.git"
    git(tmp, "clone", "-q", "--bare", str(seed), str(bare))
    return bare


def bump(bare: Path, rel: str, tag: str, artifact: Path, *extra: str):
    kind = "--cask" if rel.startswith("Casks/") else "--formula"
    name = Path(rel).stem
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GOH_DIR=str(REPO_ROOT),
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    return subprocess.run(
        [
            "bash",
            str(BUMP),
            "--tap",
            str(bare),
            kind,
            name,
            "--tag",
            tag,
            "--artifact",
            str(artifact),
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def next_version(v: str) -> str:
    parts = v.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def old_version(before: dict) -> str:
    if before["version"]:
        return before["version"]
    return before["url"].split("/tags/v", 1)[1].removesuffix(".tar.gz")


@pytest.mark.parametrize("rel", SHAPES)
def test_every_real_shape_moves_version_url_and_sha256_together(tmp_path, rel):
    before = read(FIXTURES / rel)
    old = old_version(before)
    new = next_version(old)
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"the new release " + rel.encode())
    extra, want_res = [], {}
    for name in before["resources"]:
        res = tmp_path / f"{name}.bin"
        res.write_bytes(b"resource " + name.encode())
        extra += ["--resource", f"{name}={res}"]
        want_res[name] = digest(res)
    bare = tap_with(tmp_path, rel)
    r = bump(bare, rel, f"v{new}", artifact, *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "after.rb"
    out.write_text(git(bare, "show", f"main:{rel}"))
    after = read(out)
    assert after["sha256"] == digest(artifact), after
    assert new in after["url"] and old not in after["url"], after["url"]
    if before["version"]:
        assert after["version"] == new, after
    assert set(after["resources"]) == set(before["resources"])
    for name, res in after["resources"].items():
        assert new in res["url"] and old not in res["url"], (name, res["url"])
        assert res["sha256"] == want_res[name], (name, res["sha256"])


def _local_releases(tmp: Path) -> str:
    """multitop.rb with its resources served from a file:// release tree instead of GitHub."""
    text = (FIXTURES / "Formula/multitop.rb").read_text()
    return text.replace("https://github.com/ztomer/multitop/releases/", f"file://{tmp}/releases/")


def test_a_resource_left_without_its_artifact_is_fetched_from_its_new_url(tmp_path):
    rel = "Formula/multitop.rb"
    want = {}
    for arch in ("x86_64", "aarch64"):
        asset = (
            tmp_path
            / "releases"
            / "download"
            / "v0.55.0"
            / f"multitop-agent-{arch}-unknown-linux-musl"
        )
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(arch.encode())
        want[f"multitop-agent-{arch}"] = digest(asset)
    bare = tap_with(tmp_path, rel, _local_releases(tmp_path))
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"x")
    r = bump(bare, rel, "v0.55.0", artifact)
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "after.rb"
    out.write_text(git(bare, "show", f"main:{rel}"))
    after = read(out)
    assert {k: v["sha256"] for k, v in after["resources"].items()} == want, after


def test_a_resource_whose_new_url_does_not_serve_is_refused_and_nothing_pushed(tmp_path):
    rel = "Formula/multitop.rb"
    bare = tap_with(tmp_path, rel, _local_releases(tmp_path))
    before = git(bare, "rev-parse", "main")
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"x")
    r = bump(bare, rel, "v0.55.0", artifact)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "multitop-agent-x86_64" in out, out
    assert git(bare, "rev-parse", "main") == before


def test_a_resource_whose_url_carries_no_version_is_refused(tmp_path):
    rel = "Formula/multitop.rb"
    text = (FIXTURES / rel).read_text()
    text = text.replace("releases/download/v0.54.0/multitop-agent-x86_64", "latest/agent-x86_64")
    bare = tap_with(tmp_path, rel, text)
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"x")
    r = bump(
        bare,
        rel,
        "v0.55.0",
        artifact,
        "--resource",
        f"multitop-agent-x86_64={artifact}",
        "--resource",
        f"multitop-agent-aarch64={artifact}",
    )
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "multitop-agent-x86_64" in out and "no version" in out, out


def test_the_fixtures_are_the_taps_real_files():
    """Every formula and cask the tap had at `ad99f17`, so a new shape there is a new case here."""
    assert SHAPES == [
        "Casks/antiknob.rb",
        "Casks/cadgoose.rb",
        "Casks/divoom-control.rb",
        "Formula/multitop.rb",
        "Formula/ztools.rb",
    ]
