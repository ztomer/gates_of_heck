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
    reuse.py lookup-gate --repo R --sha SHA [--how full]
                                                     # exit 0, the record printed: a green verdict
                                                     # on SHA's TREE under today's gate identity;
                                                     # exit 1, the reason on stderr: a miss
    reuse.py shadow-log                              # the sampled reused-vs-fresh comparisons

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

THE SHADOW CHECK. A reused verdict is a claim that a fresh run would agree. `shadow_due` picks a
deterministic sample of keys (GOH_REUSE_SHADOW=RATE, 0..1, default 0: off), and a caller that
re-ran the work anyway hands both answers to `shadow_report`, which appends one JSON line to
shadow.jsonl in the store; a disagreement is the finding that the key is missing an input.

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
import contextlib
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
LEGACY = ("goh-landed",)


@dataclass(frozen=True)
class ReusePath:
    name: str
    fields: tuple[str, ...]
    ttl_s: int
    doc: str


PATHS: dict[str, ReusePath] = {}


def register(name: str, fields: tuple[str, ...], ttl_s: int, doc: str) -> ReusePath:
    """Name a reuse path and the fields its key is made of. Once per name, at least one field."""
    if name in PATHS:
        raise ValueError(f"reuse path {name!r} is registered twice")
    if not fields or len(set(fields)) != len(fields):
        raise ValueError(f"reuse path {name!r}: its key fields must be distinct and non-empty")
    PATHS[name] = ReusePath(name, tuple(fields), ttl_s, doc)
    return PATHS[name]


GATE = register(
    "gate",
    ("repo", "tree", "identity"),
    DAY_S,
    "a green gate over a commit's tree: the push gate writes; ship verdict, the release rule read",
)


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


# The sampled shadow check (header): the future reuse paths' audit (GOH.REUSEAUDIT).
def shadow_due(k: str) -> bool:
    try:
        rate = float(os.environ.get("GOH_REUSE_SHADOW", "0") or 0)
    except ValueError:
        rate = 0.0
    return rate > 0 and int(k[:8], 16) < rate * 0x100000000


def shadow_report(path: str | ReusePath, fields: dict[str, str], reused: str | None,
                  fresh: str) -> bool:  # fmt: skip
    """Append the reused and the fresh verdict for one key; True when they agree."""
    p = _path(path)
    agree = reused == fresh
    line = {"path": p.name, "key": key(p, fields), "fields": fields, "reused": reused,
            "fresh": fresh, "agree": agree, "epoch": now()}  # fmt: skip
    root().mkdir(parents=True, exist_ok=True)
    with (root() / "shadow.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")
    return agree


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
    return out


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
    sub.add_parser("shadow-log")
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
    with contextlib.suppress(FileNotFoundError):
        print((root() / "shadow.jsonl").read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
