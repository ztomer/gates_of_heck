"""The removable cache tier is decided at USE time, never by static config (GOH.CACHEEVICT).

Every case runs on tmp directories behind lib/cache_tier.py's System seams (mounts, mount-point
test, the root device, statvfs, the volume's own UUID); nothing here touches /Volumes.
"""

from __future__ import annotations

import contextlib
import os
import sys
from types import SimpleNamespace

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cache_tier

UUID = "0CEF6FFE-0215-4BDB-BB7E-A0BE39A04265"
GIB = 1 << 30


def _vfs(free_gib: float, total_gib: float = 1000):
    return lambda _p: SimpleNamespace(f_bavail=int(free_gib * GIB) // 4096, f_frsize=4096,
                                      f_blocks=int(total_gib * GIB) // 4096)  # fmt: skip


def _volume(tmp_path, name="1TB", marker=f"volume_uuid={UUID}\npurpose=caches\n"):
    mount = tmp_path / name
    (mount / "cache.noindex").mkdir(parents=True)
    if marker is not None:
        (mount / "cache.noindex" / ".goh-cache-volume").write_text(marker)
    return mount


def _sys(mounts, mounted=True, own=UUID, free=900.0, root_dev=None):
    return cache_tier.System(
        mounts=lambda: [str(m) for m in mounts],
        ismount=lambda p: mounted,
        statvfs=_vfs(free),
        volume_uuid=lambda p: own,
        root_dev=root_dev or (lambda: -1 if mounted else os.stat(str(mounts[0])).st_dev),
    )


def test_a_marked_mounted_writable_volume_is_the_root(tmp_path):
    mount = _volume(tmp_path)
    v = cache_tier.resolve("/int", {}, _sys([mount]))
    assert v.external and v.root == str(mount / "cache.noindex"), v.reason


def test_a_remount_under_another_name_still_resolves(tmp_path):
    other = tmp_path / "Other"
    other.mkdir()
    mount = _volume(tmp_path, name="1TB 1")
    v = cache_tier.resolve("/int", {}, _sys([other, mount]))
    assert v.external and "1TB 1" in v.root


def test_absent_drive_is_internal_with_a_reason(tmp_path):
    v = cache_tier.resolve("/int", {"GOH_CACHE_VOLUME_UUID": UUID},
                           _sys([tmp_path / "1TB"]))  # fmt: skip
    assert not v.external and v.root == "/int"
    assert "absent" in v.reason and "locked" in v.reason


def test_a_stray_directory_at_the_mount_path_is_not_a_mount(tmp_path):
    mount = _volume(tmp_path)
    v = cache_tier.resolve("/int", {}, _sys([mount], mounted=False))
    assert not v.external and "not a mount point" in v.reason


def test_same_device_as_root_is_refused_even_if_called_a_mount(tmp_path):
    mount = _volume(tmp_path)
    dev = os.stat(str(mount)).st_dev
    v = cache_tier.resolve("/int", {}, _sys([mount], root_dev=lambda: dev))
    assert not v.external and "not a mount point" in v.reason


def test_a_wrong_or_missing_marker_is_refused(tmp_path):
    wrong = _volume(tmp_path, marker="volume_uuid=11111111-2222-3333-4444-555555555555\n")
    v = cache_tier.resolve("/int", {"GOH_CACHE_VOLUME_UUID": UUID}, _sys([wrong]))
    assert not v.external and "expected" in v.reason
    bare = _volume(tmp_path, name="bare", marker=UUID)  # the whole-file form is not key=value
    v = cache_tier.resolve("/int", {}, _sys([bare]))
    assert not v.external and "marker" in v.reason


def test_a_copied_marker_on_another_volume_is_refused(tmp_path):
    mount = _volume(tmp_path)
    v = cache_tier.resolve("/int", {}, _sys([mount], own="DEADBEEF-0000-0000-0000-000000000000"))
    assert not v.external and "copied marker" in v.reason


def test_a_full_volume_is_refused(tmp_path):
    mount = _volume(tmp_path)
    v = cache_tier.resolve("/int", {}, _sys([mount], free=49.0))
    assert not v.external and "floor" in v.reason
    assert cache_tier.floor_bytes(4000 * GIB) == 200 * GIB  # 5 % of a large volume


def test_a_read_only_root_is_refused(tmp_path):
    mount = _volume(tmp_path)
    os.chmod(mount / "cache.noindex", 0o555)
    try:
        v = cache_tier.resolve("/int", {}, _sys([mount]))
    finally:
        os.chmod(mount / "cache.noindex", 0o755)
    assert not v.external and "not writable" in v.reason


def test_the_resolver_never_creates_the_root(tmp_path):
    """With the drive gone, its parent writable, nothing is made at the mount path."""
    gone = tmp_path / "1TB"
    v = cache_tier.resolve("/int", {"GOH_CACHE_VOLUME_UUID": UUID}, _sys([gone]))
    assert not v.external and not gone.exists()
    with contextlib.suppress(ValueError):
        cache_tier.open_root(v)
    assert not gone.exists() and os.listdir(tmp_path) == []


def test_unrelated_volumes_are_no_candidates(tmp_path):
    other = tmp_path / "Other"
    other.mkdir()
    v = cache_tier.resolve("/int", {}, _sys([other]))
    assert not v.external and "no mounted volume" in v.reason
