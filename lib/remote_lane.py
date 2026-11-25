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
* THE RECORD. The remote passes are kept in the pre-step's verdict record (tests/_land_verdicts.py)
  as its `remote` list, apart from `passed`: the land gate's suite (lib/pre_reuse.py `suite`) takes
  them for platform_agnostic tests and reruns the suite on the Mac for a macos_only one
  (GOH.REMOTETRUST, lib/remote_trust.py, replacing GOH.REMOTESHARE's "accept the rerun").
  The share shortens the land's pre-step, the Mac-bound wait before the lock. The GATE's suite has
  its own share since GOH.REMOTEGATE (lib/remote_gate.py), on this module's split and launcher.
* THE SIZE (GOH.REMOTESHARE). Each land measures both sides' pace (`Rates`, `measure`) and the
  next split balances them (`split`), so the worker takes what it finishes by the time the Mac does.

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

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import durations as duration_db, fast_tests, remote_rates, remote_saved, tmp_root
from lib.remote_rates import Rates, blend, load_rates
from lib.remote_saved import cause  # how a share ended, re-exported (lib/remote_gate.py)
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


def unsafe() -> set[str]:
    """The modules a sampled shadow saw disagree between the Mac and the worker (GOH.REMOTEGATE,
    lib/remote_gate.py): goh.db's `linux_unsafe`, {module: why}, beside the rates. Each leaves
    tests/linux_safe.txt by itself: `split` sends none of them again."""
    data = duration_db.get("linux_unsafe")
    return set(data) if isinstance(data, dict) else set()


def split(root: Path, nodes: list[str], durations: dict[str, float] | None = None,
          rates: Rates | None = None, mac_else: float = 0.0,
          runner: bool = False) -> tuple[list[str], list[str]]:  # fmt: skip
    """(the worker's, the Mac's). A node may go when its FILE is recorded Linux-safe; of those,
    the costliest go first while the worker's estimated wall (`Rates`: its overhead plus its
    seconds over its width) stays within the Mac's remaining wall (its seconds, `mac_else` -- the
    judges it runs first -- included, over its width), so both sides end together.
    GOH_REMOTE_SHARE=F instead caps the worker at a fixed fraction F of the set's seconds.
    `runner`: the caller IS the suite's runner (the land gate's controller, lib/remote_gate.py),
    never a test inside it, so the in-suite guard does not apply."""
    suite = (os.environ.get("GATES_OF_HECK_SUITE_RUNNER") and not runner
             and not os.environ.get("GOH_REMOTE_HOST"))  # fmt: skip
    if os.environ.get("GOH_LAND_REMOTE", "on") == "off" or suite:  # a test never reaches the host
        return [], list(nodes)
    safe = listed(root, LINUX_SAFE) - unsafe()  # module names, never paths: data, not code
    secs = cost(nodes, fast_tests.load() if durations is None else durations)
    fixed = os.environ.get("GOH_REMOTE_SHARE")
    r = rates or load_rates()
    room = float(fixed or 0) * sum(secs.values())
    mac_left, wall = mac_else + sum(secs.values()), r.overhead_s
    remote: set[str] = set()
    for n in sorted((n for n in nodes if module(n) in safe), key=lambda n: -secs[n]):
        if fixed:
            if secs[n] <= room:
                remote.add(n)
                room -= secs[n]
            continue
        took = r.worker_s(n, secs[n]) / r.worker_width
        if wall + took <= (mac_left - secs[n]) / r.mac_width:
            remote.add(n)
            wall, mac_left = wall + took, mac_left - secs[n]
    return [n for n in nodes if n in remote], [n for n in nodes if n not in remote]


def measure(root: Path, share: Share, junit: str, mac_end: float) -> None:
    """One land's paces folded into the rates the next split reads (`Rates`)."""
    try:
        doc = ET.fromstring(junit)
        landed = share.junit.stat().st_mtime
    except (OSError, ET.ParseError):
        return
    suite = doc if doc.tag == "testsuite" else doc.find(".//testsuite")
    took = float(suite.get("time", "0")) if suite is not None else 0.0
    tests = {node_id(root, tc.get("classname", ""), tc.get("name", "")): float(tc.get("time") or 0)
             for tc in doc.iter("testcase")}  # fmt: skip
    mac = fast_tests.load()

    def fold(old: Rates) -> Rates:
        new = Rates(old.mac_width, old.worker_width, old.overhead_s, old.ratio)
        work = sum(tests.values())
        if took > 0 and work > 0:
            new.worker_width = blend(old.worker_width, work / took)
            new.overhead_s = blend(old.overhead_s, max(0.0, landed - share.started - took))
        both = [n for n in tests if mac.get(n)]
        if both and sum(mac[n] for n in both) > 0:
            new.ratio = blend(old.ratio, sum(tests[n] for n in both) / sum(mac[n] for n in both))
        if share.mac_s > 0 and mac_end > share.started:
            new.mac_width = blend(old.mac_width, share.mac_s / (mac_end - share.started))
        return new

    with contextlib.suppress(OSError, sqlite3.Error):  # a lost fold costs the next split its pace
        remote_rates.put(tests, fold)


@dataclass
class Share:
    nodes: list[str]
    proc: subprocess.Popen[bytes]
    junit: Path
    log: Path
    started: float = 0.0  # time.time() at the start, for both sides' walls (`measure`)
    mac_s: float = 0.0  # the Mac's estimated serial seconds this pre-step, judges included
    est_s: float = 0.0  # the share's own Mac wall at the Mac's width (lib/remote_saved.py)
    ended: tuple[float, float] = (0.0, 0.0)  # the gate's (Mac done, worker done), and its log
    said: str = ""


def begin(root: Path, nodes: list[str], mac_runs: bool = True,
          judges: list[str] | None = None) -> tuple[list[str], Share | None]:  # fmt: skip
    """Start the worker's share of `nodes` in the background; (the Mac's nodes, the share).
    `mac_runs` False (no other Mac run this pre-step): one node stays on the Mac, whose pytest
    writes the verdict record tools/land.sh requires of a green pre-step (GOH.VERDICTCARRY2).
    `judges`: the Mac's own run before `nodes`, whose seconds the balance counts."""
    durations = fast_tests.load()
    before = sum(cost(judges or [], durations).values())
    remote, local = split(root, nodes, durations, mac_else=before)
    if remote and not local and not mac_runs:
        remote, local = remote[1:], remote[:1]
    if not remote:
        return local, None
    info(f"pre-land: {len(remote)} Linux-safe tests on the remote worker, beside the Mac's "
         f"{len(local)}")  # fmt: skip
    mac_s = before + sum(cost(local, durations).values())
    return local, launch(root, remote, mac_s)


def launch(root: Path, remote: list[str], mac_s: float,
           env: dict[str, str] | None = None) -> Share:  # fmt: skip
    """`goh remote-shard --no-local` on `remote`, in the background (its log and junit in goh's
    temp root); `mac_s`, the Mac's estimated seconds meanwhile, for `measure`."""
    fd, junit = tempfile.mkstemp(prefix="goh-remote-", suffix=".xml", dir=tmp_root.goh_tmp())
    os.close(fd)
    log = Path(junit).with_suffix(".log")
    argv = [sys.executable, str(root / "tools" / "remote_shard.py"), "--tree", str(root),
            "--no-local", "--junit", junit, "--mac-skips", str(root / MAC_SKIPS),
            *remote]  # fmt: skip
    with log.open("wb") as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env)  # fmt: skip
    est = sum(cost(remote, fast_tests.load()).values()) / max(load_rates().mac_width, 1.0)
    return Share(remote, proc, Path(junit), log, time.time(), mac_s, est)


