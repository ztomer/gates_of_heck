"""The reuse auditor: every reuse path sampled, compared with a fresh run, switched off when they
disagree (GOH.REUSEAUDIT; docs/reuse.md).

    reuse_audit.py report [--json]          # per path: state, uses, samples, disagreements, flakes
    reuse_audit.py enable PATH --fix SHA --test NODE   # back on after a disagreement: the fix, and
                                            # the test that misses on the input it lacked
    reuse_audit.py enable PATH --why TEXT   # back on after a starved window
    reuse_audit.py use PATH [--unbacked] K=V...   # a shell site's ask: reuse, shadow or off
    reuse_audit.py compare PATH --reused R --fresh F [--again R2 F2] [--logs L1 L2] K=V...
                                            # its sampled reuse against the fresh run (exit 0:
                                            # they agree; 1: they do not)

WHY. Every speedup skips work on a recorded verdict, and a skip keyed on less than its real inputs
makes the gate lie while reading faster (GOH.PROVENBUILD skipped a build an export needed;
GOH.VERDICTKEYDRIFT keyed one commit two ways). A reuse is a claim that a fresh run would agree, so
a share of every path's reuses is re-run and compared.

THE PATHS are lib/reuse.py's registry, one table: each names its key fields, its sites, its store
and its shadow. A site asks `use(path, fields)` before it reuses: "reuse" (skip the work), "shadow"
(do the work anyway; the caller then hands both verdicts to `compare`) or "off" (the path is off:
do the work). One use in the path's `every` is a shadow, counted, never drawn by chance: the share
is exact and a test can name the sampled use. A shadow re-runs the work at the site, on this box,
in the shape the reused verdict was made in. Never hosted CI (GOH.LANDEDREUSEON): it is another
machine's shape and advisory, so its red switched a LOCAL reuse off (`landed`, v0.139.6 and
REUSEOFFLANDED4); a CI red is the loop's fault item (`loopctl reconcile` files it), not an audit's.

A DISAGREEMENT IS RE-RUN FIRST. Known load flakes exist, so a reused verdict that differs from the
fresh one re-runs both sides (`again`: the store read again, the work done again); only a
disagreement both re-runs repeat turns the path off. One that does not repeat is a flake, counted.

OFF IS ONE RECORDED STATE: the path's row of goh.db's `reuse_audit` (lib/reuse_audit_store.py,
GOH.VERDICTDB; a file `<store>/audit/<path>.json` before the switch), never an environment
variable, and never the disposable cache: lost, it would turn a wrong reuse back on. It names
the cause, both verdicts and the key, and a fault item is filed (`loopctl file`; GOH_LOOPCTL and
GOH_LOOP_QUEUE are the seams; it names its class as rules.class_first asks, `class_of: none`; one
that cannot be filed is named in the record and at every SessionStart, hooks/claude/
estate_advisories.py; an id the queue already holds is an earlier off's, so the next free id is
filed, GOH.REUSEOFFLANDED2). It stays off until `enable` names the fix commit and the
calibrated-miss test it added, both found in the tree.

A STARVED SAMPLER IS NEVER CLEAN. Each path's counters run in a window (WINDOW_S). When a window
closes with fewer compared samples than min(min_samples, the samples it drew), the shadows never
ran: the path turns off (cause "starved"). A window that drew fewer samples than min_samples reads
"thin" in the report, never "clean". Pinned by tests/test_reuse_audit.py.

The reuse auditor (GOH.REUSEAUDIT, `docs/reuse.md`): a site asks `use(path, fields)` (reuse, shadow
or off; one use in the path's `every` is a shadow, counted); `compare` re-runs both sides of a
disagreement and turns a repeated one's path OFF in one recorded state, the path's row of goh.db's
`reuse_audit` (`reuse_audit_store.py`) with a fault item filed (`loopctl file`); a starved window
turns it off too; `enable` needs the fix and its calibrated-miss test; every shadow is local, at
its site, never hosted CI (GOH.LANDEDREUSEON); `report [--json]` counts uses, samples,
disagreements and flakes per path.

`lib/crate_reach.py`: GOH.AFFECTEDBINARY2: which goh subcommands a crates/ change reaches, read
from the crate (a per-file module graph: paths, `use` bindings, a constant read as no call, an
`impl` reached through its type's file; main.rs's `Commands::` dispatch arms through its helpers).
`subcommands(path)` is None (every one) for main.rs, Cargo files, other crates and what main()
reaches before dispatch; empty for a test-only source; `binary_runners.choose` keeps the runners
whose argv words name a reached subcommand.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import argparse
import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_root  # lib/: GOH_LIVE_ROOT, honoured for an export only
import lock_order  # lib/: the one acquire path (GOH.LOCKORDER)
import reuse  # lib/, beside this file: the one store and its path registry
import reuse_audit_store  # lib/: the records' rows in goh.db (GOH.VERDICTDB)
import reuse_fault  # lib/: the fault item an off files

# The audit's own names: _off files through this module's global (a test's seam), and
# hooks/claude/estate_advisories.py reads `reuse_audit.unfiled`.
file_fault, unfiled = reuse_fault.file_fault, reuse_fault.unfiled

WINDOW_S = 7 * reuse.DAY_S
COUNTS = ("uses", "sampled", "compared", "agreed", "disagreed", "flakes")


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """One read-change-write of a record at a time. The flock stays a file (handover plan (e)):
    the change may file a fault (a loopctl child), which no database transaction may span."""
    lock = reuse_audit_store.lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as fh:
        lock_order.flock(fh, "reuse_audit")  # released when the file closes
        yield


def _fresh_window(start: int) -> dict:
    return {"start": start, **dict.fromkeys(COUNTS, 0)}


def _read(name: str) -> dict:
    rec = reuse_audit_store.get(name) or {}
    if not isinstance(rec.get("window"), dict):
        rec = {"path": name, "state": "on", "window": _fresh_window(reuse.now()), "closed": []}
    return rec


def _write(rec: dict) -> None:
    reuse_audit_store.put(rec)


def _roll(p: reuse.ReusePath, rec: dict) -> dict:
    """Close the window when it is over; a starved one turns the path off."""
    win = rec["window"]
    if reuse.now() - win["start"] < WINDOW_S:
        return rec
    rec["closed"] = [*rec.get("closed", []), win][-8:]
    rec["window"] = _fresh_window(reuse.now())
    need = min(p.min_samples, win["sampled"])
    if p.shadow and rec["state"] == "on" and win["compared"] < need:
        why = f"{win['compared']} of {need} sampled reuses were compared in the window"
        _off(p, rec, "starved", why, {})
    return rec


def _off(p: reuse.ReusePath, rec: dict, cause: str, why: str, line: dict) -> None:
    rec.update(state="off", cause=cause, why=why, since=reuse.now(), evidence=line)
    rec["fault"] = file_fault(p, cause, why, line)


def file_unfiled() -> list[str]:
    """File again each off whose filing failed; one line a path filed. A suite's shadow turns a path
    off under the suite home, which hides the loop's checkout, so it can file nothing there: the
    idle lander and SessionStart, under the owner's home, file it (GOH.REUSEOFFTEST)."""
    out = []
    if not unfiled():
        return out
    with _locked():
        for name, p in reuse.PATHS.items():
            rec = reuse_audit_store.get(name)
            if not rec or rec.get("state") != "off":
                continue
            if str(rec.get("fault") or "").startswith("filed "):
                continue
            rec["fault"] = file_fault(p, rec["cause"], rec["why"], rec.get("evidence") or {})
            _write(rec)
            if rec["fault"].startswith("filed "):
                out.append(f"{name} off ({rec['cause']}): {rec['fault']}")
    return out


