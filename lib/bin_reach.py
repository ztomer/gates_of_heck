"""Which binary tests a Rust change can reach, read from their stored profiles (GOH.BINREACH).

WHY. A change to goh's crate chose every test that runs the binary and reaches a subcommand the
change can reach (lib/binary_runners.py, lib/crate_reach.py): a 2-path change chose 2,736 tests,
2,863 test-s, and the enqueue's pre-step ran them all at 5 workers (the test-once design, lever 4).
Most of them never execute the changed file. Each binary test's profile is kept per land
(lib/prof_store.py, tests/_prof_record.py): the functions it ran, by their mangled names, which
name the module each lives in. So at the ENQUEUE (affected_tests' enqueue mode only; the gate and
the lander still choose every runner) a test is chosen when its profile reaches the change.

THE REACH OF A CHANGE (`touched`). Not only the changed file: a constant, a static, a type or a
generic has no executed region of its own -- its bytes are compiled into the files that name it.
So a change reaches its file, every file whose code names it (crate_reach's use and path edges,
calls and constant reads alike: a file that both calls a module and reads its constant holds only
the call edge), and, transitively, every file reading a constant of one of those (a constant's
initialiser names another's). main.rs and ported.rs (the dispatch files every test executes) count
only through the constants they read: their calls are the dispatch, which runs the callee's own
regions. A change the graph cannot place -- outside goh's src modules and their embeds, a file
defining a `macro_rules!` (expanded where it is used, possibly with no path) -- narrows nothing; nor
does one to a module every run passes through before dispatch (crate_reach `shared`: cli.rs,
cli_args, main.rs): a derive's output is not instrumented, so clap's parser, generated in cli.rs,
runs in every test while no profile ever enters cli.rs (0 of 1,743 stored profiles do).

THE REACH OF A TEST (`Reach`). Every stored key's profiles, each read once (`llvm-profdata show
--all-functions`) and kept as the module paths its entered functions live in
(<reuse root>/profreach/<key>.json); a test's reach is the UNION over every key the store holds
(the profiles are the last lands' and ships', so a union of several trees is the conservative
read). A v0 symbol is parsed for the module its code sits in (`home`: an impl's methods live where
the impl is, not where its type is); one that does not parse makes its test chosen.

ALWAYS CHOSEN: a test with no stored profile (new, never run instrumented, or pruned: the store
keeps ~880-1,740 of ~1,740 binary nodes per key), a test whose profile names an unparsed symbol, a
file whose nodes cannot be told apart from its source (`names`: a test not defined in the file, a
nested class), and every runner when no profile is stored at all.

THE AUDIT: one land in ten also runs the binary tests the reach left out (lib/bin_reach_audit.py).
GOH_BINREACH=off chooses every runner again.
Pinned by tests/test_bin_reach.py.

GOH.BINREACH: at the ENQUEUE only, a Rust change's binary tests by their stored profiles' reach:
each `profstore` key's profiles read once (`llvm-profdata show --all-functions`, cached in `<reuse
root>/profreach/`, a ship key mapped back by its `index.json`), a test's reach the union over keys;
a change reaches its file, every file naming it and, transitively, every reader of a constant of
those (`touched`; a macro or an unplaceable path narrows nothing). Always chosen: a test with no
stored profile, an unread symbol, a file whose nodes its source cannot tell; left out: a node the
suite records measured starting no goh. `GOH_BINREACH=off`.
"""

from __future__ import annotations

import ast
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

try:
    from lib import crate_reach, reuse
    from lib.v0_home import UNKNOWN, homes
except ImportError:  # run by path, not as a package
    import crate_reach  # type: ignore[no-redef]
    import reuse  # type: ignore[no-redef]
    from v0_home import UNKNOWN, homes  # type: ignore[no-redef]

KILL = "GOH_BINREACH"
RECORDS = 30  # suite records read for the nodes measured starting no goh (`free`)
CRATE = "goh"
MACRO = re.compile(r"\bmacro_rules\s*!")


def enabled() -> bool:
    return os.environ.get(KILL, "on") != "off"


# --- the reach of each stored test --------------------------------------------------------------


