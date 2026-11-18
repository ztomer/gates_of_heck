"""The reuse auditor: every reuse path sampled, compared with a fresh run, switched off when they
disagree (GOH.REUSEAUDIT; docs/reuse.md).

    reuse_audit.py report [--json]          # per path: state, uses, samples, disagreements, flakes
    reuse_audit.py drain                    # read the queued CI shadows (exit 0; one line each)
    reuse_audit.py enable PATH --fix SHA --test NODE   # back on after a disagreement: the fix, and
                                            # the test that misses on the input it lacked
    reuse_audit.py enable PATH --why TEXT   # back on after a starved window
    reuse_audit.py use PATH [--unbacked] K=V...   # a shell site's ask: reuse, shadow or off
    reuse_audit.py compare PATH --reused R --fresh F [--again R2 F2] K=V...
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
is exact and a test can name the sampled use. An inline shadow re-runs the work at the site; a
"ci" shadow is hosted CI's full gate of the pushed commit -- goh's own release push -- queued by
`enqueue` and read by `drain` (`loopctl ci`, never polled).

A DISAGREEMENT IS RE-RUN FIRST. Known load flakes exist, so a reused verdict that differs from the
fresh one re-runs both sides (`again`: the store read again, the work done again); only a
disagreement both re-runs repeat turns the path off. One that does not repeat is a flake, counted.

OFF IS ONE RECORDED STATE: `<store>/audit/<path>.json`, never an environment variable. It names
the cause, both verdicts and the key, and a fault item is filed (`loopctl file`; GOH_LOOPCTL and
GOH_LOOP_QUEUE are the seams; it names its class as rules.class_first asks, `class_of: none`; one
that cannot be filed is named in the record and at every SessionStart, hooks/claude/
estate_advisories.py). A CI run that judged nothing (cancelled, timed out, no CI) is no fresh side:
its shadow is dropped uncompared, never read as red (GOH.REUSEOFFLANDED). It stays off until
`enable` names the fix commit and the calibrated-miss test it added, both found in the tree.

A STARVED SAMPLER IS NEVER CLEAN. Each path's counters run in a window (WINDOW_S). When a window
closes with fewer compared samples than min(min_samples, the samples it drew), the shadows never
ran: the path turns off (cause "starved"). A window that drew fewer samples than min_samples reads
"thin" in the report, never "clean". Pinned by tests/test_reuse_audit.py.
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
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lock_order  # lib/: the one acquire path (GOH.LOCKORDER)
import reuse  # lib/, beside this file: the one store and its path registry

WINDOW_S = 7 * reuse.DAY_S
COUNTS = ("uses", "sampled", "compared", "agreed", "disagreed", "flakes")
CI_GREEN, CI_RED, CI_NONE = 0, 1, 4  # `loopctl ci`'s exits; 3 pending, 75 throttled stay queued
# `loopctl ci` exits 1 for red AND for unread, and for a run that ended without judging (cancelled
# at GitHub's limit or superseded, timed out), which it prints as "red" with "no verdict". Its
# printed state line decides: v0.133.0's cancelled run 44203722455 read as red and turned `landed`
# off (GOH.REUSEOFFLANDED). NO_VERDICT, a run that judged nothing: the shadow is dropped uncompared.
NO_VERDICT = "none"
_CI_LINE = re.compile(r"\bCI (green|red|unread|pending|none|throttled): (.*)")
_UNJUDGED = re.compile(r"\b(?:cancelled|timed_out)\b|\bno verdict\b")


def _dir() -> Path:
    return reuse.root() / "audit"


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    _dir().mkdir(parents=True, exist_ok=True)
    with (_dir() / ".lock").open("a") as fh:
        lock_order.flock(fh, "reuse_audit")  # released when the file closes
        yield


def _fresh_window(start: int) -> dict:
    return {"start": start, **dict.fromkeys(COUNTS, 0)}


def _read(name: str) -> dict:
    try:
        rec = json.loads((_dir() / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = {}
    if not isinstance(rec, dict) or not isinstance(rec.get("window"), dict):
        rec = {"path": name, "state": "on", "window": _fresh_window(reuse.now()), "closed": []}
    return rec


def _write(rec: dict) -> None:
    dest = _dir() / f"{rec['path']}.json"
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, dest)


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
            again: Callable[[], tuple[str, str]] | None = None) -> bool:  # fmt: skip
    """A sampled reuse against its fresh run; True when they agree. A disagreement re-runs both
    sides (`again` returns the store's verdict read again and the work's done again): only a
    repeated one turns the path off and files the fault; one `again` cannot repeat is a flake."""
    p = reuse._path(path)
    line = {"path": p.name, "key": reuse.key(p, fields), "fields": fields, "reused": reused,
            "fresh": fresh, "epoch": reuse.now()}  # fmt: skip
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
        with (_dir() / "shadow.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**line, "outcome": kind}, sort_keys=True) + "\n")
    return reused == fresh


def file_fault(p: reuse.ReusePath, cause: str, why: str, line: dict) -> str:
    """File the fault item (loopctl file); the outcome, as the state record names it."""
    loopctl = os.environ.get("GOH_LOOPCTL") or str(Path.home() / "Projects/.loop/tools/loopctl.py")
    queue = os.environ.get("GOH_LOOP_QUEUE", "")
    if not os.path.isfile(loopctl):
        return f"not filed: no loopctl at {loopctl}"
    iid = "GOH.REUSEOFF" + "".join(c for c in p.name.upper() if c.isalnum())
    item = {"id": iid, "repo": "gates_of_heck", "status": "todo", "phase": "SPD", "depends": [],
            "source": "goh reuse audit (lib/reuse_audit.py)",
            "item": f"The reuse path {p.name!r} ({p.doc}) is OFF: {cause}: {why}. Both verdicts "
            f"and the key: {json.dumps(line, sort_keys=True)}",
            "done_state": f"the missing key input found and keyed, a test that misses on it, and "
            f"`reuse_audit.py enable {p.name} --fix SHA --test NODE`",
            # rules.class_first (loopctl): each path's off is its own instance, no open closure's
            "class_of": "none",
            "class_why": f"the reuse path {p.name!r} turned off on its own evidence ({cause}); its "
            "cause is that path's key or reader, found from this record"}  # fmt: skip
    argv = [sys.executable, loopctl, *(["--queue", queue] if queue else []), "file", "--json",
            json.dumps(item)]  # fmt: skip
    try:
        r = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120,
                           stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"not filed: {e}"
    said = (r.stdout + r.stderr).strip()
    if r.returncode == 0 or "already exists" in said:
        return f"filed {iid}"
    return f"not filed: {said[-200:]}"


def unfiled() -> list[str]:
    """One line per path that is off with its fault NOT filed: read-only (no roll, no lock), for
    SessionStart (hooks/claude/estate_advisories.py), so a refused filing alerts someone."""
    out = []
    for name in reuse.PATHS:
        try:
            rec = json.loads((_dir() / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fault = str(rec.get("fault") or "") if isinstance(rec, dict) else ""
        if rec.get("state") == "off" and not fault.startswith("filed "):
            out.append(f"{name} off ({rec.get('cause')}), fault {fault or 'not filed'}")
    return out


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


def enqueue(path: str, fields: dict[str, str], reused: str, *, slug: str, sha: str) -> Path:
    """Queue a "ci" shadow: hosted CI's full gate of SHA on SLUG is the fresh side."""
    p = reuse._path(path)
    k = reuse.key(p, fields)
    dest = _dir() / "pending" / f"{p.name}-{k[:16]}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    entry = {"path": p.name, "fields": fields, "reused": reused, "slug": slug, "sha": sha,
             "epoch": reuse.now(), "reads": []}  # fmt: skip
    dest.write_text(json.dumps(entry, sort_keys=True), encoding="utf-8")
    return dest