def state(path: str) -> dict:
    """The path's recorded state: {"state": "on"|"off", "cause", "why", "window", ...}."""
    p = reuse._path(path)
    with _locked():
        rec = _roll(p, _read(p.name))
        _write(rec)
    return rec


def use_many(path: str, keys: list[dict[str, str]], backstop: bool = True) -> list[str]:
    """One decision per reuse a site is about to make, in order: "reuse", "shadow" or "off".
    `backstop=False`: nothing else re-runs what these reuses skip (a repo no hosted CI gates), so
    they are counted apart and sampled one in the path's `every_unbacked`."""
    p = reuse._path(path)
    for fields in keys:
        reuse.key(p, fields)  # a partial key raises, as a lookup's does
    with _locked():
        rec = _roll(p, _read(p.name))
        win = rec["window"]
        out = []
        for _ in keys:
            if rec["state"] != "on":
                out.append("off")
                continue
            win["uses"] += 1
            n, every = win["uses"], p.every
            if not backstop and p.every_unbacked:
                win["unbacked"] = n = win.get("unbacked", 0) + 1
                every = p.every_unbacked
            due = bool(p.shadow) and n % every == 0
            win["sampled"] += due
            out.append("shadow" if due else "reuse")
        _write(rec)
    return out


def use(path: str, fields: dict[str, str], backstop: bool = True) -> str:
    return use_many(path, [fields], backstop)[0]