def cancel(share: Share | None) -> None:
    """The Mac's share went red first: the worker's verdict is not needed; the log says so."""
    if share is not None:
        n = len(share.nodes)
        info(f"pre-land: the remote share of {n} test{'s' * (n != 1)} stopped: the Mac's run went "
             "red first, and its verdict stands")  # fmt: skip
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
    """The worker's passes, kept in the pre-step's verdict record apart from the Mac's (module
    doc): its `remote` list, which no gate's reuse reads as a pass."""
    if not record or record == os.devnull or not Path(record).is_file() or not ids:
        return
    data = json.loads(Path(record).read_text())
    data["remote"] = sorted({*data.get("remote", []), *ids})
    Path(record).write_text(json.dumps(data))


def mac_run(root: Path) -> Callable[[list[str]], int]:
    """The Mac's run of nodes the worker gave back: tools/pytest.sh, its own timings row."""
    pytest = ["bash", str(root / "tools" / "pytest.sh"), "-q"]
    return lambda nodes: timed(f"pre-land: {len(nodes)} tests given back", [*pytest, *nodes])


def note(share: Share, rc: int, said: str, fig: dict | None = None) -> None:
    """The share's outcome beside the pre-step's figures (`$GOH_LAND_PRE_TOOK.remote`), which
    lib/land_prestep.py carries into the land record's `pre_run.remote` for --report: the tests
    sent, `remote` (the worker judged them), `failed` (some failed there) or `fallback` with its
    `cause`, whether the run left a broken ssh multiplexer for a fresh connection, and what it
    saved (FIG, lib/remote_saved.py), which is also said where the land's log reads it."""
    if fig is not None:
        remote_saved.say(fig)
    took = os.environ.get("GOH_LAND_PRE_TOOK")
    if not took:
        return
    outcome = "remote" if rc == 0 else "failed" if rc == 1 else "fallback"
    data = {"tests": len(share.nodes), "outcome": outcome, **(fig or {}),
            "cause": cause(said) if outcome == "fallback" else None,
            "fresh": "a fresh connection instead" in said}  # fmt: skip
    with contextlib.suppress(OSError):  # the instrument never fails the pre-step
        Path(f"{took}.remote").write_text(json.dumps(data))


