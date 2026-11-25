"""The objects of the adds and commits a test makes without git (tests/_quiet_git.py), as one pack
written when something can ask for them (GOH.FSEVQUIET).

Each add and each commit wrote a pack and its idx, four file events with their removal; a fixture of
five commits made ten. They are kept in this process, per repository, and written as one pack when a
process starts (git's own, a gate's, a fork), or the test's process looks into a `.git`, copies or
renames anything -- and never, for a repository its test only removes.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import struct
import subprocess
import sys
import threading
import zlib
from collections.abc import Iterator, Mapping

PENDING: dict[str, list[tuple[str, bytes]]] = {}  # git dir -> objects
LOCK = threading.RLock()
_ASKS = {"os.posix_spawn", "os.system", "os.fork", "os.forkpty", "os.exec", "os.rename",
         "shutil.move", "goh.clone_tree"}  # fmt: skip
_LOOKS = {"open", "os.listdir", "os.scandir", "shutil.copyfile", "os.link"}
_OWN = threading.local()


def is_quiet() -> bool:
    return getattr(_OWN, "on", False)


@contextlib.contextmanager
def quietly() -> Iterator[None]:
    """What this thread opens and starts here reads no pending object."""
    was, _OWN.on = is_quiet(), True
    try:
        yield
    finally:
        _OWN.on = was


def own(argv: list[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[bytes]:
    """A git that reads no object."""
    with quietly():
        return subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, check=False,
                              env=dict(env))  # fmt: skip


def keep(git: str, objects: list[tuple[str, bytes]]) -> None:
    with LOCK:
        PENDING.setdefault(git, []).extend(objects)


def flush() -> None:
    """Every object made here is on the disk."""
    with LOCK, quietly():
        while PENDING:
            git, objects = PENDING.popitem()
            if os.path.isdir(os.path.join(git, "objects")):
                _pack(git, objects)


def drop(path: str) -> None:
    """The repositories under PATH are about to be removed or put back (tests/_tree_pool.py)."""
    with LOCK:
        for git in [g for g in PENDING if g.startswith(path + os.sep)]:
            del PENDING[git]


def _audit(event: str, args: tuple) -> None:
    if not PENDING or is_quiet():
        return
    if event in _ASKS or (event in _LOOKS and any(".git" in str(a) for a in args[:2])):
        flush()


def watch() -> None:
    sys.addaudithook(_audit)


def _sha(kind: str, data: bytes) -> bytes:
    return hashlib.sha1(b"%s %d\0" % (kind.encode(), len(data)) + data).digest()


def _put(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _pack(git: str, objects: list[tuple[str, bytes]]) -> None:
    """OBJECTS as one pack of the repository GIT, and its idx (version 2)."""
    rows, body = [], bytearray(b"")
    kinds = {"commit": 1, "tree": 2, "blob": 3}
    for kind, data in {_sha(*o): o for o in objects}.values():
        size, start = len(data), 12 + len(body)
        first, size = (kinds[kind] << 4) | (size & 15), size >> 4
        head = bytearray()
        while size:
            head.append(first | 0x80)
            first, size = size & 127, size >> 7
        head.append(first)
        raw = bytes(head) + zlib.compress(data, 1)
        body += raw
        rows.append((_sha(kind, data), zlib.crc32(raw), start))
    rows.sort()
    pack = struct.pack(">4sII", b"PACK", 2, len(rows)) + bytes(body)
    trailer = hashlib.sha1(pack).digest()
    idx = b"\xfftOc" + struct.pack(">I", 2)
    idx += b"".join(struct.pack(">I", sum(r[0][0] <= i for r in rows)) for i in range(256))
    idx += b"".join(r[0] for r in rows)
    idx += b"".join(struct.pack(">I", r[1]) for r in rows)
    idx += b"".join(struct.pack(">I", r[2]) for r in rows) + trailer
    at = os.path.join(git, "objects", "pack")
    os.makedirs(at, exist_ok=True)
    name = os.path.join(at, f"pack-{trailer.hex()}")
    if not os.path.exists(f"{name}.idx"):
        _put(f"{name}.pack", pack + trailer)
        _put(f"{name}.idx", idx + hashlib.sha1(idx).digest())
