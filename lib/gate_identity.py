"""The identity of a gate verdict: what besides the tree decided it (GOH.VERDICTKEY).

A verdict recorded by tree alone outlived what made it. The CIP.CACHING survey reproduced two:
gates/proven.sh keyed a step on rustc, cargo, python and swift only, so with another shellcheck
first on PATH it answered "proven ... skipped"; lib/loaded_verdict.py keyed goh-loaded/<tree>.json
on the tree only, so a run green under `--cmd true` answered "green already" for `--cmd false`.
And the release push rule (lib/release_gate.py) trusted a parent's goh-landed line, which a land
under a hand-set GOH_LAND_GATE=true wrote exactly as a full gate's.

    gate_identity.py tools                    # one line per tool a gate step may invoke
    gate_identity.py key --mode MODE [--root GOH]   # sha256 of MODE, the gates' content, the tools
    gate_identity.py proven [--root GOH...] [--repo R --pythonpath PP] [--closure]
                                              # gates/_proven.sh's run-constant identity: each root
                                              # once by content, PYTHONPATH by content, the tools
    gate_identity.py keyed --root GOH...      # the paths that content covers, one per line
    gate_identity.py rust --repo R [--root GOH] [--external CMD] [--pythonpath PP]
                                              # the Rust coverage verdict's inputs, as lines

The three parts:
  * MODE -- the command or gate mode the verdict is of (`full` for a land's own push gate, else
    the GOH_LAND_GATE string);
  * the CONTENT of the gates that ran: every file under gates/ checks/ lib/ tools/ tui/ scripts/ of
    the gates root (lib/run_rule.py CODE_PARTS), and the goh binary's build inputs (crates/,
    Cargo.toml, Cargo.lock, rust-toolchain.toml: the trees its source stamp names,
    gates/_goh_bin.sh), by path, kind and bytes -- ONE formula, so a checkout and its commit's head
    export key alike (GOH.VERDICTKEYDRIFT: two formulas put one tree's loaded verdict under two keys
    and the ship missed it); bytecode skipped. The gates run bin/goh, the build of those inputs:
    without them a change to goh's Rust alone left every key unchanged (GOH.PROVENIDENTITY). Where
    the binary is NOT the content's build it is keyed apart: an explicit GOH_BIN by its bytes (a
    tool line), and a checkout whose build inputs differ from HEAD without GOH_LIVE (it runs HEAD's
    build) by HEAD's trees. The version string a build reports (`git describe`, the release tag) is
    not keyed: the binary store already holds one build per source (gates/_goh_bin.sh), and a tag
    push reuses its commit's proofs;
  * the TOOLS: for each of rustc, cargo, python (the house interpreter), swift, shellcheck, ruff,
    cargo-llvm-cov and xcodebuild, the PATH hit, the binary it resolves to and that binary's size
    and mtime (no spawn: an upgrade rewrites the binary); a rustup proxy also by the toolchain
    binary it resolves to (`rustup which`, not the RUSTUP_TOOLCHAIN spelling); plus the selected
    Xcode. An absent tool is its own token, so installing one changes the identity. A shim naming
    the binary it runs is keyed as that binary (lib/tool_shim.py, GOH.VERDICTCARRY3).
THE RUST COVERAGE VERDICT (GOH.RUSTCOVSKIP, `rust`). A goh land spent 600-900 s measuring Rust
coverage when its diff touched no Rust (GOH.XEXITDETAIL: 896.62 s of a 21:37 land, for Python and
shell). The measurement's own inputs are narrower than the gates: the repo's Rust build inputs
(lib/goh_version.sh's stamp formula, embedded files included: build_inputs) and the Rust gate's
coverage scripts (rust_gate_scripts: every file gates/rust_gate.sh sources, imports or runs,
transitively, read from the sources by lib/rust_cov_scripts.py, never a hand list), the files
the external suite's command runs the binary THROUGH (runner_files: goh's tools/pytest.sh and
its closure, tests/ never: the owner's rule), PYTHONPATH with each gates root on it by content
(pythonpath: goh's land puts a new head export path on it every commit, GOH.RUSTCOVKEYSCOPE),
plus the tools. gates/_rust_proven.sh keys the coverage record on these lines;
on a hit the instrumented build and report are skipped and the tests still run, plain. A workspace
member or path dependency outside the keyed inputs has no such identity (exit 1): a change there
could not move the key, so the coverage runs.
Used by lib/step_closure.py (each proven step's goh roots by its CLOSURE, GOH.PROVENCLOSURE;
GOH.PROVENIDENTITY: gates/_proven.sh kept a formula of
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
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cov_env  # lib/, beside this file: what the coverage measurement reads
import identity_cache  # lib/, beside this file: the cross-process memo (GOH.IDENTITYPORT)
import run_rule  # lib/, beside this file: the run edge through a declared root (GOH.RUNRULEROOTS)
import rust_cov_scripts  # lib/, beside this file: the coverage scripts' closure (GOH.COVKEYGAP)
from tool_shim import fingerprint, past_shims  # lib/, beside this file

_RUN, _closure = run_rule.RUN, run_rule.run_closure  # lib/step_closure.py's run edge, closure
TOOLS = ("rustc", "cargo", "python3", "swift", "shellcheck", "ruff", "cargo-llvm-cov", "xcodebuild")
# The binary's build ROOTS; its inputs add each file a crate embeds: ask build_inputs, never this.
BUILD = ("crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml")
# goh's code (lib/run_rule.py CODE_PARTS: scripts/ too, whose builder every gate may run) and the
# binary's build roots: the ONE list every key mode reads, the whole content and each step's
# closure alike (GOH.FULLKEYSCRIPTS: the closure keyed scripts/ and this list did not).
PARTS = (*run_rule.CODE_PARTS, *BUILD)
GOH_ROOT = Path(__file__).resolve().parent.parent


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
        hit = past_shims(hit)  # a shim first on PATH: the binary it runs (lib/tool_shim.py)
        line = f"tool {name} {fingerprint(hit) if hit else 'absent'}"
        proxy = os.path.realpath(hit) if hit else ""
        if proxy and os.path.basename(proxy) == "rustup":  # a proxy: key what it runs
            got = _run([proxy, "which", name])
            line += f" runs {fingerprint(got) if os.path.isabs(got) else 'unresolved ' + got}"
        out.append(line)
    # An explicit GOH_BIN is the binary every gate runs (gates/_goh_bin.sh), whatever the content.
    named = os.environ.get("GOH_BIN", "")
    out.append(f"tool goh-bin {fingerprint(named) if named else 'unset'}")
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
        listed = identity_cache.listed(root, tuple(parts), PARTS, BUILD[1:], _git)
        if listed is not None:
            return sorted(r for r in listed if os.path.lexists(root / r))
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


def _entries(root: Path, parts: tuple[str, ...] | list[str], skip: frozenset[str] = frozenset()):
    """(rel, kind, bytes) of each keyed file under root: bytecode and `skip` left out."""
    for rel in _files(root, parts):
        if "__pycache__" in rel.split("/") or rel in skip:
            continue
        p = root / rel
        if p.is_symlink():
            yield rel, b"l", os.readlink(p).encode()
        else:
            yield rel, (b"x" if p.stat().st_mode & 0o111 else b"f"), p.read_bytes()


def content(root: Path, parts: tuple[str, ...] | list[str] = PARTS,
            skip: frozenset[str] = frozenset()) -> str:  # fmt: skip
    """sha256 of the gates' bytes under root, ONE formula wherever they are read: each file's
    repo-relative path, kind (link, executable, file) and bytes. A checkout and the head export of
    its commit hold the same bytes and key alike; two formulas (`ls-files -s` plus the diff here,
    path and bytes there) put one commit's verdict under two keys (GOH.VERDICTKEYDRIFT)."""
    h = hashlib.sha256()
    for rel, kind, data in _entries(root, parts, skip):
        h.update(rel.encode() + b"\0" + kind + b"\0" + str(len(data)).encode() + b"\0" + data)
    return h.hexdigest()


