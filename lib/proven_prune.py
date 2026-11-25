#!/usr/bin/env python3
"""proven_prune.py <goh-proven dir> -- drop the identity sidecars no record needs, in one pass.

THE CLASS (GOH.PROVENSCAN, 2026-11-25). gates/_proven_why.sh keeps beside each proven record the
identity lines it was keyed under (`.id/<key>`) and the goh manifest those lines stand for
(`.id/m-<hash>`), and pruned them in bash after EVERY recorded step: a `cat`, a `grep`, a `shasum`
and a `cut` per sidecar. With 420 sidecars that was 6.9 s a record, 69-82 s of a ten-step gate
after its last step ended, under the land lock. Read here once a gate, by one process.

What goes: a sidecar whose record is gone, and a manifest no kept sidecar stands for. What stays:
everything else, dot files (a write in flight) included. The manifests are listed BEFORE the
sidecars, and a record is written before its sidecar, its sidecar before its manifest: a manifest
listed here has a sidecar the second listing sees. A note that starts after it and finds its
manifest already kept can still lose that manifest: a miss then names no goh files, nothing else.
Exit 0; 1 with the reason when the store cannot be read or an entry cannot be removed.

Called by `proven_note_identity` (`gates/_proven_why.sh`), once per gate. Pinned by
`test_proven_scan.py`.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import hashlib
import os
import sys
from collections.abc import Callable, Iterable

MANIFEST = "m-"


def manifest_key(identity: bytes) -> str:
    """The manifest an identity's goh lines stand for: `_proven_goh_mkey`'s name, byte for byte."""
    goh = b"".join(x + b"\n" for x in identity.split(b"\n") if x.startswith(b"goh "))
    return MANIFEST + hashlib.sha256(goh).hexdigest()


def doomed(
    manifests: Iterable[str],
    sidecars: Iterable[str],
    recorded: Callable[[str], bool],
    identity: Callable[[str], bytes | None],
) -> list[str]:
    """The entries to remove: sidecars with no record, then manifests no kept sidecar names. An
    identity of None is a sidecar another gate removed since it was listed."""
    gone, kept = [], set()
    for name in sidecars:
        if not recorded(name):
            gone.append(name)
        elif (text := identity(name)) is not None:
            kept.add(manifest_key(text))
    return gone + [m for m in manifests if m not in kept]


def _listed(ids: str, manifests: bool) -> list[str]:
    return [
        e.name
        for e in os.scandir(ids)
        if e.name.startswith(MANIFEST) is manifests and not e.name.startswith(".") and e.is_file()
    ]


def _read(ids: str, name: str) -> bytes | None:
    try:
        with open(os.path.join(ids, name), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def prune(store: str) -> None:
    """Prune one `goh-proven` directory's `.id/`."""
    ids = os.path.join(store, ".id")
    manifests = _listed(ids, manifests=True)  # first: see the header
    sidecars = _listed(ids, manifests=False)
    for name in doomed(
        manifests,
        sidecars,
        lambda n: os.path.isfile(os.path.join(store, n)),
        lambda n: _read(ids, n),
    ):
        try:
            os.unlink(os.path.join(ids, name))
        except FileNotFoundError:
            continue


def main(argv: list[str]) -> int:
    try:
        prune(argv[0])
    except OSError as e:
        print(f"proven_prune: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
