"""ONE run of a change's test set before the batch gate (GOH.ONEVERDICTTREE3).

WHY. A lane runs `tools/affected_tests.py --run` on its committed tip, then `goh land --enqueue`
runs the land pre-step (GOH_LAND_PRE: the same tool, the same set) on the very same sha: both
ONEVERDICTTREE lands judged one commit twice, the enqueue's copy 586 s and 350 s (done/*.json
`pre_s`, median 477 s over the last 48 goh lands). So the set's green run is kept in the one store
(lib/reuse.py, the `affected` path) and a run of the same set on the same commit under the same
suite inputs takes it instead of running pytest again.

THE KEY, and why it is exact:
  repo       the common git dir (every worktree and land slot of the repo shares it);
  tree       the commit's tree, every byte a test reads, never the sha: a lane that re-commits
             the tree it ran (a reworded message) is the same run (GOH.PRESTEPVERDICTMISS:
             STEPCLOSUREREFUSE's enqueue re-ran 515 s on such a recommit), a changed tree misses;
             the land gate keys a test verdict so (lib/gate_identity.py `suite`). The record handed
             names the checkout's commit, the one it judged in `judged_on`;
  selection  sha256 of the chosen node ids, the set itself (the diff it was chosen from enters
             only through it: the same nodes on the same commit are the same run);
  identity   lib/gate_identity.py `suite` -- the commit's tree in a CLEAN checkout (no change, no
             untracked file: a file the lane forgot to add makes a checkout with no identity, so
             its run is never kept) plus every tool's bytes as PATH finds them;
  env        the suite's inputs by value: every GOH_* tests/suite_home.py lets through to a test
             (CALLER_INPUTS; it drops every other GOH_* before a test runs, tests/
             test_suite_ship_env.py) but the verdict-free ones (gates/verdict_free_keys.txt: the
             record's own path, the floor memo), GOH_PY as the interpreter it RESOLVES to
             (lib/house_python.sh, which tools/pytest.sh sources), and the suite's shape
             (GATES_OF_HECK_SUITE_SHAPE; every keyed run carries a land record, so it is `ci`,
             the land's). A tree whose suite declares no inputs has no key: it runs.
The record kept is the run's own verdict record (tests/_land_verdicts.py), the very file the land
gate already reuses on `identity` alone; this key is that plus four more fields, so the enqueue
trusts it strictly less than the gate trusted it before. A reuse writes that record to the
caller's GOH_LAND_VERDICTS_RECORD, stamped with the caller's base (lib/pre_reuse.py `stamp`), and
the structural gates and the cheap judges still run fresh: seconds.

THE LANE'S RUN RECORDS. A whole-set run with no record of its own (a lane's) is given one, so its
suite runs in the land's shape (`ci`) and its passes are the land's record. A run that deselects
(GOH_LAND_VERDICTS_REUSE) is no whole set: it neither keeps nor takes.

THE AUDIT. Every take asks lib/reuse_audit.py; one in `every` is a SHADOW (the set runs anyway and
the verdicts are compared; a repeated disagreement turns the path off, and an off path runs every
set). GOH.LANDEDREUSEON's enqueue was such a shadow (830.9 s, agreed).

EVERY MISS SAYS WHY, in one line of the run's output (so of the enqueue's): no key and what took it
(a named untracked or changed file, a deselecting run, no declared inputs), the nearest kept
verdict of the repo and the fields it differs in, the audit's shadow, or an off path.

WHAT THE ENQUEUE DID, ON ITS RECORD (GOH.LANEPRESTEP). `outcome` reads those lines back: a take
(`took`, the sha judged, `by`, `lane_s` the seconds of the run not repeated, `tests`) or a run
(`ran`, why). lib/land_prestep.py puts it on the land request and the done record carries it
(`pre_affected`), so a 35 s `pre_s` says whether it was a take or a small set, and the runs of one
tree's set before the land gate are counted from the records, not from lane transcripts (24 goh
lands, 2026-11-19 22:22Z to 11-20 09:25Z: 5 took the lane's verdict, pre_s 26-35 s; 2 ran a set the
lane had already run on that tree, the audit's shadow, 865 s, and a recommit, 550 s, keyed by tree
since; the other 17 had the pre-step's run alone). The parser lives beside the lines it reads,
pinned by tests/test_affected_verdict.py with the rest.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

LIB = Path(__file__).resolve().parent
PATH = "affected"
RECORD = "GOH_LAND_VERDICTS_RECORD"
REUSE = "GOH_LAND_VERDICTS_REUSE"
SHAPE = "GATES_OF_HECK_SUITE_SHAPE"
# The lines a run of the set says (`judged`), and `outcome`'s patterns for them: one place each.
TOOK = (
    "pre-land: the set's verdict on {on} holds (same tree, set and suite inputs; {by}, {took} s, "
    "{tests} tests) -- not run again"
)
RAN = "pre-land: no verdict taken ({why}) -- it runs"
NO_KEY = "pre-land: no exact key ({why}) -- the set runs, nothing is kept"
_TOOK = re.compile(
    r"pre-land: the set's verdict on (?P<on>\S+) holds \(same tree, set and suite inputs; "
    r"(?P<by>.+), (?P<took>[0-9.]+|None) s, (?P<tests>\d+) tests\) -- not run again"
)
_RAN = re.compile(
    r"pre-land: no (?:verdict taken|exact key) \((?P<why>.*)\) -- (?:it runs|the set runs, nothing)"
)


def _lib(name: str):
    if name not in sys.modules:
        sys.path.insert(0, str(LIB))
        try:
            importlib.import_module(name)
        finally:
            del sys.path[0]
    return sys.modules[name]


def _say(kind: str, text: str) -> None:
    getattr(importlib.import_module("tui.lib"), kind)(text)


def house_python(root: Path, env: dict[str, str]) -> str:
    """The interpreter tools/pytest.sh will run the suite on: GOH_PY's, else the house search's."""
    named = env.get("GOH_PY", "")
    if not named:
        out = subprocess.run(["bash", str(root / "lib" / "house_python.sh")], capture_output=True,
                             text=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
        named = out.stdout.strip() if out.returncode == 0 else ""
    return os.path.realpath(named) if named else "unresolved"


def env_lines(root: Path, env: dict[str, str]) -> list[str] | None:
    """The suite's inputs by value (module doc, `env`); None: the tree declares none."""
    cov_env = _lib("cov_env")
    declared = cov_env.declared(root)
    if declared is None:
        return None
    free = cov_env.free_keys(root)
    out = [f"shape {env.get(SHAPE, '')}", f"GOH_PY -> {house_python(root, env)}"]
    for name in sorted(set(declared) - free - {"GOH_PY"}):
        out.append(f"{name}={env[name]}" if name in env else f"{name} unset")
    return out