def manifest(roots: list[Path]) -> list[str]:
    """`<sha256> <kind> <path>` per file the proven identity keys under the roots (the first root
    holding a path wins), test-only Rust left out as `identity` leaves it: a record keeps the one
    its pass was keyed under, so a later miss on the same tree names the goh files that moved
    (GOH.PROOFKEY) instead of a bare new key."""
    out: dict[str, str] = {}
    for root in roots:
        for rel, kind, data in _entries(root, PARTS, test_only(root)):
            out.setdefault(rel, f"{hashlib.sha256(data).hexdigest()} {kind.decode()} {rel}")
    return [out[r] for r in sorted(out)]


def test_only(root: Path) -> frozenset[str]:
    """The Rust sources under root's crates/ that no release build compiles (GOH.PROOFKEY,
    lib/rust_test_only.py): a change to these alone is no new verdict, the binary the gates run is
    the same program. A file a crate embeds (build_inputs) stays keyed whatever its place."""
    if not (root / "crates").is_dir():
        return frozenset()
    import rust_test_only  # lib/, beside this file

    rels = [r for r in _files(root, ("crates",)) if r.endswith(".rs")]
    return rust_test_only.find(root, rels, lambda: build_inputs(root))


def build_inputs(root: Path) -> list[str]:
    """The paths the stamp names at root's HEAD, from the one formula (lib/goh_version.sh --inputs,
    GOH.STAMPINPUTS): the build roots, then each file a crate embeds -- an uncommitted edit to
    lib/busyloop_shapes.json is a binary that is not the content's build too."""
    script = GOH_ROOT / "lib" / "goh_version.sh"  # memoized (lib/identity_cache.py)
    return identity_cache.stamp_inputs(root, script, GOH_ROOT / "lib" / "bash5.sh") or list(BUILD)


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
    return f"content {content(root, PARTS, test_only(root))}" + (f" {extra}" if extra else "")


