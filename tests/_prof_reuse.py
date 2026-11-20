"""A binary test's stored profile stands for its run at the ship (GOH.COVPROFILEREUSE).

WHY. Since GOH.COVOFFLAND the ship's instrumented run is the only coverage measurement, and it ran
every binary-running test again: v0.140.1's suite step was 918.9 s, 881 s of it the instrumented
pytest, while 9 of the 10 ships before it changed no Rust input. The profile store held keys and
the ship read none, for three reasons: the ship's own-tree plan (tests/_same_tree.py) takes no
stored profile; the store's key (lib/prof_store.py) names the TREE, and every ship is on a new
one; and the instrumented goh's bytes moved with every release (lib/goh_version.sh `--coverage`
named the release HEAD sits past: four ships, four binaries, one set of Rust inputs).

THE KEY, per node: the instrumented binaries' content hash (prof_store.bin_id), the node id, the
CONTENT of what its test file reads -- the file, everything tools/affected_tests.py's graph
reaches from it (lib/test_closure.py `reach`), the base pytest loads before every test, and the
files its declared reads match (lib/declared_reads.py) -- the tools the suite ran under with the
llvm-profdata that merged the profile, and the GOH_COV_* settings the coverage judge fingerprints
(gates/_rust_proven.sh, the same exclusions). A tree reader and a test with undeclared reads
(gates/undeclared_reads.txt) are never reused: what they read is the tree. A key matched exactly
reuses; any other difference runs the test.

THE SHIP, its @tree run only (`take`, called by tests/_same_tree.py): each node whose profile the
store holds under this exact key is deselected and its profile linked where the report reads it,
so the floor is judged on the merged result. Such a node counts as judged on this tree (the owner's
term: a profile of the same bytes and inputs IS its measurement), so the record stays the full
instrumented one the release kit asks for. At session end (`finish`, from tests/_prof_record.py)
every binary test that passed fresh is stored under its key, and the counts -- reused and fresh,
per binary -- go to GOH_PROF_REUSE_OUT, which tools/ship.py folds into the ship record.

THE SELF-CHECK. Each ship's reuse is one use of the audited `profreuse` path (lib/reuse.py): one
ship in ten is a SHADOW, whose reusable nodes run anyway; at its end the counters their stored
profiles cover are compared with the ones their fresh runs cover. A difference is read again and,
repeated, turns the path off and files a fault (lib/reuse_audit.py), so every later ship measures.
Hosted release CI measures afresh regardless. GOH_PROFCARRY_TESTS=off stops it, as does `profcarry`
turned off. Pinned by tests/test_prof_reuse.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

PATH = "profreuse"
OUT = "GOH_PROF_REUSE_OUT"
_OUT = os.environ.pop(OUT, "")  # taken as the plugin loads: no session a test starts writes it
INDEX = "index.json"
_state: dict = {}


def _lv():
    import _land_verdicts  # tests/: loaded before this module is used, never at its import

    return _land_verdicts


def _pr():
    import _prof_record  # tests/

    return _prof_record


def _ps():
    return _lv()._lib("prof_store")


def tools() -> str:
    """The tools a profile was measured with: the suite's, and the llvm-profdata that merged it."""
    gi = _lv()._lib("gate_identity")
    prof = _pr()._profdata()
    rows = [gi.suite_tools(), f"llvm-profdata {gi.fingerprint(prof) if prof else 'absent'}"]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def config(env=None) -> str:
    """The GOH_COV_* settings, as gates/_rust_proven.sh keys them (where, how big, how wide: no)."""
    env = os.environ if env is None else env
    rows = sorted(f"{k}={v}" for k, v in env.items() if k.startswith("GOH_COV_")
                  and k != "GOH_COV_RUST_JOBS" and k != "GOH_COV_CACHE"
                  and not k.startswith("GOH_COV_CACHE_"))  # fmt: skip
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def fields() -> dict[str, str] | None:
    """The key's run-wide part; None when nothing is stored or reused (not instrumented, no store,
    the kill switch, no binary)."""
    lv = _lv()
    if not (lv.instrumented() and lv._store() and _ps().enabled()):
        return None
    binary = _ps().bin_id(os.environ["GOH_COVERAGE_BIN_DIR"])
    if not binary:
        return None
    with lv._in_store() as (reuse, _):
        repo = reuse.repo_id(lv.REPO_ROOT)
    return {"repo": repo, "bin": binary, "tools": tools(), "config": config()}


