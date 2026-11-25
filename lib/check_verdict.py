"""lib/check_verdict.py -- a land check's green verdict, kept and taken once (GOH.CHECKVERDICT).

WHY. A lane runs `goh land --check` until it is green, and `goh land --enqueue` then runs the same
steps and guard tests on the same tree: 362 enqueue checks in 7 days, p50 22.4 s, 2.88 h in all
(33-170 s each at load 30 on 2026-11-25). The check's own green was dropped.

WHAT IS KEPT. The steps and guard tests a wholly green run ran (never the judges of `--check`: no
enqueue asks for them), under the `check` path of the one store (lib/reuse.py), keyed on:
  repo      the common git dir (a lane and its land slot share it)
  tree      HEAD's tree in a CLEAN checkout, the same at the end of the run as at its start, with no
            tracked file touched meanwhile (its ctime): a lane that edits its tree during a check
            and puts the edit back (GOH.LANDSNAPSHOT's first incident) keeps nothing
  base      main's tip and HEAD's fork from it: `shape-diff --since main` reads both, and main
            moving is what a check cannot answer for
  identity  the gate code (lib/gate_identity.py `proven`: the goh this check runs from and GOH_DIR's
            by content, PYTHONPATH by content, every tool's bytes)
  env       every variable of the jobs' environment a gate or a test reads by prefix (ENV), less the
            verdict-free keys (gates/verdict_free_keys.txt), by value
A run with a waiver (lib/land_waive.py: main's reds) keeps nothing and takes nothing: its green is
not the tree's alone. Nor does a checkout with no main.

WHAT IS TAKEN. At enqueue alone, whole or not at all: one step or guard test the kept run did not
run (a `--no-pre` enqueue's lint, a guard the suite's measurement lets in now) and every one runs,
so nothing is green that nobody judged. `goh land --check` itself always judges. The commit gate's
stamps and the changelog fragments read commits, not the tree, and are read fresh at every enqueue
(lib/land_check_cli.py `tip`). Audited like every reuse (lib/reuse_audit.py): one take in ten runs
the guards anyway and compares. Two hours' life: a guard may read the estate, which no key holds;
the land gate runs every one of these again on the tree it lands, so this verdict is never the last
word.

`goh land --check`'s green verdict (GOH.CHECKVERDICT): the steps and guard tests a green run judged,
kept in `lib/reuse.py`'s `check` path by tree, base, gate identity and environment, and taken by
`goh land --enqueue` in their place when it judged every one. Invoked by `lib/land_check.py`.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [p for p in (str(ROOT), str(ROOT / "lib")) if p not in sys.path]
import cov_env  # noqa: E402 -- lib/, on the path from the line above
import gate_identity  # noqa: E402
import reuse  # noqa: E402
import reuse_audit  # noqa: E402

PATH = "check"
ENV = ("GOH_", "GATES_OF_HECK_", "PYTEST_", "RUST", "CARGO")  # what a gate or a test reads
ELSEWHERE = {"GOH_DIR", "GOH_PY"}  # keyed by content, and by the interpreter it resolves to


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def env_lines(env: dict[str, str]) -> list[str]:
    """The jobs' environment as a verdict reads it, sorted (module doc, `env`)."""
    free = cov_env.free_keys(ROOT) | ELSEWHERE
    kept = [f"{k}={v}" for k, v in env.items() if k.startswith(ENV) and k not in free]
    return [*sorted(kept), f"GOH_PY -> {os.path.realpath(env.get('GOH_PY', ''))}"]


def key(root: Path, env: dict[str, str]) -> dict[str, str] | None:
    """The fields of a check of ROOT's committed tree under ENV (module doc); None: it has none."""
    tree = gate_identity.clean_tree(root)
    main, repo = reuse._git(root, "rev-parse", "--verify", "--quiet", "main"), reuse.repo_id(root)
    fork = reuse._git(root, "merge-base", "HEAD", "main") if main else ""
    if not (tree and fork and repo):
        return None
    roots = list(dict.fromkeys([ROOT, Path(env.get("GOH_DIR") or ROOT).resolve()]))
    code = gate_identity.proven(roots, env.get("PYTHONPATH"), root)
    return {"repo": repo, "tree": tree, "base": f"{main} {fork}", "identity": _digest(code),
            "env": _digest(env_lines(env))}  # fmt: skip


def touched(root: Path, since_ns: int) -> str:
    """A tracked file of ROOT written (or replaced) at or after SINCE_NS; '' when none was."""
    for rel in reuse._git(root, "ls-files", "-z").split("\0"):
        try:
            if rel and os.lstat(root / rel).st_ctime_ns >= since_ns:
                return rel
        except OSError:
            return rel
    return ""


class Hold:
    """One run's side of the verdict: what it may take, and what its green keeps."""

    def __init__(self, root: Path, env: dict[str, str], door: str, waived: bool) -> None:
        self.root, self.env, self.door = root, env, door
        self.since, self.t0 = time.time_ns(), time.monotonic()
        self.key = None if waived else key(root, env)
        self.shadow = False

    def take(self, steps: list[str], nodes: list[str], say: Callable[[str], None]) -> bool:
        """Whether a kept green verdict of this key judged every one of STEPS and NODES."""
        rec = reuse.lookup(PATH, self.key)[0] if self.key else None
        if rec is None or self.key is None or rec.get("verdict") != "green":
            return False
        if set(steps) - set(rec.get("steps") or ()) or set(nodes) - set(rec.get("nodes") or ()):
            return False
        use = reuse_audit.use("check", self.key)
        if use != "reuse":
            self.shadow = use == "shadow"
            why = "the audit's shadow, one take in ten" if self.shadow else f"the path is {use}"
            say(f"check: a kept verdict holds and the guards run anyway ({why})")
            return False
        say(f"check: {len(steps)} steps and {len(nodes)} guard tests are green on this tree, base "
            f"and gate code ({rec.get('by')}, {(reuse.now() - int(rec['epoch'])) // 60} min ago, "
            f"{rec.get('took_s')} s) -- not run again")  # fmt: skip
        return True

    def close(self, steps: list[str], nodes: list[str], refused: list[str], blind: bool,
              again: Callable[[], bool]) -> None:  # fmt: skip
        """The run's end: a shadow compared (AGAIN: the guards run once more, green or not), and a
        green of STEPS and NODES kept when the checkout was this one throughout (module doc)."""
        if self.key is None:
            return
        if self.shadow and not blind:
            fresh = "red" if refused else "green"
            reuse_audit.compare("check", self.key, "green", fresh,
                                lambda: (self.kept(), "green" if again() else "red"))  # fmt: skip
        same = key(self.root, self.env) == self.key and not touched(self.root, self.since)
        if not refused and same:
            reuse.record(PATH, self.key, "green", by=f"goh land --{self.door}",
                         extra={"steps": steps, "nodes": nodes,
                                "took_s": round(time.monotonic() - self.t0, 1)})  # fmt: skip
            reuse.prune(PATH)

    def kept(self) -> str:
        """The store's verdict of this key, now."""
        rec = reuse.lookup(PATH, self.key)[0] if self.key else None
        return str(rec.get("verdict")) if rec else "absent"
