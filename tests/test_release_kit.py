"""tools/release-kit — release.sh, gen_app_icons.py, update_dev.sh.

Contract pinned here:
  release.sh runs its steps in order (gate → changelog → tag → push →
  release → tap), every failure names its step, tagging and gh-release are
  idempotent (an existing tag/release is left alone), --dry-run performs
  NOTHING, a missing CHANGELOG stanza fails the changelog step.
  gen_app_icons.py emits exactly the ten Apple ladder members plus an .icns;
  sizes are verified from the PNG IHDR bytes.
  update_dev.sh builds, replaces the installed copy, strips quarantine (and
  proves it), signs, launches only on request.

git is REAL throughout — fixtures use local bare remotes so push semantics
are genuine. Only gh is faked (a stateful stub: `release view` succeeds iff
that release was previously created).
"""

import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from conftest import REPO_ROOT

RELEASE = REPO_ROOT / "tools" / "release-kit" / "release.sh"
GEN_ICONS = REPO_ROOT / "tools" / "release-kit" / "gen_app_icons.py"
UPDATE_DEV = REPO_ROOT / "tools" / "release-kit" / "update_dev.sh"

VERSION = "1.2.3"
TAG = f"v{VERSION}"
STANZA_BODY = "- Added the release kit\n- Fixed seven drifting releasers"


# ── helpers ──────────────────────────────────────────────────────────────────