def key(mode: str, root: Path = GOH_ROOT) -> str:
    text = "\n".join(["gate-identity v3", f"mode {mode}", identity(root), *tools()])
    return hashlib.sha256(text.encode()).hexdigest()


def proven(roots: list[Path], path: str | None = None, repo: Path | None = None,
           closure: bool = False) -> list[str]:  # fmt: skip
    """gates/_proven.sh's run-constant half: one `goh` line per distinct root identity (a checkout
    and its export are one), PYTHONPATH by content (scope gate_files, GOH.PROVENPYPATH), the tools.
    `closure` (GOH_PROVEN_CLOSURE=on): no goh lines, each step's closure line keys them instead."""
    where = repo if repo is not None else Path.cwd()
    pp = "unset" if path is None else ((pythonpath(path, where, by_step_closure) or "goh only")
          if closure else pythonpath(path, where, gate_files))  # fmt: skip
    seen = [] if closure else list(dict.fromkeys(f"goh {identity(r)}" for r in roots))
    return [*seen, f"pythonpath {pp}", *tools()]


def by_step_closure(_root: Path) -> list[str]:
    """`pythonpath`'s proven scope: a gates root on it is read by each step's closure instead."""
    return []


def keyed(roots: list[Path]) -> list[str]:
    """The paths whose bytes the identity carries, under each root that holds them: a path
    dependency in one may be left to the identity (`goh rust-scope`, GOH_PROVEN_KEYED_ROOTS)."""
    return [str(root / part) for root in roots for part in PARTS if os.path.lexists(root / part)]


def rust_gate_scripts(root: Path = GOH_ROOT) -> list[str]:
    """The Rust coverage measurement's scripts under root: gates/rust_gate.sh's closure, derived
    from the sources (lib/rust_cov_scripts.py, GOH.COVKEYGAP); LookupError on a hole."""
    return rust_cov_scripts.rust_gate_scripts(root)


def runner_files(repo: Path, external: str, goh: Path = GOH_ROOT) -> list[str]:
    """The files the external suite's command runs the binary through (lib/rust_cov_scripts.py)."""
    return rust_cov_scripts.runner_files(repo, external, goh)


def _is_goh_root(d: Path) -> bool:
    measure = rust_cov_scripts.RUST_MEASURE
    return (d / "lib" / "gate_identity.py").is_file() and (d / measure).is_file()


def gate_files(root: Path) -> list[str]:
    """What a gate can import from a gates root on PYTHONPATH: the gates' own files, the ones
    `content` keys (PARTS, bytecode aside). Gate code imports nothing of a gates root outside them
    (tests/test_proven_pythonpath.py walks every import), so its tests/, docs/ and hooks/ are not
    what a proven step read."""
    skip = test_only(root)
    return [r for r in _files(root) if "__pycache__" not in r.split("/") and r not in skip]


