"""Each test's own profile, kept for the next tree that builds the same binary (GOH.COVCARRY3 c).

WHY. Under coverage a test that ran the native binary was never carried across trees: its run is
coverage the floor counts (tests/_land_verdicts.py), so every land re-ran about 1800 binary-running
nodes against the instrumented goh even when its diff left that binary's bytes alone -- 87% of
commits (D-0011, option 4). The instrumented run already merges each test's raw profiles into one
sparse indexed file (tests/_prof_record.py); this store keeps that file, so a later run of an
unchanged binary can put the stored profile where the report reads it instead of running the test.

THE KEY is (repo, tree, tools, binary): the tree and tools the run judged (lib/gate_identity.py,
a clean tree only) and `bin_id`, the content hash of the instrumented binaries the run drove
($GOH_COVERAGE_BIN_DIR). The binary is in the key, so a profile is never read against bytes that
did not make it: a diff that changes a build input changes the binary and finds nothing (the build
is path-independent, so two lanes of one tree share it: tests/test_coverage_bin_identity.py). A
node is looked up by `node_hash`, its file `<root>/profstore/<key>/<node hash>.profdata`.

WHO CARRIES. tests/_land_verdicts.py `_carry`: a binary-running node a source record passed, that
tools/affected_tests.py does not choose for the diff, whose profile the store holds under the
source tree and THIS run's binary -- one audited use of the `carry` path, so one source in ten is
a shadow whose nodes run anyway. Its profile is linked into the run's profile directory (the
report merges it: the floor judges a complete profile, the owner's term 1) and stored again under
this tree, so the next land carries it on. The idle lander's and the ship's fresh full
instrumented run carries nothing (terms 2 and 3 stay: lib/full_run.py, lib/prof_carry.py).

THE KILL SWITCH, GOH_PROFCARRY_TESTS=off: nothing stored, nothing carried; and the `profcarry`
audit turned off (a fresh run disagreed with a land's claim) stops every coverage reuse already.
Bounded: the newest KEEP keys are kept, older ones removed at each store. Pinned by
tests/test_prof_store.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reuse  # lib/, beside this file

KILL = "GOH_PROFCARRY_TESTS"
KEEP = 8  # trees kept: at most ~1800 nodes x 19 KB each (D-0011), carried ones hard links
_ids: dict[str, str] = {}


def enabled() -> bool:
    return os.environ.get(KILL, "on") != "off"


def node_hash(nodeid: str) -> str:
    return hashlib.sha256(nodeid.encode()).hexdigest()[:20]


def bin_id(bin_dir: str | os.PathLike) -> str:
    """The content hash of every executable file in `bin_dir` (name and bytes); "" for none."""
    d = Path(bin_dir)
    if str(d) in _ids:
        return _ids[str(d)]
    h = hashlib.sha256()
    seen = False
    for f in sorted(d.iterdir()) if d.is_dir() else []:
        if f.is_file() and os.access(f, os.X_OK):
            seen = True
            h.update(f.name.encode() + b"\0")
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            h.update(b"\0")
    _ids[str(d)] = h.hexdigest() if seen else ""
    return _ids[str(d)]


def key(repo: str, tree: str, tools: str, binary: str) -> str:
    text = json.dumps({"repo": repo, "tree": tree, "tools": tools, "bin": binary}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _dir(k: str) -> Path:
    return reuse.root() / "profstore" / k


def get(k: str, node: str) -> Path | None:
    f = _dir(k) / f"{node_hash(node)}.profdata"
    return f if f.is_file() else None


def held(k: str, nodes) -> set[str]:
    """The nodes of `nodes` whose profile the store holds under `k`."""
    d = _dir(k)
    have = {p.stem for p in d.glob("*.profdata")} if d.is_dir() else set()
    return {n for n in nodes if node_hash(n) in have}


def _link(src: Path, dest: Path) -> None:
    """`src` at `dest`, atomically: a hard link, else a copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def put(k: str, node: str, src: Path) -> None:
    """Keep `src` (one node's indexed profile) under `k`."""
    _link(src, _dir(k) / f"{node_hash(node)}.profdata")
    os.utime(_dir(k))


def place(src: Path, dest: Path) -> None:
    """A stored profile where a run's report reads it."""
    _link(src, dest)


def prune(keep: int = KEEP) -> int:
    """Remove all but the newest `keep` keys; how many went."""
    base = reuse.root() / "profstore"
    if not base.is_dir():
        return 0
    dirs = sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime)
    gone = dirs[: max(0, len(dirs) - keep)]
    for d in gone:
        shutil.rmtree(d, ignore_errors=True)
    return len(gone)
