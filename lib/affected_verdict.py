"""ONE run of a change's test set before the batch gate (GOH.ONEVERDICTTREE3).

WHY. A lane runs `tools/affected_tests.py --run` on its committed tip, then `goh land --enqueue`
runs the land pre-step (GOH_LAND_PRE: the same tool, the same set) on the very same sha: both
ONEVERDICTTREE lands judged one commit twice, the enqueue's copy 586 s and 350 s (done/*.json
`pre_s`, median 477 s over the last 48 goh lands). So the set's green run is kept in the one store
(lib/reuse.py, the `affected` path) and a run of the same set on the same commit under the same
suite inputs takes it instead of running pytest again.

THE KEY, and why it is exact:
  repo       the common git dir (every worktree and land slot of the repo shares it);
  sha        the commit: its tree, so every byte a test reads, and its parents;
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
set). Pinned by tests/test_affected_verdict.py.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
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


def fields(root: Path, chosen: list[str], env: dict[str, str] | None = None) -> dict | None:
    """The key of this set's run on root's commit (module doc); None: it has none and runs."""
    env = dict(os.environ) if env is None else env
    gi, reuse = _lib("gate_identity"), _lib("reuse")
    identity = gi.suite(root)
    lines = env_lines(root, env)
    sha = reuse._git(root, "rev-parse", "HEAD")
    if identity is None or lines is None or not sha or env.get(REUSE):
        return None

    def digest(parts: list[str]) -> str:
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    return {"repo": reuse.repo_id(root), "sha": sha, "selection": digest(sorted(chosen)),
            "identity": identity, "env": digest(lines)}  # fmt: skip


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


def _hand(rec: dict, base: str | None) -> None:
    """The kept record, written where the caller's run would have written its own."""
    dest = os.environ.get(RECORD, "")
    if dest and dest != os.devnull:
        Path(dest).write_text(json.dumps(rec["record"]))
        _lib("pre_reuse").stamp(dest, base)


def keep(root: Path, key: dict, took: float) -> bool:
    """A green run's record kept under KEY, when it judged this very checkout start to end."""
    path = os.environ.get(RECORD, "")
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    gi = _lib("gate_identity")
    if not isinstance(rec, dict) or not isinstance(rec.get("passed"), dict) \
            or rec.get("identity") is None or rec.get("sha") != key["sha"] \
            or gi.suite(root) != key["identity"]:  # fmt: skip
        return False
    reuse = _lib("reuse")
    reuse.record(PATH, key, "green", by="tools/affected_tests.py --run",
                 extra={"record": rec, "took_s": round(took, 1)})  # fmt: skip
    reuse.prune(PATH)
    return True


def judged(root: Path, chosen: list[str], base: str | None, work: Callable[[], int]) -> int:
    """WORK (the set's run) unless a kept green verdict of this exact key stands; its exit."""
    key = fields(root, chosen)
    if key is None:
        _say(
            "info",
            "pre-land: this run has no exact key (a changed or untracked file, no "
            "declared suite inputs, or a deselecting run) -- the set runs, nothing is kept",
        )
        return work()
    rec, why = _lib("reuse").lookup(PATH, key)
    use = "miss"
    if rec is not None and rec.get("verdict") == "green" and isinstance(rec.get("record"), dict):
        use = _lib("reuse_audit").use("affected", key)
        if use == "reuse":
            _hand(rec, base)
            _say(
                "ok",
                f"pre-land: the set's verdict on {key['sha'][:8]} holds (same commit, "
                f"set and suite inputs; {rec.get('by')}, {rec.get('took_s')} s, "
                f"{len(rec['record']['passed'])} tests) -- not run again",
            )
            return 0
        why = f"the audited `{PATH}` path says {use!r}"
    _say("info", f"pre-land: no kept verdict of this set ({why}) -- it runs")
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
            f"pre-land: the set's green verdict on {key['sha'][:8]} is kept "
            f"(lib/reuse.py `{PATH}`): the enqueue's pre-step takes it",
        )
    return rc


def _again(key: dict) -> str:
    rec, _ = _lib("reuse").lookup(PATH, key)
    return str(rec.get("verdict")) if rec else "absent"