def pythonpath(value: str, repo: Path, scope: Callable[[Path], list[str]]) -> str:
    """PYTHONPATH as a verdict reads it, the ONE formula the proven steps (scope gate_files) and the
    Rust coverage measurement (scope rust_gate_scripts) key it by: an entry that is the repo is
    `repo` (keyed apart), a gates root the files of scope(root) by CONTENT, any other its path.
    goh's land prepends the tip's head export, ~/.cache/goh/head/<sha>, a new path per commit:
    keyed by path no land found the last one's measurement (GOH.RUSTCOVKEYSCOPE, 468-476 s each)
    nor any proven step (GOH.PROVENPYPATH). A gates root BEHIND another is keyed by the files of
    its scope the roots ahead lack (~/.zshrc puts the live checkout there, main before the land);
    one whose every file is ahead, or met before, is no input (one more copy of the same gates
    re-measured, 830 s). An entry that is not there is no input either: head_export.py prunes the
    oldest export when a land makes one, and keyed by path it moved the key under a running suite
    (GOH.VERDICTCARRY2)."""
    top = repo.resolve()
    out: list[str] = []
    ahead: set[str] = set()
    for entry in value.split(":"):
        d = Path(entry).resolve() if entry else None
        if d is not None and not d.exists():
            continue
        if d is not None and d == top:
            label = "repo"
        elif d is not None and d.is_dir() and _is_goh_root(d):
            scripts = scope(d)
            rest = [r for r in scripts if r not in ahead]
            ahead |= set(scripts)
            if not rest:
                continue  # wholly shadowed: nothing in scope is ever read from it
            label = "goh " + content(d, rest)
        else:
            label = f"path {entry}"
        if label not in out:
            out.append(label)
    return "; ".join(out)


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
    the external suite runs the binary through (runner_files; runner_goh_files, what it reaches in
    goh by $GOH_DIR), PYTHONPATH as the measurement reads it (pythonpath), the tools. Each line
    that keys files lists them after its hash, so a miss can name the file that moved. Raises
    LookupError when a workspace member or path dependency lies outside the keyed inputs."""
    inputs = build_inputs(repo)
    if missing := _uncovered(repo, inputs):
        raise LookupError("outside the Rust inputs: " + " ".join(missing))
    scripts = rust_gate_scripts(root)
    lines = ["rust-identity v2",
             f"inputs {content(repo, inputs)} {' '.join(inputs)}",
             f"scripts {content(root, scripts)} {' '.join(scripts)}"]  # fmt: skip
    if external is not None:  # the GOH_* those files can read, by value (lib/cov_env.py)
        runner = runner_files(repo, external, root)  # its $GOH_DIR is the gates root's
        lines.append(f"runner {content(repo, runner) if runner else 'none'} {' '.join(runner)}")
        if gohs := rust_cov_scripts.runner_goh_files(repo, external, root):
            lines.append(f"runner-goh {content(root, gohs)} {' '.join(gohs)}")
        readers = rust_cov_scripts.env_readers  # a data file reads no variable
        read = [(repo, inputs), (root, readers(root, [*scripts, *gohs])), (repo, runner)]
        lines += cov_env.lines((b for r, ps in read for *_, b in _entries(r, ps)), repo, root,
                               os.environ)  # fmt: skip
    if path is not None:
        lines.append(f"pythonpath {pythonpath(path, repo, rust_gate_scripts)}")
    return [*lines, *tools()]


def clean_tree(root: Path) -> str | None:
    """HEAD's tree when the checkout at root holds exactly it (no change, no untracked file). With
    `suite_tools`, the two halves of `suite` apart: a verdict CARRIED from another tree keys its
    record by the tree it judged and the tools (tests/_land_verdicts.py, GOH.VERDICTCARRY)."""
    status = _git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all")
    tree = _git(root, "rev-parse", "HEAD^{tree}")  # a reader never takes index.lock (above)
    if status.returncode or tree.returncode or status.stdout.strip():
        return None
    return tree.stdout.decode().strip() or None


def suite_tools() -> str:
    """The tools a test verdict was reached with, without the tree (`suite` adds the tree)."""
    return hashlib.sha256("\n".join(["suite-tools v1", *tools()]).encode()).hexdigest()


def suite(root: Path) -> str | None:
    """The identity of a TEST verdict on root (GOH.LANDPREDUP): a test reads the whole tree, not
    only the gates' part, so its key is the commit's tree -- git's own content identity, by path,
    mode and bytes, the formula `content` applies to the gates' part -- plus the tools above. A
    checkout with any change to it, untracked files included, has no identity: its bytes are not
    the tree's, and a verdict on it is not reusable. None outside a checkout."""
    if (tree := clean_tree(root)) is None:
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
    sub.add_parser("manifest").add_argument("--root", type=Path, action="append", required=True)
    pv = sub.add_parser("proven")
    pv.add_argument("--root", type=Path, action="append", default=[])
    pv.add_argument("--repo", type=Path, help="the repo a PYTHONPATH entry may be")
    pv.add_argument("--pythonpath", help="PYTHONPATH as the gate has it (absent: unset)")
    pv.add_argument("--closure", action="store_true", help="GOH_PROVEN_CLOSURE=on")
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
        print("\n".join(proven([r.resolve() for r in a.root], a.pythonpath, a.repo, a.closure)))
        return 0
    if a.verb == "manifest":
        print("\n".join(manifest([r.resolve() for r in a.root])))
        return 0
    if a.verb == "keyed":
        print("\n".join(keyed([r.resolve() for r in a.root])))
        return 0
    print(key(a.mode, a.root.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
