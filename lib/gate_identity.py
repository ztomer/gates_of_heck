"""The identity of a gate verdict: what besides the tree decided it (GOH.VERDICTKEY).

A verdict recorded by tree alone outlived what made it. The CIP.CACHING survey reproduced two:
gates/proven.sh keyed a step on rustc, cargo, python and swift only, so with another shellcheck
first on PATH it answered "proven ... skipped"; lib/loaded_verdict.py keyed goh-loaded/<tree>.json
on the tree only, so a run green under `--cmd true` answered "green already" for `--cmd false`.
And the release push rule (lib/release_gate.py) trusted a parent's goh-landed line, which a land
under a hand-set GOH_LAND_GATE=true wrote exactly as a full gate's.

    gate_identity.py tools                    # one line per tool a gate step may invoke
    gate_identity.py key --mode MODE [--root GOH]   # sha256 of MODE, the gates' content, the tools
    gate_identity.py proven [--root GOH...] [--repo R --pythonpath PP]
                                              # gates/_proven.sh's run-constant identity: each root
                                              # once by content, PYTHONPATH by content, the tools
    gate_identity.py keyed --root GOH...      # the paths that content covers, one per line
    gate_identity.py rust --repo R [--root GOH] [--external CMD] [--pythonpath PP]
                                              # the Rust coverage verdict's inputs, as lines

The three parts:
  * MODE -- the command or gate mode the verdict is of (`full` for a land's own push gate, else
    the GOH_LAND_GATE string);
  * the CONTENT of the gates that ran: every file under gates/ checks/ lib/ tools/ tui/ of the
    gates root, and the goh binary's build inputs (crates/, Cargo.toml, Cargo.lock,
    rust-toolchain.toml: the trees its source stamp names, gates/_goh_bin.sh), by path, kind and
    bytes -- ONE formula, so a checkout and its commit's head export key alike
    (GOH.VERDICTKEYDRIFT: two formulas put one tree's loaded verdict under two keys and the ship
    missed it); bytecode skipped. The gates run bin/goh, the build of those inputs: without them
    a change to goh's Rust alone left every key unchanged (GOH.PROVENIDENTITY). Where the binary
    is NOT the content's build it is keyed apart: an explicit GOH_BIN by its bytes (a tool line),
    and a checkout whose build inputs differ from HEAD without GOH_LIVE (it runs HEAD's build) by
    HEAD's trees. The version string a build reports (`git describe`, the release tag) is not
    keyed: the binary store already holds one build per source (gates/_goh_bin.sh), and a tag
    push reuses its commit's proofs;
  * the TOOLS: for each of rustc, cargo, python (the house interpreter), swift, shellcheck, ruff,
    cargo-llvm-cov and xcodebuild, the PATH hit, the binary it resolves to and that binary's size
    and mtime (no spawn: an upgrade rewrites the binary); a rustup proxy also by the toolchain
    binary it resolves to (`rustup which`, not the RUSTUP_TOOLCHAIN spelling); plus the selected
    Xcode. An absent tool is its own token, so installing one changes the identity.
THE RUST COVERAGE VERDICT (GOH.RUSTCOVSKIP, `rust`). A goh land spent 600-900 s measuring Rust
coverage when its diff touched no Rust (GOH.XEXITDETAIL: 896.62 s of a 21:37 land, for Python and
shell). The measurement's own inputs are narrower than the gates: the repo's Rust build inputs
(lib/goh_version.sh's stamp formula, embedded files included: build_inputs) and the Rust gate's
coverage scripts (rust_gate_scripts: gates/rust_gate.sh, gates/_rust_proven.sh, and every file
gates/coverage_gate.sh sources, imports or runs, read from the sources by lib/source_closure.py,
never a hand list), the files the external suite's command runs the binary THROUGH (runner_files:
goh's tools/pytest.sh and its closure, tests/ never: the owner's rule), PYTHONPATH with each gates
root on it by content (pythonpath: goh's land puts a new head export path on it every commit,
GOH.RUSTCOVKEYSCOPE), plus the tools. gates/_rust_proven.sh keys the coverage record on these lines;
on a hit the instrumented build and report are skipped and the tests still run, plain. A workspace
member or path dependency outside the keyed inputs has no such identity (exit 1): a change there
could not move the key, so the coverage runs.
Used by gates/_proven.sh's step identity (its goh roots, GOH.PROVENIDENTITY: it kept a formula of
its own, HEAD sha and diff for a checkout, the PATH for an export, so a commit's checkout and its
head export never reused each other's proven steps; and PYTHONPATH, by the one `pythonpath` formula
the coverage key uses, scope gate_files: by path, a land's head export made every key new,
GOH.PROVENPYPATH), checks/_estate_cache.py (GOH_DIR by `identity`, not its path), and
tools/land.sh's goh-landed line (read by the push rule); the loaded run's record key went with
that run (GOH.LOADEDDROP). Pinned by
tests/test_verdict_key.py and tests/test_proven_goh_content.py.
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
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

TOOLS = ("rustc", "cargo", "python3", "swift", "shellcheck", "ruff", "cargo-llvm-cov", "xcodebuild")
# The binary's build roots; its full inputs add each file a crate embeds (build_inputs).
BUILD = ("crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml")
PARTS = ("gates", "checks", "lib", "tools", "tui", *BUILD)
GOH_ROOT = Path(__file__).resolve().parent.parent


def _fingerprint(path: str) -> str:
    real = os.path.realpath(path)
    try:
        st = os.stat(real)
    except OSError:
        return f"{path} -> {real} unreadable"
    return f"{path} -> {real} {st.st_size} {st.st_mtime_ns}"


def tools() -> list[str]:
    """One line per tool: where PATH finds it and which bytes that is, or `absent`. A rustup proxy
    is keyed by the binary it RESOLVES to (`rustup which`, which reads RUSTUP_TOOLCHAIN and any
    rust-toolchain file the way the gate's own call will), never by the spelling of the override:
    `stable`, `stable-aarch64-apple-darwin` and no override name one rustc, so one identity
    (GOH.VERDICTKEYDRIFT)."""
    out = []
    for name in TOOLS:
        # The interpreter running this is the house one every caller names; keyed by its bytes.
        hit = os.path.realpath(sys.executable) if name == "python3" else shutil.which(name)
        line = f"tool {name} {_fingerprint(hit) if hit else 'absent'}"
        proxy = os.path.realpath(hit) if hit else ""
        if proxy and os.path.basename(proxy) == "rustup":  # a proxy: key what it runs
            got = _run([proxy, "which", name])
            line += f" runs {_fingerprint(got) if os.path.isabs(got) else 'unresolved ' + got}"
        out.append(line)
    # An explicit GOH_BIN is the binary every gate runs (gates/_goh_bin.sh), whatever the content.
    named = os.environ.get("GOH_BIN", "")
    out.append(f"tool goh-bin {_fingerprint(named) if named else 'unset'}")
    xsel = shutil.which("xcode-select")
    out.append(f"xcode {os.environ.get('DEVELOPER_DIR') or (_run([xsel, '-p']) if xsel else '')}")
    return out


def _run(argv: list[str]) -> str:
    r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)
    return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else ""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def _files(root: Path, parts: tuple[str, ...] | list[str] = PARTS) -> list[str]:
    """The gates' files under root, repo-relative: a checkout's tracked and untracked-unignored
    ones that exist, or every file of an export (a `git archive` of a commit: its tracked files)."""
    if (root / ".git").exists():
        listed = _git(root, "ls-files", "-z", "-co", "--exclude-standard", "--", *parts)
        if listed.returncode == 0:
            rels = {r for r in listed.stdout.decode().split("\0") if r}
            return sorted(r for r in rels if os.path.lexists(root / r))
    rels = []
    for part in parts:
        top = root / part
        if top.is_file() or top.is_symlink():
            rels.append(part)
        elif top.is_dir():
            rels.extend(
                str(p.relative_to(root)) for p in top.rglob("*") if p.is_file() or p.is_symlink()
            )
    return sorted(rels)


def content(root: Path, parts: tuple[str, ...] | list[str] = PARTS) -> str:
    """sha256 of the gates' bytes under root, ONE formula wherever they are read: each file's
    repo-relative path, kind (link, executable, file) and bytes. A checkout and the head export of
    its commit hold the same bytes and key alike; two formulas (`ls-files -s` plus the diff here,
    path and bytes there) put one commit's verdict under two keys (GOH.VERDICTKEYDRIFT)."""
    h = hashlib.sha256()
    for rel in _files(root, parts):
        if "__pycache__" in rel.split("/"):
            continue
        p = root / rel
        if p.is_symlink():
            kind, data = b"l", os.readlink(p).encode()
        else:
            kind, data = (b"x" if p.stat().st_mode & 0o111 else b"f"), p.read_bytes()
        h.update(rel.encode() + b"\0" + kind + b"\0" + str(len(data)).encode() + b"\0" + data)
    return h.hexdigest()


def build_inputs(root: Path) -> list[str]:
    """The paths the stamp names at root's HEAD, from the one formula (lib/goh_version.sh --inputs,
    GOH.STAMPINPUTS): the build roots, then each file a crate embeds -- an uncommitted edit to
    lib/busyloop_shapes.json is a binary that is not the content's build too."""
    script = GOH_ROOT / "lib" / "goh_version.sh"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run(["bash", str(script), "--inputs", str(root), "HEAD"], capture_output=True,
                         text=True, env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    listed = out.stdout.split() if out.returncode == 0 else []
    return listed or list(BUILD)


def binary(root: Path) -> str:
    """Where the binary the gates at root run is not the build of the content: a checkout whose
    build inputs differ from HEAD, outside GOH_LIVE, runs HEAD's build (gates/_goh_bin.sh), so it is
    keyed by HEAD's trees too. Empty otherwise: an export, a clean checkout, or GOH_LIVE (whose
    binary is the working tree's build)."""
    if os.environ.get("GOH_LIVE") or not (root / ".git").exists():
        return ""
    inputs = build_inputs(root)
    moved = _git(root, "status", "--porcelain", "--untracked-files=all", "--", *inputs)
    if moved.returncode != 0 or not moved.stdout.strip():
        return ""
    trees = _git(root, "rev-parse", *(f"HEAD:{p}" for p in inputs))
    return "binary head " + " ".join(trees.stdout.decode().split())


def identity(root: Path) -> str:
    """The gates root as one line: its content, and the binary if it is not the content's build."""
    extra = binary(root)
    return f"content {content(root)}" + (f" {extra}" if extra else "")


def key(mode: str, root: Path = GOH_ROOT) -> str:
    text = "\n".join(["gate-identity v3", f"mode {mode}", identity(root), *tools()])
    return hashlib.sha256(text.encode()).hexdigest()


def proven(roots: list[Path], path: str | None = None, repo: Path | None = None) -> list[str]:
    """gates/_proven.sh's run-constant goh half: one `goh` line per distinct root identity (a
    checkout and its commit's export are one), PYTHONPATH as a gate imports from it (pythonpath,
    scope gate_files: `unset` when it is), then the tools. PYTHONPATH was the live half's
    `env PYTHONPATH=<path>`, and goh's land puts the tip's head export first on it, a new path for
    every commit: no proven step was ever reused from one land to the next (GOH.PROVENPYPATH)."""
    seen: list[str] = []
    for root in roots:
        line = f"goh {identity(root)}"
        if line not in seen:
            seen.append(line)
    where = repo if repo is not None else Path.cwd()
    pp = "unset" if path is None else pythonpath(path, where, gate_files)
    return [*seen, f"pythonpath {pp}", *tools()]


def keyed(roots: list[Path]) -> list[str]:
    """The paths whose bytes the identity carries, under each root that holds them: a path
    dependency in one may be left to the identity (`goh rust-scope`, GOH_PROVEN_KEYED_ROOTS)."""
    return [str(root / part) for root in roots for part in PARTS if os.path.lexists(root / part)]


# The Rust gate's coverage group: the scripts that decide it, and the measurement it runs.
RUST_GATE = ("gates/rust_gate.sh", "gates/_rust_proven.sh")
RUST_MEASURE = "gates/coverage_gate.sh"
# A script a gate RUNS by its own directory or the gates root (`bash "$HERE/x.sh"`,
# `"$GOH_PY" "$GOH_ROOT/gates/x.py"`); source_closure follows what it sources and imports.
_RUN = re.compile(
    r"\$\{?(HERE|_goh_d|GOH_ROOT|GOH_DIR|GOH)\}?/((?:\.\./)?[A-Za-z0-9_./-]+\.(?:sh|py))"
    r'|\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/([A-Za-z0-9_./-]+\.(?:sh|py))'
)


def _closure(seeds: set[str], root: Path, goh: Path) -> set[str]:
    """seeds and every file under root they source, import (lib/source_closure.py) or run."""
    import source_closure  # lib/, beside this file; only the rust verb needs it

    files = set(seeds)
    while True:
        found, _ = source_closure.closure(sorted(files), root, goh)
        grown = set(found)
        for rel in found:
            if not rel.endswith(".sh"):
                continue
            here = Path(rel).parent
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                for m in _RUN.finditer(source_closure._strip_comment(line)):
                    var, path, own = m.groups()
                    rel_to = here if own or var in ("HERE", "_goh_d") else Path()
                    cand = os.path.normpath(rel_to / (path or own))
                    if (root / cand).is_file():
                        grown.add(cand)
        if grown <= files:
            return files
        files |= grown


def rust_gate_scripts(root: Path = GOH_ROOT) -> list[str]:
    """The Rust gate's coverage scripts under root, derived from their sources: RUST_GATE, and the
    closure of RUST_MEASURE over what each file sources, imports (lib/source_closure.py) or runs."""
    return sorted(_closure({RUST_MEASURE}, root, root) | set(RUST_GATE))


def runner_files(repo: Path, external: str) -> list[str]:
    """The files the external suite's command runs the binary THROUGH (GOH.RUSTCOVKEYSCOPE): each
    word of GOH_COV_RUST_EXTERNAL that names a file of the repo (`./tools/pytest.sh`), and what it
    sources, imports or runs. The suite itself is not one (the owner's rule, GOH.RUSTCOVSKIP):
    anything under tests/ is left out, as is a word naming a directory (`pytest tests/`)."""
    try:
        words = shlex.split(external, comments=True)
    except ValueError:
        words = external.split()
    seeds = set()
    top = repo.resolve()
    for w in words:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):  # VAR=value: an assignment, not a file
            continue
        cand = Path(os.path.normpath(top / w))
        try:
            rel = cand.relative_to(top).as_posix()
        except ValueError:
            continue
        if cand.is_file():
            seeds.add(rel)
    if not seeds:
        return []
    return sorted(r for r in _closure(seeds, top, GOH_ROOT) if r.split("/")[0] != "tests")


def _is_goh_root(d: Path) -> bool:
    return (d / "lib" / "gate_identity.py").is_file() and (d / RUST_MEASURE).is_file()


def gate_files(root: Path) -> list[str]:
    """What a gate can import from a gates root on PYTHONPATH: the gates' own files, the ones
    `content` keys (PARTS, bytecode aside). Gate code imports nothing of a gates root outside them
    (tests/test_proven_pythonpath.py walks every import), so its tests/, docs/ and hooks/ are not
    what a proven step read."""
    return [r for r in _files(root) if "__pycache__" not in r.split("/")]


def pythonpath(value: str, repo: Path, scope: Callable[[Path], list[str]]) -> str:
    """PYTHONPATH as a verdict reads it, the ONE formula the proven steps (scope gate_files) and the
    Rust coverage measurement (scope rust_gate_scripts) key it by: each entry that is the repo is
    `repo` (its inputs are keyed apart), each that is a gates root is the files of scope(root) by
    CONTENT, any other by its path. goh's own land prepends the tip's head export,
    ~/.cache/goh/head/<sha>, a new path for every commit: keyed by path, no land ever found the
    last one's measurement (GOH.RUSTCOVKEYSCOPE: three lands in a row re-measured, 468-476 s each)
    nor any proven step (GOH.PROVENPYPATH). A gates root BEHIND another is keyed by the files of
    its scope the roots ahead lack: the rest is never read from it. ~/.zshrc puts the live checkout
    there, main before the land, and keyed whole it moved the key once after every change."""
    top = repo.resolve()
    out = []
    ahead: set[str] = set()
    for entry in value.split(":"):
        d = Path(entry).resolve() if entry else None
        if d is not None and d == top:
            out.append("repo")
        elif d is not None and d.is_dir() and _is_goh_root(d):
            scripts = scope(d)
            rest = [r for r in scripts if r not in ahead]
            out.append("goh:" + (content(d, rest) if rest else "shadowed"))
            ahead |= set(scripts)
        else:
            out.append(entry)
    return ":".join(out)


def _uncovered(repo: Path, inputs: list[str]) -> list[str]:
    """Workspace members and path dependencies of repo's cargo workspace that no input covers."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(["cargo", "metadata", "--no-deps", "--offline", "--format-version", "1"],
                       cwd=repo, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    if r.returncode != 0:
        return [f"(cargo metadata failed: {(r.stderr.strip().splitlines() or ['?'])[-1]})"]
    top = repo.resolve()
    dirs = []
    for pkg in json.loads(r.stdout).get("packages", []):
        dirs.append(Path(pkg["manifest_path"]).parent)
        dirs += [Path(d["path"]) for d in pkg.get("dependencies", []) if d.get("path")]
    out = []
    for d in dirs:
        try:
            rel = d.resolve().relative_to(top).as_posix()
        except ValueError:
            out.append(str(d))
            continue
        if not any(rel == i or rel.startswith(f"{i}/") for i in inputs):
            out.append(rel)
    return sorted(set(out))


def rust(repo: Path, root: Path = GOH_ROOT, external: str | None = None,
         path: str | None = None) -> list[str]:  # fmt: skip
    """The Rust coverage verdict's identity lines (GOH.RUSTCOVSKIP, GOH.RUSTCOVKEYSCOPE): the
    repo's Rust build inputs by content, the Rust gate's coverage scripts by content, the files
    the external suite runs the binary through (runner_files), PYTHONPATH as the measurement reads
    it (pythonpath), the tools. Each line that keys files lists them after its hash, so a miss can
    name the file that moved. Raises LookupError when a workspace member or path dependency lies
    outside the keyed inputs."""
    inputs = build_inputs(repo)
    if missing := _uncovered(repo, inputs):
        raise LookupError("outside the Rust inputs: " + " ".join(missing))
    scripts = rust_gate_scripts(root)
    lines = ["rust-identity v2",
             f"inputs {content(repo, inputs)} {' '.join(inputs)}",
             f"scripts {content(root, scripts)} {' '.join(scripts)}"]  # fmt: skip
    if external is not None:
        runner = runner_files(repo, external)
        lines.append(f"runner {content(repo, runner) if runner else 'none'} {' '.join(runner)}")
    if path is not None:
        lines.append(f"pythonpath {pythonpath(path, repo, rust_gate_scripts)}")
    return [*lines, *tools()]


def suite(root: Path) -> str | None:
    """The identity of a TEST verdict on root (GOH.LANDPREDUP): a test reads the whole tree, not
    only the gates' part, so its key is the commit's tree -- git's own content identity, by path,
    mode and bytes, the formula `content` applies to the gates' part -- plus the tools above. A
    checkout with any change to it, untracked files included, has no identity: its bytes are not
    the tree's, and a verdict on it is not reusable. None outside a checkout."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"  # a reader never takes index.lock
    out = []
    for args in (("status", "--porcelain", "--untracked-files=all"), ("rev-parse", "HEAD^{tree}")):
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        if r.returncode != 0:
            return None
        out.append(r.stdout.strip())
    status, tree = out
    if status or not tree:
        return None
    text = "\n".join(["suite-identity v1", f"tree {tree}", *tools()])
    return hashlib.sha256(text.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("tools")
    k = sub.add_parser("key")
    k.add_argument("--mode", required=True)
    k.add_argument("--root", type=Path, default=GOH_ROOT)
    sub.add_parser("keyed").add_argument("--root", type=Path, action="append", required=True)
    pv = sub.add_parser("proven")
    pv.add_argument("--root", type=Path, action="append", default=[])
    pv.add_argument("--repo", type=Path, help="the repo a PYTHONPATH entry may be")
    pv.add_argument("--pythonpath", help="PYTHONPATH as the gate has it (absent: unset)")
    r = sub.add_parser("rust")
    r.add_argument("--repo", type=Path, required=True)
    r.add_argument("--root", type=Path, default=GOH_ROOT)
    r.add_argument("--external", help="GOH_COV_RUST_EXTERNAL: its runner's files are keyed")
    r.add_argument("--pythonpath", help="PYTHONPATH as the gate has it (an argument: re-running "
                   "HEAD's copy rewrites the environment's)")  # fmt: skip
    a = ap.parse_args(argv)
    if a.verb == "rust":
        try:
            print("\n".join(rust(a.repo.resolve(), a.root.resolve(), a.external, a.pythonpath)))
        except LookupError as e:
            print(f"gate_identity: no Rust coverage identity: {e}", file=sys.stderr)
            return 1
        return 0
    if a.verb == "tools":
        print("\n".join(tools()))
        return 0
    if a.verb == "proven":
        print("\n".join(proven([r.resolve() for r in a.root], a.pythonpath, a.repo)))
        return 0
    if a.verb == "keyed":
        print("\n".join(keyed([r.resolve() for r in a.root])))
        return 0
    print(key(a.mode, a.root.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
