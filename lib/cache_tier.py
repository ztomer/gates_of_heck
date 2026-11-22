#!/usr/bin/env python3
"""Which cache root to use NOW: the removable LARGE tier, or the internal one (GOH.CACHEEVICT).

The owner added a removable, encrypted 1 TB SSD for disposable caches (rules.cache_policy
.removable_tier, 2026-11-22). A removable volume may be absent, locked, full or replaced, so its
path is never written into static config (~/.cargo/config.toml, sccache's config): with the drive
gone, a `mkdir -p` of that path would squat the mount point on the internal disk, fill it
silently, and push the drive to "/Volumes/1TB 1" at its next mount. The root is decided here, at
USE time, and a volume counts only when ALL of these hold:

  (a) it is a mount point whose st_dev differs from /'s (present-but-locked is not mounted, so it
      reads absent);
  (b) `<mount>/cache.noindex` opens as a directory on that same device;
  (c) the marker `cache.noindex/.goh-cache-volume` (key=value lines) names `volume_uuid=` equal to
      the volume's OWN UUID (diskutil), so a copied marker cannot pass, and equal to
      GOH_CACHE_VOLUME_UUID when that pins one volume;
  (d) the root is writable;
  (e) its free space is above the floor, max(FLOOR_GIB, FLOOR_FRACTION of the volume).

The volume is found by its marker across every mount under /Volumes, never by a fixed name. This
module never creates the mount path or `cache.noindex`: only children of a verified root are made,
through `open_root`'s directory fd (lib/cache_move.py), so a drive leaving mid-copy fails the copy
(ENOENT, EIO) instead of landing it on the internal disk.

usage: cache_tier.py [root] [--json]     print `<root>\t<tier>: <reason>`; exit 0 either way
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import json
import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field

CACHE_DIR = "cache.noindex"
MARKER = ".goh-cache-volume"
FLOOR_GIB = 50.0
FLOOR_FRACTION = 0.05
VOLUMES = "/Volumes"
GIB = 1 << 30


def _diskutil_uuid(mount: str) -> str | None:
    """The volume's own UUID, from `diskutil info -plist`; None when diskutil cannot say."""
    try:
        out = subprocess.run(["diskutil", "info", "-plist", mount], capture_output=True,
                             check=False, timeout=20, stdin=subprocess.DEVNULL).stdout  # fmt: skip
        return str(plistlib.loads(out).get("VolumeUUID") or "") or None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _mounts(volumes: str = VOLUMES) -> list[str]:
    try:
        names = sorted(os.listdir(volumes))
    except OSError:
        return []
    return [os.path.join(volumes, n) for n in names if not n.startswith(".")]


@dataclass
class System:
    """The seams a test fakes: mounts, mount-point test, statvfs, the volume's own UUID."""

    mounts: object = _mounts
    ismount: object = os.path.ismount
    statvfs: object = os.statvfs
    volume_uuid: object = _diskutil_uuid
    root_dev: object = field(default=lambda: os.stat("/").st_dev)


@dataclass
class Verdict:
    root: str
    external: bool
    reason: str
    free: int = 0
    total: int = 0


def expected_uuid(environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    return env.get("GOH_CACHE_VOLUME_UUID") or None


def floor_bytes(total: int) -> int:
    return max(int(FLOOR_GIB * GIB), int(total * FLOOR_FRACTION))


def _marker_uuid(fd: int) -> str | None:
    try:
        with open(os.open(MARKER, os.O_RDONLY, dir_fd=fd), encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "volume_uuid":
            return value.strip().upper() or None
    return None


def check(mount: str, want: str | None, sysm: System) -> Verdict:
    """One candidate mount judged against (a) to (e); external=False carries the first failure."""
    fail = lambda why: Verdict("", False, why)  # noqa: E731
    try:
        st = os.stat(mount)
    except OSError:
        return fail(f"{mount} absent (unplugged, or present but locked)")
    if not sysm.ismount(mount) or st.st_dev == sysm.root_dev():
        return fail(f"{mount} is not a mount point (a stray directory on the internal disk)")
    croot = os.path.join(mount, CACHE_DIR)
    try:
        fd = os.open(croot, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as e:
        return fail(f"{croot} unreadable ({e.strerror})")
    try:
        if os.fstat(fd).st_dev != st.st_dev:
            return fail(f"{croot} is on another device than {mount}")
        got = _marker_uuid(fd)
    finally:
        os.close(fd)
    if got is None:
        return fail(f"{croot} has no {MARKER} marker with volume_uuid=")
    if want and got != want.upper():
        return fail(f"marker names {got}, expected {want.upper()}")
    own = (sysm.volume_uuid(mount) or "").upper()
    if own != got:
        return fail(f"marker names {got} but the volume is {own or 'unknown'} (a copied marker)")
    if not os.access(croot, os.W_OK):
        return fail(f"{croot} not writable (read-only mount)")
    v = sysm.statvfs(croot)
    free, total = v.f_bavail * v.f_frsize, v.f_blocks * v.f_frsize
    if free < floor_bytes(total):
        return Verdict("", False, f"{free / GIB:.0f} GiB free on {mount}, under its "
                       f"{floor_bytes(total) / GIB:.0f} GiB floor", free, total)  # fmt: skip
    return Verdict(croot, True, f"mounted, marker matches, writable, {free / GIB:.0f} GiB free",
                   free, total)  # fmt: skip


def resolve(internal: str, environ: dict[str, str] | None = None,
            sysm: System | None = None) -> Verdict:  # fmt: skip
    """The external root when a marked volume passes every check, else `internal` with why not."""
    sysm = sysm or System()
    want = expected_uuid(environ)
    reasons = []
    for mount in sysm.mounts():
        if not os.path.isdir(os.path.join(mount, CACHE_DIR)) and not want:
            continue  # an unrelated volume: not a candidate, not a reason
        verdict = check(mount, want, sysm)
        if verdict.external:
            return verdict
        reasons.append(verdict.reason)
    why = reasons[0] if reasons else "no mounted volume carries a cache.noindex marker"
    return Verdict(internal, False, why)


def open_root(verdict: Verdict) -> int:
    """A directory fd on the verified external root, re-checked to be the volume judged; every
    later write goes through it (`dir_fd=`), never through a path resolved again."""
    if not verdict.external:
        raise ValueError(f"not an external root: {verdict.reason}")
    fd = os.open(verdict.root, os.O_RDONLY | os.O_DIRECTORY)
    if os.fstat(fd).st_dev == os.stat("/").st_dev:
        os.close(fd)
        raise OSError(f"{verdict.root} is on the internal disk now")
    return fd


def internal_root() -> str:
    return os.environ.get("GOH_CACHE_INTERNAL") or os.path.expanduser("~/.cache/goh")


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__.rsplit("usage: ", 1)[1].strip())
        return 0
    args = [a for a in argv if a != "root"]
    verdict = resolve(internal_root())
    tier = "external" if verdict.external else "internal"
    if "--json" in args:
        print(json.dumps({"root": verdict.root, "tier": tier, "reason": verdict.reason}))
    else:
        print(f"{verdict.root}\t{tier}: {verdict.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
