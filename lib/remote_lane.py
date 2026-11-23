"""The land pre-step's remote share (GOH.REMOTEWIRE): the Linux-safe part of the chosen tests runs
on the remote worker (`goh remote-shard`, tools/remote_shard.py) AT THE SAME TIME as the Mac's
part, and its verdicts join the Mac's, so the pre-step costs max(Mac, worker), not the sum.

* THE LIST. tests/linux_safe.txt names the test modules (`test_x` for tests/test_x.py: names,
  not paths, so the run closure never reads the list as code it runs) recorded as passing whole in
  the worker (a full-suite run there, no failure and no skip the Mac lacks). Only those go;
  a file not in it, or a Rust change's measured run (lib/land_rustcov.py), stays on the Mac.
* THE SKIPS. tests/mac_skips.txt is the recorded Mac skip set (node ids as junit names them,
  `tests.test_x::test_y`): a remote run whose skips differ from it, for the tests it ran, is
  refused (`--mac-skips`), because a skip is how a missing tool hides.
* THE SLOT. The worker keeps one tree per checkout (the default slot is keyed by the checkout's
  path), so two lanes' pre-steps never queue on one tree or one lock.
* NEVER THE LAND'S FAILURE. An unreachable or busy worker, short scratch, an off-pin image, a
  skip mismatch or an unreadable junit is exit 3 from the shard (`--no-local`): the Mac then runs
  those tests itself, after its own share, and says why. Only a test that FAILED remotely is red.
* THE RECORD. The remote passes join the pre-step's verdict record (tests/_land_verdicts.py) as
  having run the native binary, the conservative mark: they are never carried across a change to
  the binary's build inputs.

GOH_LAND_REMOTE=off keeps the whole pre-step on the Mac; a tree with no tests/linux_safe.txt never
sends anything, and neither does a pre-step inside a running goh suite unless it names its own
GOH_REMOTE_HOST (a fixture land never reaches the real worker).
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
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import fast_tests, tmp_root
from lib.step_timings import timed
from tui.lib import info, warn

LINUX_SAFE = "tests/linux_safe.txt"
MAC_SKIPS = "tests/mac_skips.txt"
FALLBACK = 3  # tools/remote_shard.py --no-local: the caller runs the shard


def listed(root: Path, rel: str) -> set[str]:
    """The non-comment lines of a recorded list, empty when the tree has none."""
    try:
        lines = (root / rel).read_text().splitlines()
    except OSError:
        return set()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def module(node: str) -> str:
    """`tests/test_x.py::t` -> `test_x`, the list's spelling; "" outside tests/."""
    path = node.split("::")[0]
    return path[6:-3] if path.startswith("tests/") and path.endswith(".py") else ""


def cost(nodes: list[str], durations: dict[str, float]) -> dict[str, float]:
    """Each node's seconds from the duration table (a file: its tests' sum); 1 s when unknown."""
    by_file: dict[str, float] = {}
    for test, sec in durations.items():
        f = test.split("::")[0]
        by_file[f] = by_file.get(f, 0.0) + sec
    return {n: durations.get(n) or by_file.get(n) or 1.0 for n in nodes}


def split(root: Path, nodes: list[str],
          durations: dict[str, float] | None = None) -> tuple[list[str], list[str]]:  # fmt: skip
    """(the worker's, the Mac's). A node may go when its FILE is recorded Linux-safe; of those,
    the costliest go first while the worker's seconds stay within its SHARE of the whole set's
    (GOH_REMOTE_SHARE, default 0.25), so both sides end together. Measured 2026-11-22: 57 Linux-safe
    files the duration table puts at 364 Mac-serial seconds took 139 s on the worker at -n 10, about
    2.6 Mac cores, against roughly 8 the Mac's -n 12 gives under a land's load."""
    suite = os.environ.get("GATES_OF_HECK_SUITE_RUNNER") and not os.environ.get("GOH_REMOTE_HOST")
    if os.environ.get("GOH_LAND_REMOTE", "on") == "off" or suite:  # a test never reaches the host
        return [], list(nodes)
    safe = listed(root, LINUX_SAFE)  # module names under tests/, never paths: data, not code
    secs = cost(nodes, fast_tests.load() if durations is None else durations)
    room = float(os.environ.get("GOH_REMOTE_SHARE", "0.25")) * sum(secs.values())
    remote: set[str] = set()
    for n in sorted((n for n in nodes if module(n) in safe), key=lambda n: -secs[n]):
        if secs[n] <= room:
            remote.add(n)
            room -= secs[n]
    return [n for n in nodes if n in remote], [n for n in nodes if n not in remote]


