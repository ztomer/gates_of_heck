"""The ONE verdict store and the ONE API every reuse path uses to reach it (GOH.ONEVERDICT).

goh kept a verdict store per reuse path, each keyed its own way (docs/reuse.md maps them all): the
push gate wrote `green <sha>` into a per-ship file only while `goh ship` ran, so a commit the land
queue had already pushed and gated was "never gated" to the ship (koffee_big ship
20261115T121828Z-952ef800: push.log "pre-push: nothing to gate", the release kit's full gate re-run,
3137.5 s for the release step). A verdict is now one record, keyed by what decided it, in one store
any caller can query:

    reuse.py paths                                   # the registered paths and their key fields
    reuse.py record-gate --repo R [--] SHA[:HOW]...  # the push gate: each SHA green under HOW
                                                     # (full, release or config; default full)
    reuse.py full-suite --repo R --sha SHA           # exit 0: goh's whole suite judged SHA's
                                                     # tree itself, instrumented (ship_suite.py)
    reuse.py lookup-gate --repo R --sha SHA [--how full]
                                                     # exit 0, the record printed: a green verdict
                                                     # on SHA's TREE under today's gate identity;
                                                     # exit 1, the reason on stderr: a miss

THE KEY. A reuse path REGISTERS its name and its key fields (`register`, below, the one table).
A record's key is sha256 over the path name and each field's value in the registered order; a
lookup names every field and nothing else (KeyError otherwise), so a path cannot be asked with a
partial key -- the class CIP.CACHING found twice (a proven step keyed on four tools of eight; a
loaded verdict keyed on the tree alone). The record carries its own key, fields and epoch: a
record under the wrong name, an unreadable one, or one older than the path's TTL is a MISS.

THE `gate` PATH: (repo, tree, identity). repo is the common git dir, so every worktree of a repo
shares its verdicts; tree is the commit's tree, so a verdict follows the bytes, not the sha (a
rebased or re-committed tree is the same verdict; a changed one misses); identity is
lib/gate_identity.py's key for the gate mode -- the gates' content (goh's version is its bytes),
the goh binary and every tool a step may run -- so another goh or another shellcheck misses. TTL
one day (GOH_REUSE_TTL_S), the proven cache's: an advisory database or a remote moves without the
tree. Writers: gates/push_gate.sh (every commit it gated, every push, not only under `goh ship`)
and, read through `landed_identity` until tools/land.sh writes here, the land record `goh-landed`.
Readers: `goh ship verdict` (lib/ship_verdict.py), the release push rule (lib/release_gate.py).

THE `pushed` PATH: (repo, tree), deliberately WITHOUT the gate identity. The floor rule
(lib/config_push.py) asks whether the remote's tip was ever push-gated green: a commit the remote
holds is never re-gated under newer gates, so the floor push certifies only what the remote gains
under today's structural gate, and a key with the identity would turn the rule off at every goh
change. The same `record-gate` call writes it, beside `gate`. The `goh-pushed` file it replaces
is no longer written, and read here only, by sha, for a base pushed before the store (LEGACY):
without it every consumer's first floor push re-ran its whole gate. TTL a year: the base is the
remote's tip, however old.

THE AUDIT. Every reuse in goh is a registered path here -- its sites, store, shadow and rate --
wherever its records live (docs/reuse.md); lib/reuse_audit.py samples, compares and switches off
(GOH.REUSEAUDIT). tests/test_reuse_audit.py refuses a reuse site no path names.

The store's root: GOH_REUSE_DIR (absolute), else $XDG_STATE_HOME/goh/reuse, else
~/.local/state/goh/reuse. Nothing else reads or writes under it, nor the legacy record files named
in LEGACY (tests/test_reuse_store.py). Pinned by tests/test_reuse.py.
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
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_identity  # lib/, beside this file: the one identity (GOH.VERDICTKEY)

FORMAT = "reuse v1"
DAY_S = 86400
# Record files outside the store that a reuse path still writes, read only through this module
# until their writers move here (docs/reuse.md, the order).
LEGACY = ("goh-landed", "goh-pushed")


# How a path's sampled reuse is re-run (lib/reuse_audit.py): at the site, right then ("inline");
# by hosted CI's full gate of the pushed commit, read later ("ci"); or not yet ("": `unsampled`
# says why, and the audit's report names the path UNSAMPLED -- never clean).
SHADOWS = ("inline", "ci", "")


@dataclass(frozen=True)
class ReusePath:
    name: str
    fields: tuple[str, ...]
    ttl_s: int
    doc: str
    store: str = "lib/reuse.py"  # where its records live until GOH.ONEVERDICT2 moves them here
    sites: tuple[str, ...] = ()  # the files that skip work on its verdict (test_reuse_audit.py)
    shadow: str = ""
    every: int = 10  # one use in `every` is sampled (1: every use -- a ship's)
    every_unbacked: int = 0  # the rate where no hosted CI backs the skip (backstop=False); 0: every
    min_samples: int = 3  # compared samples a window needs, or the path turns off (starved)
    unsampled: str = ""


PATHS: dict[str, ReusePath] = {}


def register(name: str, fields: tuple[str, ...], ttl_s: int, doc: str, **audit) -> ReusePath:
    """Name a reuse path and the fields its key is made of. Once per name, at least one field;
    a path with no shadow says why (`unsampled`)."""
    if name in PATHS:
        raise ValueError(f"reuse path {name!r} is registered twice")
    if not fields or len(set(fields)) != len(fields):
        raise ValueError(f"reuse path {name!r}: its key fields must be distinct and non-empty")
    p = ReusePath(name, tuple(fields), ttl_s, doc, **audit)
    if p.shadow not in SHADOWS or bool(p.shadow) == bool(p.unsampled) or p.every < 1 \
            or p.every_unbacked < 0:  # fmt: skip
        raise ValueError(f"reuse path {name!r}: a shadow of {SHADOWS}, or `unsampled` and why")
    PATHS[name] = p
    return p


GATE = register(
    "gate",
    ("repo", "tree", "identity"),
    DAY_S,
    "a green gate over a commit's tree: the push gate writes; ship verdict, the release rule read",
    sites=("lib/ship_verdict.py",),
    unsampled="its fresh side is a full gate of the tree in an idle local slot, which only the "
    "idle lander (tools/landq.py) schedules; hosted CI's gate of a public repo reads via `landed`",
)


PUSHED = register(
    "pushed",
    ("repo", "tree"),
    365 * DAY_S,
    "a tree some push gate judged green, under any gates: the floor rule's base (config_push.py)",
    sites=("lib/config_push.py",),
    unsampled="identity-free on purpose: its fresh side is a gate of the remote's tip under the "
    "gates that pushed it, which no later run has (docs/reuse.md)",
)

# The reuse paths whose records still live in their own stores (docs/reuse.md, the order): each
# registered with its key fields and sites, so the audit counts, samples and switches it off.
LANDED = register(
    "landed", ("repo", "sha", "identity"), 365 * DAY_S,
    "the release push rule: release files only on a parent whose land was green (release_gate.py)",
    store="goh-landed", sites=("lib/release_gate.py",), shadow="ci", every=1, min_samples=1,
)  # fmt: skip
register(
    "origin", ("repo", "sha", "remote"), 0,
    "the push gate's 'the remote already has this commit': nothing new to gate",
    store="the remote's refs", sites=("gates/push_gate.sh",),
    unsampled="no verdict is reused: the skip is a scope rule (the remote gains no code), and a "
    "fresh gate of a commit the remote holds judges old code under today's tools (push_gate.sh)",
)  # fmt: skip
ESTATE = register(
    "estate", ("identity", "entry", "corpus"), 7 * DAY_S,
    "check_estate_corpus's VERIFIED entry, keyed on the checker, the entry and the corpus stat",
    store="~/.cache/goh/estate-corpus", sites=("checks/_estate_cache.py",), shadow="inline",
)  # fmt: skip
TEST = register(
    "test", ("identity", "node"), DAY_S,
    "a land's pre-step pass, deselected from its gate's suite (tests/_land_verdicts.py)",
    store="one land's GOH_LAND_VERDICTS_RECORD file", sites=("tests/_land_audit.py",),
    shadow="inline",  # one use per node, decided by the controller for every worker
)  # fmt: skip
CARRY = register(
    "carry", ("repo", "tree", "tools"), DAY_S,
    "goh's suite passes on a tree, carried to a tree whose diff from it does not choose them "
    "(tools/affected_tests.py) -- the land gate's suite deselects them (tests/_land_verdicts.py)",
    sites=("tests/_land_verdicts.py",), shadow="inline",
)  # fmt: skip
register(
    "affected", ("repo", "base", "head"), 0,
    "the pre-land test set chosen from the diff (tools/affected_tests.py)",
    store="none: the diff", sites=("tools/affected_tests.py",),
    unsampled="a selection the full gate re-runs whole: it skips nothing but through `test`",
)  # fmt: skip
register(
    "step", ("repo", "tree", "step", "closure", "identity", "env"), DAY_S,
    "a proven gate step: skipped when its inputs and the goh files that can decide it (its "
    "closure, lib/step_closure.py, GOH.PROVENCLOSURE) are unchanged",
    # proven.sh and local_ci.sh ask before a skip; a repo no hosted CI gates is sampled 4x as
    # often. The kill switch closure keying's flip waits on (GOH.PROVENCLOSURE2).
    store="goh-proven", sites=("gates/_proven_audit.sh",), shadow="inline", every=16,
    every_unbacked=4,
)  # fmt: skip
register(
    "rustcov", ("repo", "rust_identity"), DAY_S,
    "the Rust coverage measurement and its sccache/llvm-cov build, reused by identity",
    store="goh-proven (gates/_rust_proven.sh)", sites=("gates/_rust_proven.sh",),
    unsampled="sampled through `profcarry` (GOH.COVCARRY2): a reuse claims its kept summary for "
    "the tree, and the idle lander's fresh instrumented run of that tree compares; a shadow here "
    "would be a whole instrumented build and measurement under rust_gate's set -e",
)  # fmt: skip
PROFCARRY = register(
    "profcarry", ("repo", "tree", "tools"), DAY_S,
    "a land's per-crate coverage claim on a tree, built on reuse (carried suite verdicts, "
    "rustcov's reused measurement), compared by the fresh full instrumented run (prof_carry.py)",
    sites=("lib/prof_carry.py",), shadow="inline",
)  # fmt: skip
register(
    "file", ("checker", "key"), 30 * DAY_S,
    "a per-file checker verdict (shell-lint's cache)",
    store="~/.cache/goh/verdicts", sites=("crates/goh/src/verdict_cache.rs",),
    unsampled="the per-file hot path inside the goh binary, thousands of hits a gate at "
    "microseconds each against a ~60 ms ask of lib/reuse_audit.py: it needs the audit's counter "
    "in Rust",
)  # fmt: skip


def root() -> Path:
    named = os.environ.get("GOH_REUSE_DIR", "")
    if named:
        if not named.startswith("/"):
            raise ValueError(f"GOH_REUSE_DIR must be an absolute path: {named!r}")
        return Path(named)
    state = os.environ.get("XDG_STATE_HOME", "")
    base = Path(state) if state.startswith("/") else Path.home() / ".local" / "state"
    return base / "goh" / "reuse"


def _path(path: str | ReusePath) -> ReusePath:
    p = PATHS.get(path) if isinstance(path, str) else path
    if p is None:
        raise KeyError(f"no reuse path {path!r} is registered (lib/reuse.py)")
    return p


def key(path: str | ReusePath, fields: dict[str, str]) -> str:
    """sha256 of the path and each registered field, in order. A missing or extra field raises."""
    p = _path(path)
    if set(fields) != set(p.fields):
        missing = sorted(set(p.fields) - set(fields))
        extra = sorted(set(fields) - set(p.fields))
        raise KeyError(
            f"reuse path {p.name!r} is keyed by {p.fields}: missing {missing}, extra {extra}"
        )
    text = "\n".join([FORMAT, f"path {p.name}", *(f"{f} {fields[f]}" for f in p.fields)])
    return hashlib.sha256(text.encode()).hexdigest()


def _file(p: ReusePath, k: str) -> Path:
    return root() / p.name / k[:2] / f"{k}.json"


def ttl(p: ReusePath) -> int:
    raw = os.environ.get("GOH_REUSE_TTL_S", "")
    return int(raw) if raw.isdigit() else p.ttl_s


def now() -> int:
    """Epoch seconds; GOH_REUSE_NOW is the clock seam the tests age a record with."""
    raw = os.environ.get("GOH_REUSE_NOW", "")
    return int(raw) if raw.isdigit() else int(time.time())


def record(path: str | ReusePath, fields: dict[str, str], verdict: str, by: str = "",
           extra: dict[str, str] | None = None) -> Path:  # fmt: skip
    """Write the verdict for these fields whole (a temp file renamed into place)."""
    p = _path(path)
    k = key(p, fields)
    dest = _file(p, k)
    dest.parent.mkdir(parents=True, exist_ok=True)
    rec = {"format": FORMAT, "path": p.name, "key": k, "fields": fields, "verdict": verdict,
           "by": by, "epoch": now(), **(extra or {})}  # fmt: skip
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, sort_keys=True)
    os.replace(tmp, dest)
    return dest


def lookup(path: str | ReusePath, fields: dict[str, str]) -> tuple[dict | None, str]:
    """(record, "") for a live record of these fields, else (None, why it missed)."""
    p = _path(path)
    k = key(p, fields)
    f = _file(p, k)
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"no {p.name} verdict for this key"
    except (OSError, ValueError) as e:
        return None, f"the {p.name} record {f} is unreadable ({e})"
    if not isinstance(rec, dict) or rec.get("key") != k or rec.get("fields") != fields:
        return None, f"the {p.name} record {f} is not this key's"
    epoch = rec.get("epoch")
    age = now() - epoch if isinstance(epoch, int) else -1
    if not 0 <= age < ttl(p):
        return None, f"the {p.name} verdict is {age} s old, outside its {ttl(p)} s"
    rec["file"] = str(f)
    return rec, ""


def prune(path: str | ReusePath) -> int:
    """Delete the path's records older than its TTL (and unreadable ones); the count removed. A
    writer calls it once per write batch, so the store stays the size of one TTL of verdicts."""
    p = _path(path)
    gone = 0
    for f in (root() / p.name).glob("*/*.json"):
        try:
            epoch = json.loads(f.read_text(encoding="utf-8")).get("epoch")
        except (OSError, ValueError, AttributeError):
            epoch = None
        if not isinstance(epoch, int) or not 0 <= now() - epoch < ttl(p):
            f.unlink(missing_ok=True)
            gone += 1
    return gone


# --- the `gate` path ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return out.stdout.strip() if out.returncode == 0 else ""


def repo_id(repo: Path) -> str:
    """The repo as a key field: its common git dir, one for every worktree."""
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.realpath(common) if common else ""


def gate_fields(repo: Path, sha: str, how: str = "full") -> dict[str, str] | None:
    tree = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}")
    rid = repo_id(repo)
    if not tree or not rid:
        return None
    return {"repo": rid, "tree": tree, "identity": gate_identity.key(how)}


def record_gate(repo: Path, shas: list[str], by: str) -> list[Path]:
    """Each `SHA[:HOW]` green under the gate identity of HOW (empty: full)."""
    out = []
    identity: dict[str, str] = {}
    rid = repo_id(repo)
    for item in shas:
        sha, _, how = item.partition(":")
        how = how or "full"
        tree = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}")
        if not tree or not rid:
            raise LookupError(f"{sha} names no commit tree in {repo}")
        ident = identity.setdefault(how, gate_identity.key(how))
        fields = {"repo": rid, "tree": tree, "identity": ident}
        out.append(record(GATE, fields, "green", by, {"sha": sha, "how": how}))
        record(PUSHED, {"repo": rid, "tree": tree}, "green", by, {"sha": sha, "how": how})
    prune(GATE)
    prune(PUSHED)
    return out


def pushed(repo: Path, sha: str) -> bool:
    """True when a push gate judged sha's tree green, under whatever gates it ran (`pushed`)."""
    tree = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}")
    rid = repo_id(repo)
    if not tree or not rid:
        return False
    rec, _ = lookup(PUSHED, {"repo": rid, "tree": tree})
    if rec is not None:
        return rec.get("verdict") == "green"
    # The file this path replaced, read-only: a remote tip pushed before the store existed is
    # still a green base, or the first floor push of every consumer re-ran its whole gate.
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    try:
        lines = (Path(common) / "goh-pushed").read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return any(ln.split()[:1] == [sha] for ln in lines)