def ci_verdict(code: int, said: str) -> str:
    """`loopctl ci`'s answer as the audit's fresh side: "green", "red", NO_VERDICT (a run that
    judged nothing: it never will) or "" (unread for now: kept). A red needs its printed line."""
    m = _CI_LINE.search(said)
    state, text = (m.group(1), m.group(2)) if m else ("", "")
    if code == CI_GREEN:
        return "green"
    if code == CI_NONE:
        return NO_VERDICT
    if code != CI_RED or state != "red":
        return ""
    return NO_VERDICT if _UNJUDGED.search(text) else "red"


def _ci(slug: str, sha: str) -> str:
    loopctl = os.environ.get("GOH_LOOPCTL") or str(Path.home() / "Projects/.loop/tools/loopctl.py")
    try:
        r = subprocess.run([sys.executable, loopctl, "ci", sha, "--repo", slug], timeout=120,
                           capture_output=True, text=True, check=False,
                           stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return ci_verdict(r.returncode, r.stdout + r.stderr)


def drain() -> list[str]:
    """Read each queued CI shadow once. A verdict that agrees is compared; one that disagrees is
    read again at the next drain (CI's own re-runs settle between), and compared then, both sides
    again; an unread one (pending, throttled) stays queued."""
    said = []
    for f in sorted((_dir() / "pending").glob("*.json")):
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            f.unlink(missing_ok=True)
            continue
        fresh = _ci(e["slug"], e["sha"])
        if not fresh:
            said.append(f"{e['path']} {e['sha'][:12]}: CI unread, kept")
            continue
        if fresh == NO_VERDICT:  # cancelled, timed out, no CI: nothing to compare, ever
            f.unlink(missing_ok=True)
            said.append(f"{e['path']} {e['sha'][:12]}: CI no verdict (cancelled, timed out or "
                        "none), dropped uncompared")  # fmt: skip
            continue
        e["reads"].append(fresh)
        if fresh != e["reused"] and len(e["reads"]) < 2:
            f.write_text(json.dumps(e, sort_keys=True), encoding="utf-8")
            said.append(
                f"{e['path']} {e['sha'][:12]}: CI {fresh}, reused {e['reused']}: read again"
            )
            continue
        first = e["reads"][0]
        compare(e["path"], e["fields"], e["reused"], first, lambda e=e, f2=fresh: (e["reused"], f2))
        f.unlink(missing_ok=True)
        said.append(f"{e['path']} {e['sha'][:12]}: CI {fresh}, reused {e['reused']}")
    return said


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
        return 0 if compare(a.path, fields, a.reused, a.fresh, again) else 1
    except KeyError as e:
        print(f"reuse audit: {e}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("report").add_argument("--json", action="store_true")
    sub.add_parser("drain")
    en = sub.add_parser("enable")
    en.add_argument("path")
    en.add_argument("--fix", default="")
    en.add_argument("--test", default="")
    en.add_argument("--why", default="")
    en.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    us = sub.add_parser("use")
    us.add_argument("path")
    us.add_argument("--unbacked", action="store_true")
    us.add_argument("fields", nargs="*")
    co = sub.add_parser("compare")
    co.add_argument("path")
    co.add_argument("--reused", required=True)
    co.add_argument("--fresh", required=True)
    co.add_argument("--again", nargs=2, default=None)
    co.add_argument("fields", nargs="*")
    a = ap.parse_args(argv)
    if a.verb in ("use", "compare"):
        return _shell_site(a)
    if a.verb == "drain":
        print("\n".join(drain()) or "reuse audit: no CI shadow queued")
        return 0
    if a.verb == "enable":
        try:
            print(enable(a.path, root=a.root, fix=a.fix, test=a.test, why=a.why))
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