@dataclass
class Share:
    nodes: list[str]
    proc: subprocess.Popen[bytes]
    junit: Path
    log: Path


def begin(root: Path, nodes: list[str], mac_runs: bool = True) -> tuple[list[str], Share | None]:
    """Start the worker's share of `nodes` in the background; (the Mac's nodes, the share).
    `mac_runs` False (no other Mac run this pre-step): one node stays on the Mac, whose pytest
    writes the verdict record tools/land.sh requires of a green pre-step (GOH.VERDICTCARRY2)."""
    remote, local = split(root, nodes)
    if remote and not local and not mac_runs:
        remote, local = remote[1:], remote[:1]
    if not remote:
        return local, None
    fd, junit = tempfile.mkstemp(prefix="goh-remote-", suffix=".xml", dir=tmp_root.goh_tmp())
    os.close(fd)
    log = Path(junit).with_suffix(".log")
    argv = [sys.executable, str(root / "tools" / "remote_shard.py"), "--tree", str(root),
            "--no-local", "--junit", junit, "--mac-skips", str(root / MAC_SKIPS),
            *remote]  # fmt: skip
    with log.open("wb") as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)  # fmt: skip
    info(f"pre-land: {len(remote)} Linux-safe tests on the remote worker, beside the Mac's "
         f"{len(local)}")  # fmt: skip
    return local, Share(remote, proc, Path(junit), log)


def cancel(share: Share | None) -> None:
    """The Mac's share went red first: the worker's verdict is not needed."""
    if share is not None:
        share.proc.kill()
        share.proc.wait()
        _cleanup(share)


def _cleanup(share: Share) -> None:
    share.junit.unlink(missing_ok=True)
    share.log.unlink(missing_ok=True)


def node_id(root: Path, classname: str, name: str) -> str:
    """A junit case's pytest node id: the longest dotted prefix of its class name that is a file."""
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        rel = "/".join(parts[:i]) + ".py"
        if (root / rel).is_file():
            return "::".join([rel, *parts[i:], name])
    return f"{classname}::{name}"


def passed(root: Path, junit: str) -> list[str]:
    return [n for n, kind in kinds(root, junit).items() if kind == "passed"]


def record_passes(record: str, ids: list[str]) -> None:
    """The worker's passes, joined into the pre-step's verdict record (module doc)."""
    if not record or record == os.devnull or not Path(record).is_file():
        return
    data = json.loads(Path(record).read_text())
    data.setdefault("passed", {}).update(dict.fromkeys(ids, True))
    Path(record).write_text(json.dumps(data))


def mac_run(root: Path) -> Callable[[list[str]], int]:
    """The Mac's run of nodes the worker gave back: tools/pytest.sh, its own timings row."""
    pytest = ["bash", str(root / "tools" / "pytest.sh"), "-q"]
    return lambda nodes: timed(f"pre-land: {len(nodes)} tests given back", [*pytest, *nodes])


