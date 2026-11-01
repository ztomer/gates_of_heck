"""Which tracked Cargo.lock a repo's PUSH gate audits, and with which tool -- read from the gate.

The estate audit (`scripts/estate_audit.py`) told every session "the next push of <lock> is red",
and two locks disproved it on 2026-11-01. `Finance/zinc` gates with cargo-deny, whose `deny.toml`
ignores RUSTSEC-2024-0370 and whose `[graph] targets` leave glib out: its push is red for neither,
and the audit, running cargo-audit against no ignore list, said it was red for both. And
`monitor/fuzz/Cargo.lock` is its own workspace that no gate step of monitor audits: the claim
"next push is red" was false there too, and it hid the real gap, a lock nobody audits at push.

So the claim is DERIVED from the gate, never kept by hand. The gate is read from what goh knows a
push runs: the repo's `.gatesrc` `GOH_CI_STEPS` (local_ci.sh, run from the repo root),
`tools/gate.sh` (what goh's pre-push hook runs), `.pre-commit-config.yaml` and `.githooks/pre-push`,
and every tracked script or Makefile those name, transitively. In that text an audit is one of:

* `rust_gate.sh [repo [cargo_dir]]` and `rust_gate.sh --each-crate [repo]` -- cargo-audit, biting,
  on EVERY tracked lock under repo, each from the directory `lib/rust_locks.py` names (the nearest
  `.cargo/audit.toml` at or above it): the gate's own rule, imported, so it is not copied here.
  cargo_dir picks the crate that is linted, not the locks that are audited, so a variable there
  (`rust_gate.sh . "$crate"`) is readable. A `GOH_RUST_GROUPS` without `repo` audits nothing;
* `cargo audit [--file X]` -- cargo-audit on X, else on the Cargo.lock in its directory;
* `cargo deny [--manifest-path X] ... check [advisories ...]` -- cargo-deny, whose `deny.toml`
  (ignore list, graph targets, what it calls unmaintained) decides, so it is RUN, not imitated.

A command's directory is the repo root, or the literal `cd X` before it on the same line. A target
that goh cannot read -- a shell variable (`cd "$rust_core"`, `cargo audit -f "$crate/Cargo.lock"`) -- is
UNRESOLVED: a lock no resolved step covers is then `undetermined`, never called unaudited on a guess.
Pinned by tests/test_estate_gate_coverage.py.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rust_locks  # the gate's own rule for which locks it audits

ENTRIES = ("tools/gate.sh", ".pre-commit-config.yaml", ".githooks/pre-push")
SCRIPT_SUFFIXES = {".sh", ".bash", ".mk", ".yaml", ".yml"}
SCRIPT_NAMES = {"Makefile", "makefile", "GNUmakefile", "Justfile", "justfile"}
MAX_FILES = 64  # a gate that names more scripts than this is read up to it, and says so
SEPARATORS = set(";&|()<>")
WARNING_KINDS = ("unmaintained", "unsound", "yanked", "notice")


@dataclass(frozen=True)
class Audit:
    """One push-time audit of one lockfile: the tool, the directory it runs in, and its argv."""

    tool: str  # "cargo-audit" | "cargo-deny"
    cwd: str  # repo-relative directory, "." for the root
    lock: str  # repo-relative tracked Cargo.lock
    deny: tuple[str, ...] = ()  # cargo-audit: the --deny kinds on its command line
    args: tuple[str, ...] = ()  # cargo-deny: its argv after `deny`, `check` names replaced


@dataclass
class Coverage:
    audits: list[Audit] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # "<source>: <command>" goh cannot read
    sources: list[str] = field(default_factory=list)  # what was read, for the report

    def covered(self) -> set[str]:
        return {a.lock for a in self.audits}


def _tracked(repo: Path, env: dict) -> list[str]:
    r = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    return [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []


def ci_steps(repo: Path, env: dict) -> list[str]:
    """GOH_CI_STEPS as local_ci.sh reads it: the repo's `.gatesrc`, sourced, split on colons."""
    r = subprocess.run(
        [
            "bash",
            "-c",
            'unset GOH_CI_STEPS; . ./.gatesrc >/dev/null 2>&1; printf %s "${GOH_CI_STEPS:-}"',
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return [s for s in r.stdout.split(":") if s.strip()]


def _logical_lines(text: str) -> list[str]:
    """Non-comment lines, backslash continuations joined: a `( cd X \\ && cargo audit )` is one."""
    out, cur = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not cur and line.lstrip().startswith("#"):
            continue
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        out.append(cur + line)
        cur = ""
    if cur:
        out.append(cur)
    return out


def _words(line: str) -> list[str]:
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = "#"
        return list(lex)
    except ValueError:  # an unbalanced quote: read it as words, never refuse to read it
        return line.split()


def _commands(line: str) -> list[list[str]]:
    cmds, cur = [], []
    for w in _words(line):
        if w and set(w) <= SEPARATORS:
            if cur:
                cmds.append(cur)
            cur = []
        else:
            cur.append(w)
    if cur:
        cmds.append(cur)
    return cmds


def _unreadable(word: str) -> bool:
    return "$" in word or "`" in word


def _join(base: str, rel: str) -> str:
    """Repo-relative, normalised; a path that climbs out of the repo is unreadable (None-like '')."""
    p = PurePosixPath(rel) if rel.startswith("/") else PurePosixPath(base) / rel
    parts: list[str] = []
    for part in p.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return ""
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts) or "."


class _Reader:
    """Reads one repo's gate text into a Coverage."""

    def __init__(self, repo: Path, env: dict, tracked: list[str]) -> None:
        self.repo, self.env = repo, env
        self.locks = {p for p in tracked if p == "Cargo.lock" or p.endswith("/Cargo.lock")}
        self.cov = Coverage()

    def lock_for(self, d: str) -> str | None:
        """The tracked lock of the workspace `d` is in: the nearest one at or above it."""
        while True:
            cand = "Cargo.lock" if d == "." else f"{d}/Cargo.lock"
            if cand in self.locks:
                return cand
            if d == ".":
                return None
            d = str(PurePosixPath(d).parent) or "."

    def add(self, a: Audit) -> None:
        if a not in self.cov.audits:
            self.cov.audits.append(a)

    def line(self, source: str, line: str) -> None:
        cwd: str | None = "."
        for cmd in _commands(line):
            words = [w for w in cmd if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", w)]
            env = [w for w in cmd if w not in words]
            if words[:1] == ["cd"]:
                tgt = words[1] if len(words) > 1 else ""
                cwd = None if (not tgt or _unreadable(tgt) or cwd is None) else _join(cwd, tgt)
                continue
            self.command(source, cmd, words, env, cwd)

    def command(self, source: str, cmd: list[str], words: list[str], env: list[str], cwd) -> None:
        said = f"{source}: {' '.join(cmd)}"[:200]
        for i, w in enumerate(words):
            if w.endswith("rust_gate.sh"):
                groups = [e.split("=", 1)[1] for e in env if e.startswith("GOH_RUST_GROUPS=")]
                if groups and "repo" not in groups[-1].split(","):
                    return  # the advisory step is in the repo group (rust_gate.sh)
                self.rust_gate(said, words[i + 1 :], cwd)
                return
            if w == "cargo" and words[i + 1 : i + 2] == ["audit"]:
                self.cargo_audit(said, words[i + 2 :], cwd)
                return
            if w == "cargo" and words[i + 1 : i + 2] == ["deny"]:
                self.cargo_deny(said, words[i + 2 :], cwd)
                return

    def rust_gate(self, said: str, args: list[str], cwd) -> None:
        if args[:1] == ["--each-crate"]:
            args = args[1:]
            if "--list" in args:
                return  # a listing runs nothing
        repo_arg = args[0] if args else "."
        if cwd is None or _unreadable(repo_arg):
            self.cov.unresolved.append(said)
            return
        root = _join(cwd, repo_arg)
        if not root:
            self.cov.unresolved.append(said)
            return
        pre = "" if root == "." else root + "/"
        under = [lk[len(pre) :] for lk in sorted(self.locks) if lk.startswith(pre)]
        base = self.repo / root
        for cfg, lock in rust_locks.plan(under, rust_locks.on_disk(base)):
            self.add(Audit("cargo-audit", _join(root, cfg), _join(root, lock), ("warnings",)))

    def cargo_audit(self, said: str, args: list[str], cwd) -> None:
        if args[:1] and args[0] in ("fix", "bin"):
            return
        file, deny, it = None, [], iter(args)
        for w in it:
            if w in ("-f", "--file"):
                file = next(it, "")
            elif w.startswith("--file="):
                file = w.split("=", 1)[1]
            elif w in ("-D", "--deny"):
                deny.append(next(it, ""))
            elif w.startswith("--deny="):
                deny.append(w.split("=", 1)[1])
        if cwd is None or (file is not None and _unreadable(file)):
            self.cov.unresolved.append(said)
            return
        lock = _join(cwd, file or "Cargo.lock")
        if lock in self.locks:
            self.add(Audit("cargo-audit", cwd, lock, tuple(sorted(set(deny)))))

    def cargo_deny(self, said: str, args: list[str], cwd) -> None:
        if "check" not in args:
            return
        k = args.index("check")
        pre, post = args[:k], args[k + 1 :]
        names = []
        while post and not post[0].startswith("-"):
            names.append(post.pop(0))
        if names and not {"advisories", "advisory"} & set(names):
            return  # a check that names its kinds and leaves advisories out audits nothing
        manifest, keep, it = "Cargo.toml", [], iter(pre)
        for w in it:
            if w in ("-f", "--format", "-c", "--color", "-L", "--log-level"):
                next(it, None)
                continue
            if w.startswith(("--format=", "--color=", "--log-level=")):
                continue
            if w == "--manifest-path":
                manifest = next(it, "")
                keep += [w, manifest]
                continue
            if w.startswith("--manifest-path="):
                manifest = w.split("=", 1)[1]
            keep.append(w)
        if cwd is None or any(_unreadable(w) for w in [*keep, *post]):
            self.cov.unresolved.append(said)
            return
        lock = self.lock_for(str(PurePosixPath(_join(cwd, manifest)).parent) or ".")
        if lock:
            self.add(Audit("cargo-deny", cwd, lock, (), (*keep, "check", "advisories", *post)))


def _named(text: str, scripts: list[str]) -> list[str]:
    hits = [p for p in scripts if re.search(r"(?<![\w.-])" + re.escape(p) + r"(?![\w.-])", text)]
    # A script named through a directory variable (`"$(dirname "$0")/run_gates.sh"`,
    # `. "$tools_dir/gates_toolchains.sh"`): `/<name>`, when exactly one tracked script has it.
    names: dict[str, list[str]] = {}
    for p in scripts:
        names.setdefault(PurePosixPath(p).name, []).append(p)
    hits += [
        ps[0]
        for n, ps in names.items()
        if len(ps) == 1 and re.search(r"/" + re.escape(n) + r"(?![\w.-])", text)
    ]
    if re.search(r"(?<![\w-])(make|\$\(MAKE\)|\$\{MAKE\})(?![\w-])", text):
        hits += [p for p in scripts if p in ("Makefile", "makefile", "GNUmakefile")]
    return hits


def coverage(repo: Path, env: dict) -> Coverage:
    """What the repo's push gate audits, read from the gate text (module docstring)."""
    tracked = _tracked(repo, env)
    rd = _Reader(repo, env, tracked)
    # rust_gate.sh is MODELLED (its argv says what it audits), never read: its own body audits
    # "$lockfile", which is exactly the variable this reader cannot resolve.
    scripts = [
        p
        for p in tracked
        if (PurePosixPath(p).suffix in SCRIPT_SUFFIXES or PurePosixPath(p).name in SCRIPT_NAMES)
        and PurePosixPath(p).name not in ("rust_gate.sh", "rust_each_crate.sh")
    ]
    texts: list[tuple[str, str]] = []
    steps = ci_steps(repo, env)
    if steps:
        rd.cov.sources.append(".gatesrc GOH_CI_STEPS")
        texts += [(".gatesrc GOH_CI_STEPS", s) for s in steps]
    queue = [p for p in ENTRIES if p in tracked]
    seen: set[str] = set()
    for _, s in texts:
        queue += _named(s, scripts)
    while queue and len(seen) < MAX_FILES:
        p = queue.pop(0)
        if p in seen:
            continue
        seen.add(p)
        try:
            body = (repo / p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rd.cov.sources.append(p)
        lines = _logical_lines(body)
        texts += [(p, line) for line in lines]
        queue += _named("\n".join(lines), scripts)
    if queue and len(seen) >= MAX_FILES:
        rd.cov.unresolved.append(f"the gate names more than {MAX_FILES} scripts; read to there")
    for source, line in texts:
        rd.line(source, line)
    return rd.cov


def denied_kinds(repo: Path, a: Audit) -> set[str]:
    """The warning kinds a cargo-audit run fails on: its --deny flags and its `.cargo/audit.toml`
    `[output] deny`, read from the directory it runs in (the only place cargo-audit reads it)."""
    kinds = set(a.deny)
    cfg = repo / a.cwd / ".cargo" / "audit.toml"
    try:
        kinds |= set(tomllib.loads(cfg.read_text()).get("output", {}).get("deny", []))
    except (OSError, tomllib.TOMLDecodeError):
        pass
    if "warnings" in kinds:
        kinds |= set(WARNING_KINDS)
    return kinds