def landed_identity(repo: Path, sha: str) -> str | None:
    """The gate identity tools/land.sh recorded with sha (`<sha> <identity>` in goh-landed, written
    only after a green gate), `""` for a line without one, None when no land recorded sha. The
    legacy land record, read here only (LEGACY) until land.sh records into the store."""
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    try:
        lines = (Path(common) / "goh-landed").read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, NotADirectoryError):
        return None
    found = [ln.split()[1:] for ln in lines if ln.split()[:1] == [sha]]
    return (found[-1][:1] or [""])[0] if found else None


def lookup_gate(repo: Path, sha: str, how: str = "full") -> tuple[dict | None, str]:
    """A green verdict on sha's tree under today's identity of HOW: the store's, or a land's."""
    fields = gate_fields(repo, sha, how)
    if fields is None:
        return None, f"{sha} names no commit tree in {repo}"
    rec, why = lookup(GATE, fields)
    if rec is not None and rec.get("verdict") == "green":
        return rec, ""
    if rec is not None:
        why = f"the gate verdict on {fields['tree'][:12]} is {rec.get('verdict')!r}"
    if how == "full" and landed_identity(repo, sha) == fields["identity"]:
        return {"path": GATE.name, "fields": fields, "verdict": "green", "by": "goh-landed",
                "sha": sha, "file": "goh-landed"}, ""  # fmt: skip
    return None, why