def keyed(
    root: Path, chosen: list[str], env: dict[str, str] | None = None
) -> tuple[dict | None, str]:
    """(the key of this set's run on root's commit tree, "") or (None, why it has none: it runs)."""
    env = dict(os.environ) if env is None else env
    gi, reuse = _lib("gate_identity"), _lib("reuse")
    if env.get(REUSE):
        return None, f"a deselecting run ({REUSE} is set) is no whole set"
    if not (tree := reuse._git(root, "rev-parse", "--verify", "--quiet", "HEAD^{tree}")):
        return None, f"{root} has no commit"
    if (identity := gi.suite(root)) is None:
        moved = reuse._git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
        named = ", ".join(ln[3:] for ln in moved[:3]) + (
            f" (+{len(moved) - 3} more)" if moved[3:] else ""
        )
        return None, f"the checkout is not its commit: {named or 'no clean tree'}"
    if (lines := env_lines(root, env)) is None:
        return None, "the tree declares no suite inputs (tests/suite_home.py CALLER_INPUTS)"

    def digest(parts: list[str]) -> str:
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    return {"repo": reuse.repo_id(root), "tree": tree, "selection": digest(sorted(chosen)),
            "identity": identity, "env": digest(lines)}, ""  # fmt: skip


def fields(root: Path, chosen: list[str], env: dict[str, str] | None = None) -> dict | None:
    """The key of this set's run on root's commit tree (module doc); None: it has none and runs."""
    return keyed(root, chosen, env)[0]