def compare(path: str, fields: dict[str, str], reused: str, fresh: str,
            again: Callable[[], tuple[str, str]] | None = None,
            fresh_from: str = "", logs: tuple[str, str] = ("", "")) -> bool:  # fmt: skip
    """A sampled reuse against its fresh run; True when they agree. A disagreement re-runs both
    sides (`again` returns the store's verdict read again and the work's done again): only a
    repeated one turns the path off and files the fault; one `again` cannot repeat is a flake.
    `fresh_from` names what produced the fresh side when the key does not (`landed`'s: the key's
    sha is the land-judged parent, the run is the pushed release commit's). A fresh red the work's
    `again` turned green is a pass on retry: one flake record (lib/flakes.py) keeping LOGS."""
    p = reuse._path(path)
    line = {"path": p.name, "key": reuse.key(p, fields), "fields": fields, "reused": reused,
            "fresh": fresh, "epoch": reuse.now()}  # fmt: skip
    if fresh_from:
        line["fresh_from"] = fresh_from
    if reused != fresh:
        again_reused, again_fresh = again() if again else ("", "")
        line["again"] = {"reused": again_reused, "fresh": again_fresh}
        line["repeated"] = (again_reused, again_fresh) == (reused, fresh)
    with _locked():
        rec = _roll(p, _read(p.name))
        win = rec["window"]
        win["compared"] += 1
        kind = "agreed" if reused == fresh else "disagreed" if line["repeated"] else "flakes"
        win[kind] += 1
        if kind == "disagreed" and rec["state"] == "on":
            why = f"reused {reused!r}, fresh {fresh!r}, both again on a re-run of both sides"
            _off(p, rec, "disagree", why, line)
        _write(rec)
        reuse_audit_store.shadow_add({**line, "outcome": kind})
    if fresh == "red" and line.get("again", {}).get("fresh") == "green":  # outside the audit lock
        import flakes  # lib/: the flake register (GOH.FLAKEREGISTER)

        test = fields.get("step") or f"{p.name}: {p.doc}"
        flakes.record(test, fields.get("repo", ""), fields.get("sha") or fields.get("tree", ""),
                      f"{p.name} reuse sample run again (lib/reuse_audit.py)", logs)  # fmt: skip
    return reused == fresh


def _is_repo(root: Path) -> bool:
    got = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"], capture_output=True,
                         check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    return got.returncode == 0


def default_root(here: Path | None = None) -> Path:
    """The checkout `enable` reads the fix from (GOH.ENABLEROOT). This file's own parent when that
    is a repository; but gates/_from_head.py re-runs it from an export (~/.cache/goh/head/<sha>), a
    `git archive` with no history, where `git show FIX:FILE` fails for every fix. There the root is
    the checkout the export was cut from (GOH_LIVE_ROOT, honoured for an export only), else
    the caller's own (its cwd's toplevel, then GOH_DIR). With none, the parent: `enable` then names
    it as no repository."""
    own = (here or Path(__file__).resolve().parent.parent).resolve()
    if (own / ".git").exists():
        return own
    live = goh_root.live_root_for(str(own))
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    for cand in (
        live,
        top.stdout.strip() if top.returncode == 0 else "",
        os.environ.get("GOH_DIR"),
    ):
        if cand and _is_repo(Path(cand)):
            return Path(cand)
    return own