def end(root: Path, share: Share | None, record: str, merge: Callable[[Path, Path], None],
        run_local: Callable[[list[str]], int] | None = None) -> int:  # fmt: skip
    """The pre-step's verdict on the worker's share. Green: its passes join the record. A test that
    failed there is re-run on the Mac, whose verdict stands (a Linux-only failure is named, never a
    red land). A fallback: the Mac runs the whole share. The Mac's runs write their own record
    (`run_local`, pytest.sh), merged into the one before them (`merge`)."""
    if share is None:
        return 0
    rc = share.proc.wait()
    tail = share.log.read_text(errors="replace").splitlines()[-6:]
    sys.stdout.write("".join(f"  remote: {ln}\n" for ln in tail))
    try:
        got = kinds(root, share.junit.read_text()) if rc in (0, 1) else {}
    except (OSError, ET.ParseError):
        got, rc = {}, FALLBACK
    try:
        record_passes(record, [n for n, kind in got.items() if kind == "passed"])
        redo = [n for n, kind in got.items() if kind == "failed"] or (share.nodes if rc else [])
        if not redo:
            return 0
        why = (
            f"{len(redo)} failed on the worker"
            if rc == 1
            else f"the remote share fell back (exit {rc})"
        )
        info(f"pre-land: {why}: the Mac runs them now, its verdict stands")
        aside = Path(f"{record}.mac") if record and record != os.devnull else None
        if aside and Path(record).is_file():
            Path(record).replace(aside)
        mac = (run_local or mac_run(root))(redo)
        if aside and aside.is_file():
            merge(Path(record), aside)
        if mac == 0 and rc == 1:
            named = ", ".join(redo[:10])
            warn(f"pre-land: failed only on the worker (Linux), green on the Mac: {named} -- "
                 f"a Linux defect, or drop the file from {LINUX_SAFE}")  # fmt: skip
        return mac
    finally:
        _cleanup(share)


def kinds(root: Path, junit: str) -> dict[str, str]:
    """node id -> passed|skipped|failed, from a junit document."""
    out = {}
    for tc in ET.fromstring(junit).iter("testcase"):
        tags = {ch.tag for ch in tc}
        kind = (
            "failed"
            if tags & {"failure", "error"}
            else "skipped"
            if "skipped" in tags
            else "passed"
        )
        out[node_id(root, tc.get("classname", ""), tc.get("name", ""))] = kind
    return out


def record(root: Path, linux: dict[str, str], mac: dict[str, str]) -> tuple[list[str], list[str]]:
    """(the Linux-safe files, the Mac's skips in them), from one full run on each: a file is safe
    when the worker failed none of its tests and skipped exactly what the Mac skipped there."""
    files: dict[str, list[str]] = {}
    for node in linux.keys() | mac.keys():
        files.setdefault(node.split("::")[0], []).append(node)
    safe = sorted(f for f, nodes in files.items() if f.endswith(".py") and all(
        n in linux and n in mac and linux[n] != "failed"
        and (linux[n] == "skipped") == (mac[n] == "skipped") for n in nodes))  # fmt: skip
    keep = set(safe)
    skips = sorted(n for n, k in mac.items() if k == "skipped" and n.split("::")[0] in keep)
    return safe, skips


def junit_name(node: str) -> str:
    """`tests/test_x.py::C::t` as junit names it, `tests.test_x.C::t`: the shard's parity key."""
    path, _, rest = node.partition("::")
    *cls, name = rest.split("::")
    return ".".join([path.removesuffix(".py").replace("/", "."), *cls]) + "::" + name


def main(argv: list[str]) -> int:
    """`python3 lib/remote_lane.py record LINUX.xml [MAC.xml]`, at a checkout's root: rewrite both
    recorded lists from a full run on each side. With no Mac run, a file the worker skipped anything
    in is not safe (no Mac skips to compare); the shard's parity check refuses any remote skip."""
    if len(argv) not in (2, 3) or argv[0] != "record":
        print("usage: remote_lane.py record LINUX_JUNIT [MAC_JUNIT]", file=sys.stderr)
        return 2
    root = Path.cwd()  # the checkout it is run in, never the HEAD export it runs from
    linux = kinds(root, Path(argv[1]).read_text())
    mac = (kinds(root, Path(argv[2]).read_text()) if len(argv) == 3
           else {n: "passed" if k == "skipped" else k for n, k in linux.items()})  # fmt: skip
    safe, skips = record(root, linux, mac)
    how = "LINUX.xml MAC.xml" if len(argv) == 3 else "LINUX.xml (no Mac run)"
    head = f"# GOH.REMOTEWIRE: written by `python3 lib/remote_lane.py record {how}`\n"
    (root / LINUX_SAFE).write_text(head + "".join(f"{module(f)}\n" for f in safe))
    (root / MAC_SKIPS).write_text(head + "".join(f"{junit_name(n)}\n" for n in skips))
    print(f"{len(safe)} Linux-safe files of {len({n.split('::')[0] for n in linux})}; "
          f"{len(skips)} Mac skips in them")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