def nearest(key: dict) -> str:
    """Why KEY missed, in words: the store's live `affected` record of this repo nearest it (fewest
    fields apart, then the newest) and the fields it differs in."""
    reuse = _lib("reuse")
    p, best = reuse.PATHS[PATH], None
    for _, rec, age in reuse.records(PATH):
        got = rec.get("fields")
        if not isinstance(got, dict) or set(got) != set(p.fields) or not 0 <= age < reuse.ttl(p):
            continue
        off = [k for k in p.fields if got.get(k) != key.get(k)]
        if "repo" not in off and (best is None or (len(off), age) < (len(best[1]), best[2])):
            best = (rec, off, age)
    if best is None:
        return f"no kept verdict of this repo in the last {reuse.ttl(p) // 3600} h"
    rec, off, age = best
    on = str((rec.get("record") or {}).get("sha", ""))[:8] or "?"
    return f"the nearest kept verdict (on {on}, {age // 60} min old) differs in {', '.join(off)}"


@contextlib.contextmanager
def own_record() -> Iterator[None]:
    """A whole-set run with no land record of its own (a lane's) gets one, so its suite runs in the
    land's shape and its green verdict can be kept (module doc, THE LANE'S RUN RECORDS)."""
    if os.environ.get(RECORD):
        yield
        return
    tmp_root = _lib("tmp_root")  # never Spotlight-indexed (GOH.TMPNOINDEX)
    with tempfile.TemporaryDirectory(prefix="goh-affected.", dir=tmp_root.goh_tmp()) as tmp:
        os.environ[RECORD] = str(Path(tmp) / "record.json")
        try:
            yield
        finally:
            del os.environ[RECORD]


def whole(root: Path, chosen: list[str], base: str | None, is_whole: bool,
          work: Callable[[], int]) -> int:  # fmt: skip
    """tools/affected_tests.py's run of its set: a WHOLE set's under its own record and `judged`,
    unless it runs nothing (an empty set, or inside a running goh suite, which runs the set)."""
    nested = os.environ.get("GATES_OF_HECK_SUITE_RUNNER") and not os.environ.get(
        "GATES_OF_HECK_SUITE_NESTED_OK"
    )
    if not is_whole or not chosen or nested:
        return work()
    with own_record():
        return judged(root, chosen, base, work)


def _hand(root: Path, rec: dict, base: str | None, chosen: list[str]) -> None:
    """The kept record, written where the caller's run would have written its own, naming the set
    its key's `selection` is the digest of (lib/pre_reuse.py `stamp`)."""
    dest = os.environ.get(RECORD, "")
    if dest and dest != os.devnull:
        kept, head = rec["record"], _lib("reuse")._git(root, "rev-parse", "HEAD")
        if head and kept.get("sha") != head:  # a recommit of the tree it judged: the checkout's
            kept = {**kept, "sha": head, "judged_on": kept.get("sha")}
        Path(dest).write_text(json.dumps(kept))
        _lib("pre_reuse").stamp(dest, base, chosen)


def keep(root: Path, key: dict, took: float) -> bool:
    """A green run's record kept under KEY, when it judged this very checkout start to end."""
    path = os.environ.get(RECORD, "")
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    gi, reuse = _lib("gate_identity"), _lib("reuse")
    sha = rec.get("sha") if isinstance(rec, dict) else None  # the run judged this very commit
    head = reuse._git(root, "rev-parse", "HEAD")
    if not isinstance(rec, dict) or not isinstance(rec.get("passed"), dict) \
            or rec.get("identity") is None or not sha or sha != head \
            or reuse._git(root, "rev-parse", f"{sha}^{{tree}}") != key["tree"] \
            or gi.suite(root) != key["identity"]:  # fmt: skip
        return False
    reuse.record(PATH, key, "green", by="tools/affected_tests.py --run",
                 extra={"record": rec, "took_s": round(took, 1)})  # fmt: skip
    reuse.prune(PATH)
    return True