# --- the `carry` path (GOH.VERDICTCARRY) ---------------------------------------------------------


def carry_fields(repo: Path, tree: str, tools: str) -> dict[str, str]:
    return {"repo": repo_id(repo), "tree": tree, "tools": tools}


def record_suite(repo: Path, tree: str, tools: str, passed: dict[str, bool], failed: set[str],
                 full: bool, by: str, carried: int = 0, instrumented: bool = False,
                 measured: dict[str, bool] | None = None,
                 judged: set[str] | None = None) -> Path:  # fmt: skip
    """Merge one run's verdicts on `tree` into its record: `passed` ({node: it ran the binary})
    joins the passes, a node in `failed` leaves them. `full`: this run judged every test of the
    tree itself (none carried, nothing failed), so an old pass it neither made nor found judged
    leaves (an id it never collected, as one naming another checkout: GOH.SHIPTREEFULL). A record
    stays full while no later run fails and every pass in it is `judged` (GOH.CARRYASFULL).
    `instrumented`: a run drove the INSTRUMENTED binary (GOH.COVCARRY), a carrying land too; its
    WHOLE judgement did: `full_instrumented`, a ship's need (GOH.PROFKEYMISS). `measured` ({node:
    it left a profile}): what such a run SAW, all coverage may carry; `judged`: passes made HERE."""
    old = lookup(CARRY, fields := carry_fields(repo, tree, tools))[0] or {}
    mine = (set(old.get("judged") or ()) | (set(passed) if judged is None else judged)) - failed
    merged = {n: bool(b) for n, b in old.get("passed", {}).items()
              if n not in failed and (n in mine or not full)}  # fmt: skip
    merged.update(passed)
    whole = (full or bool(old.get("full"))) and not failed and set(merged) <= mine
    inst = ((full and instrumented) or bool(old.get("full_instrumented"))) and whole
    seen = {n: bool(b) for n, b in old.get("measured", {}).items() if n not in failed}
    extra = {"passed": merged, "full": whole, "carried": carried, "full_instrumented": inst,
             "instrumented": instrumented or bool(old.get("instrumented")),
             "measured": seen | (measured or {}), "judged": sorted(mine & set(merged))}  # fmt: skip
    out = record(CARRY, fields, "red" if failed else "green", by, extra)
    prune(CARRY)
    return out