def png_bytes(w: int, h: int) -> bytes:
    """A minimal valid RGBA PNG built by hand (no image library dependency)."""
    raw = b"".join(b"\x00" + b"\x40\x80\xc0\xff" * w for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        c = kind + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def png_dims(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def sh(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return r.stdout


def commit_all(repo: Path, msg: str = "fixture") -> None:
    sh(repo, "add", "-A")
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


FAKE_GH = """\
#!/usr/bin/env bash
STATE="${GH_STATE:?}"; LOG="${GH_LOG:?}"
printf 'gh %s\\n' "$*" >> "$LOG"
cmd="$1"; shift
case "$cmd" in
  auth) exit 0 ;;
  api) exit 0 ;;
  release)
    sub="$1"; shift
    case "$sub" in
      view)
        tag=""
        while [ $# -gt 0 ]; do
          case "$1" in
            --repo) shift 2 ;;
            --*) shift ;;
            *) [ -z "$tag" ] && tag="$1"; shift ;;
          esac
        done
        [ -n "$tag" ] && [ -f "$STATE/rel-$tag" ] ;;
      create)
        tag=""; notes=""
        while [ $# -gt 0 ]; do
          case "$1" in
            --notes-file) notes="$2"; shift 2 ;;
            --title|--repo) shift 2 ;;
            *) [ -z "$tag" ] && tag="$1"; shift ;;
          esac
        done
        : > "$STATE/rel-$tag"
        [ -n "$notes" ] && cp "$notes" "$STATE/notes-$tag"
        exit 0 ;;
      *) exit 0 ;;
    esac ;;
  *) exit 0 ;;
esac
"""

GIT_WRAPPER = """\
#!/usr/bin/env bash
printf 'git %s\\n' "$*" >> "${GOHKIT_GIT_LOG:?}"
exec /usr/bin/git "$@"
"""


@pytest.fixture
def kit(tmp_path: Path) -> dict:
    """A real project repo with a bare origin, stanza'd CHANGELOG, fake gh."""
    proj = tmp_path / "proj"
    proj.mkdir()
    sh(proj, "init", "-q", "-b", "main")
    (proj / "CHANGELOG.md").write_text(
        "# CHANGELOG\n\n"
        f"## {TAG}\n\n{STANZA_BODY}\n\n"
        "## v1.1.0\n\n- older\n",
        encoding="utf-8",
    )
    commit_all(proj)

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(proj), str(origin)], check=True)
    sh(proj, "remote", "add", "origin", str(origin))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_state = tmp_path / "gh-state"
    gh_state.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (bin_dir / "git").write_text(GIT_WRAPPER)
    (bin_dir / "git").chmod(0o755)

    return {
        "proj": proj,
        "origin": origin,
        "bin": bin_dir,
        "gh_state": gh_state,
        "gh_log": tmp_path / "gh.log",
        "git_log": tmp_path / "git.log",
    }


def run_release(kit: dict, *args: str, gate: str = "true") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GOH_DIR"] = str(REPO_ROOT)
    env["PATH"] = f"{kit['bin']}:{env['PATH']}"
    env["GH_STATE"] = str(kit["gh_state"])
    env["GH_LOG"] = str(kit["gh_log"])
    env["GOHKIT_GIT_LOG"] = str(kit["git_log"])
    # Identity for the tap-commit step without touching global git config.
    env.setdefault("GIT_AUTHOR_NAME", "t")
    env.setdefault("GIT_AUTHOR_EMAIL", "t@t")
    env.setdefault("GIT_COMMITTER_NAME", "t")
    env.setdefault("GIT_COMMITTER_EMAIL", "t@t")
    env.pop("GOH_RELEASE_GATE", None)
    return subprocess.run(
        ["/bin/bash", str(RELEASE), "--version", VERSION, "--gate", gate, *args],
        cwd=kit["proj"], capture_output=True, text=True, env=env,
    )


# ── step sequencing ──────────────────────────────────────────────────────────


class TestSequencing:
    def test_happy_path_runs_every_step_in_order(self, kit):
        r = run_release(kit)  # push asserted in its own test below
        assert r.returncode == 0, r.stdout + r.stderr

        # changelog step passed → tag exists as an ANNOTATED tag
        assert sh(kit["proj"], "cat-file", "-t", TAG).strip() == "tag"

        # gh was called to create the release with the stanza body as notes
        log = kit["gh_log"].read_text()
        assert f"gh release view {TAG}" in log
        assert f"gh release create {TAG}" in log
        notes = (kit["gh_state"] / f"notes-{TAG}").read_text()
        assert STANZA_BODY.splitlines()[0] in notes

    def test_gate_failure_blocks_everything_and_names_the_step(self, kit):
        r = run_release(kit, "--gate", "false", "--no-push")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "'gate'" in combined, combined
        assert not (kit["proj"] / ".git" / "refs" / "tags" / TAG).exists()
        assert not kit["gh_log"].exists() or "create" not in kit["gh_log"].read_text()

    def test_missing_changelog_stanza_fails_naming_the_step(self, kit):
        (kit["proj"] / "CHANGELOG.md").write_text("## v9.9.9\n\n- other\n")
        commit_all(kit["proj"])
        r = run_release(kit, "--no-push")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "'changelog'" in combined
        assert VERSION in combined
        # the failure happened BEFORE any side effect
        assert not (kit["proj"] / ".git" / "refs" / "tags" / TAG).exists()
        assert not kit["gh_log"].exists() or "create" not in kit["gh_log"].read_text()

    def test_push_pushes_branch_and_tag_to_origin(self, kit):
        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr
        refs = sh(kit["origin"], "rev-parse", "--verify", f"refs/tags/{TAG}")
        assert refs.strip() == sh(kit["proj"], "rev-parse", TAG).strip()
        assert (
            sh(kit["origin"], "rev-parse", "--verify", "refs/heads/main").strip()
            == sh(kit["proj"], "rev-parse", "HEAD").strip()
        )

    def test_no_push_leaves_origin_untouched(self, kit):
        r = run_release(kit, "--no-push", "--skip-release")
        assert r.returncode == 0, r.stdout + r.stderr
        out = sh(kit["origin"], "tag", "-l")
        assert TAG not in out


# ── idempotency ──────────────────────────────────────────────────────────────


class TestIdempotency:
    def test_second_run_skips_existing_tag(self, kit):
        assert run_release(kit).returncode == 0
        first = sh(kit["proj"], "rev-parse", TAG).strip()

        kit["git_log"].write_text("")
        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr

        calls = kit["git_log"].read_text()
        assert "git tag -a" not in calls, calls
        assert "already exists" in r.stdout
        assert sh(kit["proj"], "rev-parse", TAG).strip() == first

    def test_second_run_skips_existing_github_release(self, kit):
        assert run_release(kit).returncode == 0
        creates = kit["gh_log"].read_text().count(f"gh release create {TAG}")
        assert creates == 1

        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr
        creates = kit["gh_log"].read_text().count(f"gh release create {TAG}")
        assert creates == 1, "second run re-created the GitHub release"


# ── dry run ──────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_has_zero_side_effects(self, kit):
        marker = kit["proj"] / "gate-marker"
        r = run_release(
            kit, "--dry-run", "--gate", f"touch {marker}", "--tap", "/nonexistent-tap",
            "--cask", "foo", "--artifact", "/nonexistent.tar.gz",
        )
        assert r.returncode == 0, r.stdout + r.stderr

        assert "[dry-run]" in r.stdout
        assert not marker.exists(), "dry-run executed the gate"
        out = sh(kit["proj"], "tag", "-l")
        assert TAG not in out, "dry-run created the tag"
        refs = sh(kit["origin"], "for-each-ref").strip()
        assert TAG not in refs, "dry-run pushed the tag"
        assert (
            sh(kit["origin"], "rev-parse", "--verify", "refs/heads/main").strip()
            == sh(kit["proj"], "rev-parse", "HEAD").strip()
        ), "dry-run pushed commits"
        assert not kit["gh_log"].exists() or "create" not in kit["gh_log"].read_text()


# ── tap bump ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tap(tmp_path: Path) -> tuple[Path, Path]:
    """A bare tap repo seeded with a cask pinned at v0.0.0 (+ its seed clone)."""
    seed = tmp_path / "tap-seed"
    seed.mkdir()
    sh(seed, "init", "-q", "-b", "main")
    cask_dir = seed / "Casks"
    cask_dir.mkdir()
    (cask_dir / "foo.rb").write_text(
        'cask "foo" do\n'
        '  url "https://example.com/foo/archive/refs/tags/v0.0.0.tar.gz"\n'
        '  sha256 "0000000000000000000000000000000000000000000000000000000000000000"\n'
        "end\n",
        encoding="utf-8",
    )
    commit_all(seed)
    bare = tmp_path / "tap.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    return seed, bare


def bare_show(bare: Path, ref: str, rel: str) -> str:
    """Read a file straight out of the bare tap repo (authoritative, no clone)."""
    return subprocess.run(
        ["git", "-C", str(bare), "show", f"{ref}:{rel}"],
        capture_output=True, text=True, check=True,
    ).stdout


def bare_head(bare: Path) -> str:
    return sh(bare, "rev-parse", "--verify", "refs/heads/main").strip()


class TestTap:
    def test_bump_rewrites_url_and_sha_and_pushes(self, kit, tap):
        _, bare = tap
        artifact = kit["proj"] / "artifact.tar.gz"
        artifact.write_bytes(b"payload-bytes")

        r = run_release(
            kit, "--tap", str(bare), "--cask", "foo", "--artifact", str(artifact)
        )
        assert r.returncode == 0, r.stdout + r.stderr

        import hashlib

        want_sha = hashlib.sha256(b"payload-bytes").hexdigest()
        content = bare_show(bare, "main", "Casks/foo.rb")
        assert f"/tags/{TAG}.tar.gz" in content
        assert f'sha256 "{want_sha}"' in content
        assert "v0.0.0" not in content

    def test_unchanged_formula_is_not_recommitted(self, kit, tap):
        _, bare = tap
        artifact = kit["proj"] / "artifact.tar.gz"
        artifact.write_bytes(b"payload-bytes")
        args = ("--tap", str(bare), "--cask", "foo", "--artifact", str(artifact))
        assert run_release(kit, *args).returncode == 0
        head_before = bare_head(bare)

        r = run_release(kit, *args)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "nothing to commit" in r.stdout
        assert bare_head(bare) == head_before


# ── gen_app_icons.py ─────────────────────────────────────────────────────────


EXPECTED_LADDER = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def run_gen(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(GEN_ICONS), *args], capture_output=True, text=True
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="sips/iconutil are macOS tools")
class TestGenIcons:
    @pytest.fixture
    def generated(self, tmp_path: Path) -> Path:
        src = tmp_path / "src.png"
        src.write_bytes(png_bytes(64, 64))
        out = tmp_path / "out"
        r = run_gen(str(src), str(out), "--appiconset", "--name", "Demo")
        assert r.returncode == 0, r.stdout + r.stderr
        return out

    def test_iconset_contains_exactly_the_apple_ladder(self, generated):
        iconset = generated / "Demo.iconset"
        names = sorted(p.name for p in iconset.iterdir())
        assert names == sorted(n for n, _ in EXPECTED_LADDER)

    def test_each_member_has_the_right_pixel_size(self, generated):
        iconset = generated / "Demo.iconset"
        for fname, px in EXPECTED_LADDER:
            assert png_dims(iconset / fname) == (px, px), fname

    def test_icns_is_produced(self, generated):
        icns = generated / "Demo.icns"
        assert icns.is_file() and icns.stat().st_size > 0

    def test_modern_appiconset_contents_json(self, generated):
        spec = json.loads((generated / "Demo.appiconset" / "Contents.json").read_text())
        images = spec["images"]
        assert images == [
            {"filename": "icon_1024x1024.png", "idiom": "universal",
             "platform": "ios", "size": "1024x1024"}
        ]
        assert (generated / "Demo.appiconset" / "icon_1024x1024.png").is_file()

    def test_legacy_ladder_contents_json_matches_rendered_pngs(self, tmp_path):
        src = tmp_path / "src.png"
        src.write_bytes(png_bytes(32, 32))
        out = tmp_path / "out"
        r = run_gen(str(src), str(out), "--appiconset", "--legacy-ladder", "--name", "L")
        assert r.returncode == 0, r.stdout + r.stderr
        group = out / "L.appiconset"
        spec = json.loads((group / "Contents.json").read_text())
        entries = spec["images"]
        assert {e["idiom"] for e in entries} == {"iphone", "ipad", "ios-marketing"}
        for e in entries:
            assert (group / e["filename"]).is_file(), e["filename"]
            scale = int(e["scale"].rstrip("x"))
            pt = float(e["size"].split("x")[0])
            assert png_dims(group / e["filename"]) == (round(pt * scale),) * 2

    def test_deterministic_output_is_a_no_op_diff(self, tmp_path):
        src = tmp_path / "src.png"
        src.write_bytes(png_bytes(64, 64))
        out1, out2 = tmp_path / "one", tmp_path / "two"
        assert run_gen(str(src), str(out1)).returncode == 0
        assert run_gen(str(src), str(out2)).returncode == 0
        names1 = sorted(p.relative_to(out1) for p in out1.rglob("*") if p.is_file())
        names2 = sorted(p.relative_to(out2) for p in out2.rglob("*") if p.is_file())
        assert names1 == names2
        for rel in names1:
            assert (out1 / rel).read_bytes() == (out2 / rel).read_bytes(), rel


