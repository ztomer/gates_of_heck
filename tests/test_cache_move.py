"""Demote, not delete: a cold build dir moves to the large tier and back whole (GOH.CACHEEVICT).

The "drive" is a tmp directory opened by fd, as lib/cache_tier.py's open_root hands it out; nothing
here touches /Volumes or ~/.cargo/build.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cache_move


def _build_dir(base):
    d = base / "build" / "abc"
    (d / "debug" / "deps").mkdir(parents=True)
    (d / "debug" / "deps" / "libx-0123.rlib").write_bytes(b"x" * 5000)
    exe = d / "debug" / "build-script-build"
    exe.write_bytes(b"#!/bin/sh\n")
    exe.chmod(0o755)
    (d / "debug" / "link").symlink_to("deps/libx-0123.rlib")
    for p in [d / "debug" / "deps" / "libx-0123.rlib", exe, d / "debug" / "deps", d / "debug"]:
        os.utime(p, ns=(1_600_000_000_000_000_000, 1_600_000_000_123_456_789))
    return d


def _snapshot(d):
    out = {}
    for dp, dns, fns in os.walk(d):
        for n in dns + fns:
            p = os.path.join(dp, n)
            st = os.lstat(p)
            if os.path.islink(p):
                body = os.readlink(p)
            elif os.path.isfile(p):
                with open(p, "rb") as handle:
                    body = handle.read()
            else:
                body = None
            out[os.path.relpath(p, d)] = (st.st_mode, st.st_mtime_ns, body)
    return out


@pytest.fixture
def drive(tmp_path):
    root = tmp_path / "drive" / "cache.noindex"
    root.mkdir(parents=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    yield root, fd
    os.close(fd)


def test_round_trip_keeps_bytes_modes_mtimes_and_links(tmp_path, drive):
    root, fd = drive
    d = _build_dir(tmp_path)
    before = _snapshot(d)
    moved = cache_move.demote(str(d), fd, "build")
    assert moved >= 5000 and not d.exists()
    assert cache_move.demoted(str(d), fd, "build")
    origin = root / "demoted" / "build" / cache_move.slot(str(d)) / ".goh-origin"
    assert origin.read_text().strip() == str(d)
    assert cache_move.promote(str(d), fd, "build") == moved
    assert _snapshot(d) == before
    assert not cache_move.demoted(str(d), fd, "build")


def test_a_live_dir_wins_over_its_demoted_copy(tmp_path, drive):
    _root, fd = drive
    d = _build_dir(tmp_path)
    cache_move.demote(str(d), fd, "build")
    d.mkdir()
    assert cache_move.promote(str(d), fd, "build") == 0
    assert os.listdir(d) == []


def test_a_drive_gone_mid_copy_leaves_the_source_whole(tmp_path, drive, monkeypatch):
    _root, fd = drive
    d = _build_dir(tmp_path)
    before = _snapshot(d)
    real = cache_move._copy_file
    calls = []

    def dying(*a):
        if calls:
            raise OSError(5, "Input/output error")  # EIO: the drive went away
        calls.append(1)
        real(*a)

    monkeypatch.setattr(cache_move, "_copy_file", dying)
    with pytest.raises(OSError):
        cache_move.demote(str(d), fd, "build")
    assert _snapshot(d) == before
    assert not cache_move.demoted(str(d), fd, "build")


def test_writes_follow_the_fd_not_the_path(tmp_path, drive):
    """The root path vanishes after the fd opened (the drive left, a stray dir took its name): the
    copy fails or lands in the opened (now unlinked) directory, never at the path."""
    root, fd = drive
    d = _build_dir(tmp_path)
    shutil.rmtree(root)
    with pytest.raises(OSError):
        cache_move.demote(str(d), fd, "build")
    assert not root.exists() and d.exists()


def test_promote_of_nothing_is_zero(tmp_path, drive):
    _root, fd = drive
    assert cache_move.promote(str(tmp_path / "never"), fd, "build") == 0


def _tier_at(monkeypatch, root):
    import cache_tier

    v = cache_tier.Verdict(str(root), True, "test drive")
    monkeypatch.setattr(cache_move.cache_tier, "resolve", lambda *a, **k: v)
    monkeypatch.setattr(cache_move.cache_tier, "open_root",
                        lambda v: os.open(v.root, os.O_RDONLY | os.O_DIRECTORY))  # fmt: skip


def test_promote_for_brings_the_workspace_build_dir_back(tmp_path, drive, monkeypatch):
    root, fd = drive
    d = _build_dir(tmp_path)
    before = _snapshot(d)
    cache_move.demote(str(d), fd, "build")
    _tier_at(monkeypatch, root)
    said = cache_move.promote_for(str(tmp_path), build_dir=lambda c: str(d))
    assert "back from the large tier" in said and _snapshot(d) == before


def test_promote_for_asks_cargo_nothing_when_nothing_is_demoted(tmp_path, drive, monkeypatch):
    root, _fd = drive
    _tier_at(monkeypatch, root)
    asked = []
    assert cache_move.promote_for(str(tmp_path), build_dir=asked.append) == ""
    assert asked == []


def test_the_suite_never_sees_the_real_volumes(monkeypatch):
    import cache_tier

    monkeypatch.setenv("GOH_CACHE_VOLUMES", "/nonexistent/goh-volumes")
    assert cache_tier._mounts() == []