def enable(path: str, *, root: Path, fix: str = "", test: str = "", why: str = "") -> str:
    """Turn the path back on; refused (ValueError) without the proof its cause needs."""
    p = reuse._path(path)
    rec = state(p.name)
    if rec["state"] == "on":
        return f"{p.name} is on"
    if rec.get("cause") == "disagree":
        file, _, name = test.partition("::")
        if not fix or not name:
            raise ValueError("a disagreement needs --fix SHA and --test FILE::TEST, its missed key")
        if not _is_repo(root):
            raise ValueError(f"{root} is not a git repository: --fix {fix} cannot be read from it")
        shown = subprocess.run(["git", "-C", str(root), "show", f"{fix}:{file}"],
                               capture_output=True, text=True, check=False,
                               stdin=subprocess.DEVNULL)  # fmt: skip
        if shown.returncode != 0 or f"def {name.split('[')[0]}(" not in shown.stdout:
            raise ValueError(f"{test} is not a test in {fix} under {root}")
    elif not why:
        raise ValueError("a starved path needs --why: what now runs its shadows")
    with _locked():
        rec = _read(p.name)
        was = {k: rec.pop(k, None) for k in ("cause", "why", "since", "evidence", "fault")}
        rec.update(
            state="on",
            window=_fresh_window(reuse.now()),
            enabled={"epoch": reuse.now(), "fix": fix, "test": test, "why": why, "was": was},
        )
        _write(rec)
    return f"{p.name} is on again"


def report() -> list[dict]:
    rows = []
    for p in reuse.PATHS.values():
        rec = state(p.name)
        win = rec["window"]
        if not p.shadow:
            verdict = f"UNSAMPLED: {p.unsampled}"
        elif rec["state"] != "on":
            verdict = f"OFF ({rec.get('cause')}): {rec.get('why')}; fault {rec.get('fault')}"
        elif win["sampled"] < p.min_samples:
            verdict = f"thin: {win['sampled']} of {p.min_samples} samples this window"
        else:
            verdict = "sampled"
        rows.append({"path": p.name, "fields": list(p.fields), "store": p.store,
                     "shadow": p.shadow or None, "every": p.every, "state": rec["state"],
                     "window": win, "verdict": verdict})  # fmt: skip
    return rows


def _shell_site(a: argparse.Namespace) -> int:
    """`use` and `compare` for a shell site (gates/_proven_audit.sh): the fields as K=V words."""
    fields = dict(f.partition("=")[::2] for f in a.fields)
    try:
        if a.verb == "use":
            print(use(a.path, fields, backstop=not a.unbacked))
            return 0
        again = (lambda: tuple(a.again)) if a.again else None
        return 0 if compare(a.path, fields, a.reused, a.fresh, again, logs=tuple(a.logs)) else 1
    except KeyError as e:
        print(f"reuse audit: {e}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("report").add_argument("--json", action="store_true")
    en = sub.add_parser("enable")
    en.add_argument("path")
    en.add_argument("--fix", default="")
    en.add_argument("--test", default="")
    en.add_argument("--why", default="")
    en.add_argument("--root", type=Path, default=None)
    us = sub.add_parser("use")
    us.add_argument("path")
    us.add_argument("--unbacked", action="store_true")
    us.add_argument("fields", nargs="*")
    co = sub.add_parser("compare")
    co.add_argument("path")
    co.add_argument("--reused", required=True)
    co.add_argument("--fresh", required=True)
    co.add_argument("--again", nargs=2, default=None)
    co.add_argument("--logs", nargs=2, default=("", ""), help="the first and the again run's logs")
    co.add_argument("fields", nargs="*")
    a = ap.parse_args(argv)
    if a.verb in ("use", "compare"):
        return _shell_site(a)
    if a.verb == "enable":
        try:
            print(enable(a.path, root=a.root or default_root(), fix=a.fix, test=a.test, why=a.why))
        except (KeyError, ValueError) as e:
            print(f"reuse audit: not enabled: {e}", file=sys.stderr)
            return 1
        return 0
    rows = report()
    if a.json:
        print(json.dumps(rows, indent=1, sort_keys=True))
        return 0
    for r in rows:
        w = r["window"]
        counts = " ".join(f"{k} {w[k]}" for k in COUNTS)
        print(f"{r['path']:9} {r['state']:4} {counts}  {r['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