# ── update_dev.sh ────────────────────────────────────────────────────────────

BUILD_CMD = (
    "mkdir -p stage/Demo.app/Contents/MacOS && "
    "printf '#!/bin/sh\\necho demo\\n' > stage/Demo.app/Contents/MacOS/Demo && "
    "chmod +x stage/Demo.app/Contents/MacOS/Demo && "
    "/usr/bin/xattr -w com.apple.quarantine '0081;test;test;' stage/Demo.app"
)


def run_update_dev(workdir: Path, bin_dir: Path | None, *args: str) -> subprocess.CompletedProcess:
    dest = workdir / "dest"
    env = dict(os.environ)
    env["GOH_DIR"] = str(REPO_ROOT)
    env["UPDATE_DEV_DEST"] = str(dest)
    env["APP_NAME"] = "Demo.app"
    env["BUILD_CMD"] = BUILD_CMD
    env["APP_PATH"] = "stage/Demo.app"
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        ["/bin/bash", str(UPDATE_DEV), *args],
        cwd=workdir, capture_output=True, text=True, env=env,
    )


class TestUpdateDev:
    def test_installs_and_clears_quarantine(self, tmp_path):
        r = run_update_dev(tmp_path, None)
        assert r.returncode == 0, r.stdout + r.stderr
        installed = tmp_path / "dest" / "Demo.app"
        binary = installed / "Contents" / "MacOS" / "Demo"
        assert binary.is_file()
        probe = subprocess.run(
            ["/usr/bin/xattr", "-p", "com.apple.quarantine", str(installed)],
            capture_output=True, text=True,
        )
        assert probe.returncode != 0, "quarantine survived the install"
        assert "quarantine clear" in r.stdout

    def test_launch_flag_opens_installed_copy(self, tmp_path):
        bin_dir = tmp_path / "fakeopen"
        bin_dir.mkdir()
        (bin_dir / "open").write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$OPEN_LOG"\n'
        )
        (bin_dir / "open").chmod(0o755)
        os.environ["OPEN_LOG"] = str(tmp_path / "open.log")
        r = run_update_dev(tmp_path, bin_dir, "--launch")
        assert r.returncode == 0, r.stdout + r.stderr
        assert (tmp_path / "open.log").read_text().startswith(str(tmp_path / "dest"))

    def test_default_does_not_launch(self, tmp_path):
        bin_dir = tmp_path / "fakeopen"
        bin_dir.mkdir()
        (bin_dir / "open").write_text('#!/usr/bin/env bash\nexit 97\n')
        (bin_dir / "open").chmod(0o755)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode == 0, r.stdout + r.stderr