def end(root: Path, share: Share | None, record: str, merge: Callable[[Path, Path], None],
        run_local: Callable[[list[str]], int] | None = None,
        clock: Callable[[], float] = time.time) -> int:  # fmt: skip
    """The pre-step's verdict on the worker's share. Green: its passes join the record. A test that
    failed there is re-run on the Mac, whose verdict stands (a Linux-only failure is named, never a
    red land). A fallback: the Mac runs the whole share. The Mac's runs write their own record
    (`run_local`, pytest.sh), merged into the one before them (`merge`)."""
    if share is None:
        return 0
    mac_end = clock()  # the Mac's side is done: its wall ends here
    rc = share.proc.wait()
    worker_end, rerun = remote_saved.ended(share.junit, clock()), 0.0
    said = share.log.read_text(errors="replace")
    tail = said.splitlines()[-6:]
    sys.stdout.write("".join(f"  remote: {ln}\n" for ln in tail))
    try:
        text = share.junit.read_text() if rc in (0, 1) else ""
        got = kinds(root, text) if text else {}
        if got:
            measure(root, share, text, mac_end)
    except (OSError, ET.ParseError):
        got, rc = {}, FALLBACK
    broke = rc == 1 and "failed" not in got.values()  # the shard's own exit 1, no test's
    rc_said = FALLBACK if broke else rc
    try:
        record_passes(record, [n for n, kind in got.items() if kind == "passed"])
        failed = [n for n, kind in got.items() if kind == "failed"]
        redo = failed or (share.nodes if rc else [])
        if not redo:
            return 0
        named = ", ".join(failed[:10]) + (f" and {len(failed) - 10} more" * (len(failed) > 10))
        why = (f"{len(failed)} failed on the worker: {named}" if failed  # GOH.REMOTEREDSILENT
               else "the remote shard exited 1 naming no failed test (its last lines above)"
               if broke else f"the remote share fell back (exit {rc})")  # fmt: skip
        info(f"pre-land: {why}: the Mac runs them now, its verdict stands")
        aside = Path(f"{record}.mac") if record and record != os.devnull else None
        if aside and Path(record).is_file():
            Path(record).replace(aside)
        t = clock()
        mac = (run_local or mac_run(root))(redo)
        rerun = clock() - t
        if aside and aside.is_file():
            merge(Path(record), aside)
        if mac == 0 and failed:
            named = ", ".join(failed[:10])
            warn(f"pre-land: failed only on the worker (Linux), green on the Mac: {named} -- "
                 f"a Linux defect, or drop the file from {LINUX_SAFE}")  # fmt: skip
        return mac
    finally:
        where = "enqueue" if os.environ.get("GOH_LAND_PRE_ONLY") else "pre-land"
        out = "remote" if rc_said == 0 else "failed" if rc_said == 1 else "fallback"
        note(share, rc_said, said, remote_saved.figure(where, said, len(share.nodes), out,
             share.started, mac_end, worker_end, share.est_s, rerun))  # fmt: skip
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
    """(the Linux-safe files, the Mac's skips in them), from one full run on the worker and a Mac
    run of any files: a file the Mac ran is safe when the worker failed none of its tests and
    skipped exactly what the Mac skipped there; a file it did not run, when the worker failed and
    skipped none (GOH.REMOTESHARE: the Mac need run only the files the worker skipped in)."""
    files: dict[str, list[str]] = {}
    for node in linux.keys() | mac.keys():
        files.setdefault(node.split("::")[0], []).append(node)
    ran = {n.split("::")[0] for n in mac}
    mac = {**{n: "passed" if k == "skipped" else k for n, k in linux.items()
              if n.split("::")[0] not in ran}, **mac}  # fmt: skip
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
    recorded lists from a full run on the worker and a Mac run of any files (`record`). With no Mac
    run, a file the worker skipped anything in is not safe (no Mac skips to compare)."""
    if len(argv) not in (2, 3) or argv[0] != "record":
        print("usage: remote_lane.py record LINUX_JUNIT [MAC_JUNIT]", file=sys.stderr)
        return 2
    root = Path.cwd()  # the checkout it is run in, never the HEAD export it runs from
    linux = kinds(root, Path(argv[1]).read_text())
    mac = kinds(root, Path(argv[2]).read_text()) if len(argv) == 3 else {}
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