def judged(root: Path, chosen: list[str], base: str | None, work: Callable[[], int]) -> int:
    """WORK (the set's run) unless a kept green verdict of this exact key stands; its exit."""
    key, why = keyed(root, chosen)
    if key is None:
        _say("info", NO_KEY.format(why=why))
        return work()
    rec, why = _lib("reuse").lookup(PATH, key)
    use = "miss"
    if rec is None and why.startswith("no "):
        why = nearest(key)
    elif rec is not None and (
        rec.get("verdict") != "green" or not isinstance(rec.get("record"), dict)
    ):
        why = f"the kept verdict of this key is {rec.get('verdict')!r}, no green record"
    elif rec is not None:
        on = str(rec["record"].get("sha", ""))[:8]
        use = _lib("reuse_audit").use("affected", key)
        if use == "reuse":
            _hand(root, rec, base, chosen)
            _say("ok", TOOK.format(on=on, by=rec.get("by"), took=rec.get("took_s"),
                                   tests=len(rec["record"]["passed"])))  # fmt: skip
            return 0
        every = _lib("reuse").PATHS[PATH].every
        why = (
            f"the verdict on {on} holds, but this take is the audit's shadow: one take in {every} "
            "runs the set to check it, lib/reuse_audit.py"
            if use == "shadow"
            else f"the audited `{PATH}` path is {use} (reuse_audit.py report names why)"
        )
    _say("info", RAN.format(why=why))
    t0 = time.monotonic()
    rc = work()
    if use == "shadow":
        _lib("reuse_audit").compare(
            PATH,
            key,
            "green",
            "green" if rc == 0 else "red",
            again=lambda: (_again(key), "green" if work() == 0 else "red"),
        )
    if rc == 0 and keep(root, key, time.monotonic() - t0):
        _say(
            "info",
            f"pre-land: the set's green verdict on tree {key['tree'][:8]} is kept "
            f"(lib/reuse.py `{PATH}`): the enqueue's pre-step takes it",
        )
    return rc


def _again(key: dict) -> str:
    rec, _ = _lib("reuse").lookup(PATH, key)
    return str(rec.get("verdict")) if rec else "absent"


def outcome(lines: list[str]) -> dict | None:
    """What a pre-step's run of the set did, from its output LINES (module doc, WHAT THE ENQUEUE
    DID): {"took", "by", "lane_s", "tests"} on a take, {"ran": why} on a run, None when no line of
    this module's is there (no whole set ran: an empty set, a --no-pre, another pre-step)."""
    for line in lines:
        if found := _TOOK.search(line):
            took = found["took"]
            return {"took": found["on"], "by": found["by"],
                    "lane_s": float(took) if took != "None" else None,
                    "tests": int(found["tests"])}  # fmt: skip
        if found := _RAN.search(line):
            return {"ran": found["why"]}
    return None


def merge_records(record: Path, first: Path) -> None:
    """tools/affected_tests.py `run`: the judges' record joined into the rest's, when both
    judged the same tree and tip."""
    try:
        a, b = json.loads(first.read_text()), json.loads(record.read_text())
    except (OSError, ValueError):
        a, b = None, None
    if a and b and (a.get("identity"), a.get("sha")) == (b.get("identity"), b.get("sha")):
        b["passed"] = {**a.get("passed", {}), **b.get("passed", {})}
        if "reads" in a or "reads" in b:  # what each file read (tests/_read_audit.py)
            b["reads"] = {**(a.get("reads") or {}), **(b.get("reads") or {})}
        if "remote" in a or "remote" in b:  # the worker's passes (GOH.REMOTETRUST), never dropped
            b["remote"] = sorted({*a.get("remote", []), *b.get("remote", [])} - set(b["passed"]))
        record.write_text(json.dumps(b))
    elif a and not record.is_file():
        first.replace(record)
        return
    first.unlink(missing_ok=True)


def carrying(record: str) -> list[str]:
    """The words before a recording run's pytest that make its suite CARRY (GOH.READKEYVERDICT):
    `env GOH_LAND_VERDICTS_REUSE=/dev/null`, the pre-step's own run, which names no same-tree
    record, so tests/_land_verdicts.py deselects each chosen test that passed on another tree
    whose diff to this one the selector does not choose it by, nor touches a file it was seen to
    read (tests/_read_audit.py), as the land gate's suite does: a run after a fix runs only the
    fix's readers. None for a run with no record, or one a caller already names REUSE for. The
    plain run only: a Rust change's measured run (lib/land_rustcov.py) runs its set whole."""
    if not record or record == os.devnull or os.environ.get(REUSE):
        return []
    return ["env", f"{REUSE}={os.devnull}"]


def ran_nothing(why: str) -> None:
    """tools/affected_tests.py `run`: the land's record when no suite ran to write it: no
    verdict, and why. tools/land.sh refuses a green pre-step with no record in a tree whose suite
    records one (GOH.VERDICTCARRY2)."""
    path = os.environ.get("GOH_LAND_VERDICTS_RECORD", "")
    if not path or path == os.devnull:
        return
    Path(path).write_text(json.dumps({"identity": None, "sha": None, "passed": {}, "ran": why}))