def key(f: dict[str, str]) -> str:
    return "ship-" + hashlib.sha256(json.dumps(f, sort_keys=True).encode()).hexdigest()


def slot(node: str, digest: str) -> str:
    """The store's name for NODE's profile when its test file reads DIGEST."""
    return f"{node}\0{digest}"


def digests(root: Path, files) -> dict[str, str | None]:
    """{test file: the content digest of what it reads (module doc), or None: never reused}."""
    lv = _lv()
    sys.path.insert(0, str(lv.REPO_ROOT / "tools"))  # this checkout's code, ROOT's files
    try:
        import affected_tests as at
    finally:
        sys.path.pop(0)
    dr, sc = lv._lib("declared_reads"), lv._lib("source_closure")
    graph, walkers = at.Closure(root), at.tree_readers(root)
    memo: dict[str, str] = {}
    listed: list[str] = []

    def h(rel: str) -> str:
        if rel not in memo:
            try:
                memo[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
            except OSError:
                memo[rel] = "absent"
        return memo[rel]

    base = "".join(f"{p} {h(p)}\n" for p in sorted(graph.base()))
    out: dict[str, str | None] = {}
    for rel in sorted(set(files)):
        text = graph.texts.get(rel)
        if text is None or rel in walkers or rel in graph.undeclared:
            out[rel] = None
            continue
        reached = set(graph.reach(rel)[0])
        pats = [*dr.reads(text, root), *(p for p, _ in dr.through(graph, rel, root))]
        if pats and not listed:
            listed = sc.tree_files(root)
        reached |= {f for f in listed for p in pats if dr.matches(p, f)}
        rows = [base, f"{rel} {h(rel)}", *(f"{p} {h(p)}" for p in sorted(reached - {rel}))]
        out[rel] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return out


def _digests(files) -> dict[str, str | None]:
    """`digests` of this checkout, each file read once a run."""
    have = _state.setdefault("digests", {})
    if missing := set(files) - set(have):
        have.update(digests(_lv().REPO_ROOT, missing))
    return {f: have[f] for f in files}


def _index_file(k: str) -> Path:
    return _ps()._dir(k) / INDEX


def _index(k: str) -> dict[str, str]:
    try:
        got = json.loads(_index_file(k).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(n): str(d) for n, d in got.items()} if isinstance(got, dict) else {}


def _out() -> Path:
    return Path(os.environ[_pr().PROFILE]).parent


def take(plan: dict, coverage: bool) -> None:
    """Add to the ship's @tree PLAN each binary-running node whose profile the store holds under
    its exact key: deselected, its profile where the report reads it; one audited use a run."""
    f = fields() if coverage else None
    if f is None:
        return
    lv, ps, k = _lv(), _ps(), key(f)
    _state.update(fields=f, key=k, decision="none", reused=[])
    lines = plan["lines"]
    if not lv._carry_audited_on():
        lines.append("profile reuse: none: the audit turned `profcarry` off")
        return
    with lv._in_store() as (_, audit):
        index = _index(k)
        now = _digests({n.split("::")[0] for n in index})
        held = {}
        for n in sorted(set(index) - set(plan.get("skip", ()))):
            d = now.get(n.split("::")[0])
            got = ps.get(k, slot(n, d)) if d else None
            if got is not None:
                held[n] = got
        decision = audit.use("profreuse", f) if held else "none"
    _state["decision"] = decision
    how = f"binary {f['bin'][:12]}, {len(index)} profiles stored"
    if decision == "none":
        lines.append(f"profile reuse: no stored profile matches its key ({how})")
    elif decision == "off":
        lines.append(
            "profile reuse: none: the audit turned `profreuse` off (reuse_audit.py report)"
        )
    elif decision == "shadow":
        plan["profcheck"] = _state["plan_check"] = {n: str(p) for n, p in held.items()}
        lines.append(f"profile reuse: {len(held)} matched RUN anyway: the audit's shadow ({how})")
    else:
        with lv._in_store():
            for n, p in held.items():
                ps.place(p, _out() / f"t-reused-{ps.node_hash(n)}.profraw")
        plan.setdefault("skipped", {}).update(dict.fromkeys(held, True))
        for name in ("skip", "judged", "binprof"):
            plan.setdefault(name, set()).update(held)
        _state["reused"] = sorted(held)
        lines.append(f"profile reuse: {len(held)} binary tests deselected, their stored profiles "
                     f"in the report ({how})")  # fmt: skip


def _fresh(nodes) -> dict[str, Path]:
    """{node: the one merged profile its fresh run left} (tests/_prof_record.py `_merged`)."""
    out, ps = {}, _ps()
    for n in nodes:
        got = [p for p in _out().glob(f"t-{ps.node_hash(n)}-*.profraw") if "-raw" not in p.name]
        if len(got) == 1:
            out[n] = got[0]
    return out


def covered(paths: list[Path]) -> set[tuple[str, str, int]]:
    """(function, its hash, counter index) of every counter the profiles PATHS count above 0."""
    tool = _pr()._profdata()
    if not tool or not paths:
        return set()
    r = subprocess.run([tool, "merge", "--text", "-o", "-", *map(str, paths)], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    if r.returncode:
        raise OSError(f"llvm-profdata merge --text: {r.stderr.strip()[:200]}")
    got: set[tuple[str, str, int]] = set()
    for block in r.stdout.split("\n\n"):
        rows = [ln for ln in block.splitlines() if ln and ln[0] not in "#:"]
        if len(rows) < 3 or not rows[2].isdigit():
            continue
        name, fhash, n = rows[0], rows[1], int(rows[2])
        got |= {
            (name, fhash, i) for i, v in enumerate(rows[3 : 3 + n]) if v.strip() not in ("0", "")
        }
    return got


def _said(cov: set) -> str:
    digest = hashlib.sha256(repr(sorted(cov)).encode()).hexdigest()[:16]
    return f"{len(cov)} counters covered ({digest})"


def check(f: dict[str, str], stored: list[Path], fresh: list[Path]) -> tuple[bool, str]:
    """The shadow's compare: what the stored profiles cover against what the fresh runs did."""

    def sides() -> tuple[str, str]:
        return _said(covered(stored)), _said(covered(fresh))

    reused, now = sides()
    with _lv()._in_store() as (_, audit):
        same = audit.compare(PATH, f, reused, now, sides)
    if same:
        return True, f"profile reuse: the shadow's {len(fresh)} fresh runs agree ({now})"
    return False, (f"profile reuse: DISAGREE -- the stored profiles cover {reused}, the fresh runs "
                   f"{now}; profreuse is off")  # fmt: skip


def finish(ran: set[str]) -> list[str]:
    """At the controller's end of a ship run: each binary test that passed fresh kept under its
    key, the shadow compared, the counts said and written. ("" lines outside a ship run.)"""
    f = _state.get("fields")
    if not f:
        return []
    lv, ps, k = _lv(), _ps(), _state["key"]
    fresh = _fresh(ran)
    now = _digests({n.split("::")[0] for n in fresh})
    lines = []
    # The shadow first: storing the fresh runs replaces the stored profiles it compares with.
    pairs = {n: (Path(s), fresh[n]) for n, s in _state.get("plan_check", {}).items() if n in fresh}
    if pairs:
        try:
            lines.append(
                check(f, [s for s, _ in pairs.values()], [p for _, p in pairs.values()])[1]
            )
        except OSError as e:  # the compare never fails the run: no sample made, said
            lines.append(f"profile reuse: the shadow compared nothing ({e})")
    with lv._in_store():
        index = _index(k)
        for n, p in sorted(fresh.items()):
            if d := now.get(n.split("::")[0]):
                ps.put(k, slot(n, d), p)
                index[n] = d
        if fresh:
            tmp = _index_file(k).with_name(f".{INDEX}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
            os.replace(tmp, _index_file(k))
    counts = {"bin": f["bin"], "reused": len(_state.get("reused", [])), "fresh": len(ran),
              "audit": _state.get("decision", "none")}  # fmt: skip
    lines.append(f"profile reuse: binary {f['bin'][:12]}: {counts['reused']} reused, "
                 f"{counts['fresh']} fresh ({counts['audit']})")  # fmt: skip
    if _OUT:
        Path(_OUT).write_text(json.dumps({"binaries": [counts]}, sort_keys=True) + "\n")
    return lines