def lookup_suite(repo: Path, tree: str, tools: str) -> tuple[dict | None, str]:
    """The suite record of `tree` under `tools`, or (None, why it missed)."""
    rec, why = lookup(CARRY, carry_fields(repo, tree, tools))
    if rec is not None and not isinstance(rec.get("passed"), dict):
        return None, f"the carry record {rec.get('file')} holds no passes"
    return rec, why


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("paths")
    rg = sub.add_parser("record-gate")
    rg.add_argument("--repo", type=Path, default=Path.cwd())
    rg.add_argument("--by", default="push")
    rg.add_argument("shas", nargs="*")
    lg = sub.add_parser("lookup-gate")
    lg.add_argument("--repo", type=Path, default=Path.cwd())
    lg.add_argument("--sha", required=True)
    lg.add_argument("--how", default="full")
    fs = sub.add_parser("full-suite")
    fs.add_argument("--repo", type=Path, default=Path.cwd())
    fs.add_argument("--sha", required=True)
    a = ap.parse_args(argv)
    if a.verb == "paths":
        for p in PATHS.values():
            print(f"{p.name}\t{','.join(p.fields)}\tttl {p.ttl_s}s\t{p.doc}")
        return 0
    if a.verb == "record-gate":
        try:
            for f in record_gate(a.repo, a.shas, a.by):
                print(f)
        except (LookupError, OSError) as e:
            print(f"reuse: no gate verdict recorded: {e}", file=sys.stderr)
            return 1
        return 0
    if a.verb == "lookup-gate":
        rec, why = lookup_gate(a.repo, a.sha, a.how)
        if rec is None:
            print(f"reuse: miss: {why}", file=sys.stderr)
            return 1
        print(json.dumps(rec, sort_keys=True))
        return 0
    if a.verb == "full-suite":  # the check itself lives with the run that makes it
        import ship_suite  # lib/: imports this module, so never at its top

        rec, why = ship_suite.full_suite(a.repo, a.sha)
        if rec is None:
            print(f"reuse: no full suite verdict: {why}", file=sys.stderr)
            return 1
        print(f"full suite verdict: {rec['file']} (tree {rec['fields']['tree'][:12]})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