def profdata_tool() -> str | None:
    """rustc's own llvm-profdata (tests/_prof_record.py `_profdata`)."""
    rustc = shutil.which("rustc")
    if not rustc:
        return None
    r = subprocess.run([rustc, "--print", "target-libdir"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    cand = Path(r.stdout.strip()).parent / "bin" / "llvm-profdata"
    return str(cand) if r.returncode == 0 and cand.is_file() else None


def _show(tool: str, f: Path) -> list[str] | None:
    r = subprocess.run([tool, "show", "--all-functions", str(f)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return homes(r.stdout) if r.returncode == 0 else None


def _cache(k: str) -> Path:
    return reuse.root() / "profreach" / f"{k}.json"


def index(key_dir: Path, tool: str | None) -> dict[str, list[str]]:
    """{node hash: the homes its profile entered} for one stored key, read once and cached."""
    cache = _cache(key_dir.name)
    try:
        got = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(got, dict) and got.get("n") == len(list(key_dir.glob("*.profdata"))):
            return got["reach"]
    except (OSError, ValueError, KeyError):
        pass
    files = sorted(key_dir.glob("*.profdata"))
    out: dict[str, list[str]] = {}
    if tool and files:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for f, got in zip(files, pool.map(lambda f: _show(tool, f), files), strict=True):
                if got is not None:
                    out[f.stem] = got
        out = _by_node(key_dir, out)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f".{cache.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"n": len(files), "reach": out}), encoding="utf-8")
        os.replace(tmp, cache)
    return out


def free(repo: Path | None, newest: int = RECORDS) -> frozenset[str]:
    """The nodes the newest `newest` suite records of `repo` (lib/reuse.py's `carry` path) MEASURED
    starting no goh -- a calibrated worker ran them and no goh left its mark
    (tests/_prof_record.py) -- and none measured starting one: no change to the binary reaches a
    test that never runs it. The coverage carry trusts the same measurement."""
    if repo is None:
        return frozenset()
    rid = reuse.repo_id(repo)
    recs = sorted(reuse.records(reuse.CARRY), key=lambda r: (r[2] >= 0, -r[2]))[-newest:]
    no, yes = set(), set()
    for _, rec, _ in recs:  # the newest last (the smallest age; no epoch: oldest)
        if (rec.get("fields") or {}).get("repo") != rid:
            continue
        for n, ran in (rec.get("measured") or {}).items():
            (yes if ran else no).add(n)
    return frozenset(no - yes)


def _by_node(key_dir: Path, by_stem: dict[str, list[str]]) -> dict[str, list[str]]:
    """BY_STEM keyed by node hash: a ship key names each profile by its node AND the digest of
    what its file reads (tests/_prof_reuse.py `slot`, its `index.json`: {node: digest}); a land
    key by the node alone."""
    try:
        names = json.loads((key_dir / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return by_stem
    if not isinstance(names, dict):
        return by_stem
    out = {}
    for n, digest in names.items():
        if (got := by_stem.get(node_hash(f"{n}\0{digest}"))) is not None:
            out[node_hash(n)] = got
    return out


class Reach:
    """Every stored test's reach, the union over the store's keys (module doc), and the nodes
    measured starting no goh (`free`)."""

    def __init__(self, by_hash: dict[str, set[str]], free_nodes: frozenset[str] = frozenset()):
        self.by_hash = by_hash
        self.free = free_nodes

    @classmethod
    def load(cls, store: Path | None = None, repo: Path | None = None) -> Reach:
        store = reuse.root() / "profstore" if store is None else store
        by: dict[str, set[str]] = {}
        dirs = sorted(d for d in store.iterdir() if d.is_dir()) if store.is_dir() else []
        tool = profdata_tool() if dirs else None
        for d in dirs:
            for h, got in index(d, tool).items():
                by.setdefault(h, set()).update(got)
        live = {d.name for d in dirs}  # a cache whose key the store pruned goes with it
        for c in (reuse.root() / "profreach").glob("*.json") if dirs else ():
            if c.stem not in live:
                c.unlink(missing_ok=True)
        return cls(by, free(repo))

    def files(self, nodeid: str, crate: crate_reach.Crate) -> set[str] | None:
        """The crate files `nodeid`'s profile entered; None when it has no profile or names a
        symbol that did not parse (either way: chosen)."""
        got = self.by_hash.get(node_hash(nodeid))
        if got is None or UNKNOWN in got:
            return None
        out: set[str] = set()
        for h in got:
            crate_name, *segs = h.split("::")
            if crate_name != CRATE:
                continue  # std, a dependency or another workspace crate: no goh src file
            for n in range(len(segs), -1, -1):
                if (rel := crate.files.get(tuple(segs[:n]))) is not None:
                    out.add(rel)
                    break
        return out


def node_hash(nodeid: str) -> str:
    return hashlib.sha256(nodeid.encode()).hexdigest()[:20]  # lib/prof_store.py's


# --- the reach of a change ----------------------------------------------------------------------


def touched(crate: crate_reach.Crate, inputs: list[str]) -> set[str] | None:
    """Every goh src file whose compiled code a change to `inputs` can alter (module doc); None
    when one of them cannot be placed (outside goh's modules and embeds, or a macro)."""
    seen: set[str] = set()
    for path in inputs:
        files = crate.includes.get(path) or ({path} if path in crate.module else None)
        if files is None or any(MACRO.search(crate.code.get(f, "")) for f in files):
            return None
        if files & crate.shared:  # every run passes through it, derived code included
            return None
        seen |= files
    edges = dict(crate.edges)
    for rel in crate_reach.DISPATCH:  # the dispatch files: only the constants they read
        if rel in crate.module:
            edges[rel] = (set(), crate.refs(rel)[1])
    users: dict[str, set[str]] = {}
    consts: dict[str, set[str]] = {}
    for rel, (calls, reads) in edges.items():
        for dep in calls | reads:
            users.setdefault(dep, set()).add(rel)
        for dep in reads:
            consts.setdefault(dep, set()).add(rel)
    out = set(seen)
    for f in seen:
        out |= users.get(f, set())
    todo = list(out)
    while todo:
        for user in consts.get(todo.pop(), ()):
            if user not in out:
                out.add(user)
                todo.append(user)
    return out


# --- which nodes of a runner file to choose ----------------------------------------------------


def names(text: str) -> list[str] | None:
    """The test ids a file defines (`test_x`, `TestY::test_z`); None when its tests cannot be
    told from its source (a parse error, a nested test class)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            out.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, ast.ClassDef):
                    return None
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith(
                    "test"
                ):
                    out.append(f"{node.name}::{sub.name}")
    return out


def _base(rel: str, nodeid: str) -> str:
    return nodeid[len(rel) + 2 :].split("[", 1)[0]


def narrow(
    crate: crate_reach.Crate,
    reach: Reach,
    hit: set[str],
    runners: dict[str, str],
    tests: dict[str, str],
    known: dict[str, list[str]],
) -> tuple[dict[str, str], list[str]]:
    """({entry: why} for the runner files' tests the change reaches, [entries left out]).
    `hit`: touched(); `known`: {test file: its node ids} (the durations table)."""
    chosen: dict[str, str] = {}
    skipped: list[str] = []
    for rel, why in runners.items():
        defined = names(tests.get(rel, ""))
        nodes = known.get(rel, [])
        if not defined or any(_base(rel, n) not in defined for n in nodes):
            chosen[rel] = why
            continue
        picks: dict[str, str] = {}
        for name in defined:
            mine = [n for n in nodes if _base(rel, n) == name]
            reason = "no stored profile" if not mine else ""
            if mine and all(n in reach.free and node_hash(n) not in reach.by_hash for n in mine):
                skipped.append(f"{rel}::{name}")  # measured: it starts no goh
                continue
            for n in mine:
                files = reach.files(n, crate)
                if files is None:
                    reason = "no stored profile" if node_hash(n) not in reach.by_hash else (
                        "its profile names a symbol the reach cannot read")  # fmt: skip
                    break
                if files & hit:
                    reason = f"its profile reaches {min(files & hit)}"
                    break
            if reason:
                picks[name] = reason
            else:
                skipped.append(f"{rel}::{name}")
        if len(picks) == len(defined):
            chosen[rel] = why
        else:
            chosen.update((f"{rel}::{n}", f"{why}; {r}") for n, r in picks.items())
    return chosen, skipped
