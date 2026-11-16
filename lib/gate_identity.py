"""The identity of a gate verdict: what besides the tree decided it (GOH.VERDICTKEY).

A verdict recorded by tree alone outlived what made it. The CIP.CACHING survey reproduced two:
gates/proven.sh keyed a step on rustc, cargo, python and swift only, so with another shellcheck
first on PATH it answered "proven ... skipped"; lib/loaded_verdict.py keyed goh-loaded/<tree>.json
on the tree only, so a run green under `--cmd true` answered "green already" for `--cmd false`.
And the release push rule (lib/release_gate.py) trusted a parent's goh-landed line, which a land
under a hand-set GOH_LAND_GATE=true wrote exactly as a full gate's.

    gate_identity.py tools                    # one line per tool a gate step may invoke
    gate_identity.py key --mode MODE [--root GOH]   # sha256 of MODE, the gates' content, the tools
    gate_identity.py proven --root GOH...     # gates/_proven.sh's run-constant identity: each root
                                              # once by content, then the tools
    gate_identity.py keyed --root GOH...      # the paths that content covers, one per line

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
Used by gates/_proven.sh's step identity (its goh roots, GOH.PROVENIDENTITY: it kept a formula of
its own, HEAD sha and diff for a checkout, the PATH for an export, so a commit's checkout and its
head export never reused each other's proven steps) and tools/land.sh's goh-landed line (read by
the push rule); the loaded run's record key went with that run (GOH.LOADEDDROP). Pinned by
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
import os
import shutil
import subprocess
import sys
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


def _files(root: Path) -> list[str]:
    """The gates' files under root, repo-relative: a checkout's tracked and untracked-unignored
    ones that exist, or every file of an export (a `git archive` of a commit: its tracked files)."""
    if (root / ".git").exists():
        listed = _git(root, "ls-files", "-z", "-co", "--exclude-standard", "--", *PARTS)
        if listed.returncode == 0:
            rels = {r for r in listed.stdout.decode().split("\0") if r}
            return sorted(r for r in rels if os.path.lexists(root / r))
    rels = []
    for part in PARTS:
        top = root / part
        if top.is_file() or top.is_symlink():
            rels.append(part)
        elif top.is_dir():
            rels.extend(
                str(p.relative_to(root)) for p in top.rglob("*") if p.is_file() or p.is_symlink()
            )
    return sorted(rels)


def content(root: Path) -> str:
    """sha256 of the gates' bytes under root, ONE formula wherever they are read: each file's
    repo-relative path, kind (link, executable, file) and bytes. A checkout and the head export of
    its commit hold the same bytes and key alike; two formulas (`ls-files -s` plus the diff here,
    path and bytes there) put one commit's verdict under two keys (GOH.VERDICTKEYDRIFT)."""
    h = hashlib.sha256()
    for rel in _files(root):
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


def proven(roots: list[Path]) -> list[str]:
    """gates/_proven.sh's run-constant goh half: one `goh` line per distinct root identity (a
    checkout and its commit's export are one), then the tools."""
    seen: list[str] = []
    for root in roots:
        line = f"goh {identity(root)}"
        if line not in seen:
            seen.append(line)
    return [*seen, *tools()]


def keyed(roots: list[Path]) -> list[str]:
    """The paths whose bytes the identity carries, under each root that holds them: a path
    dependency in one may be left to the identity (`goh rust-scope`, GOH_PROVEN_KEYED_ROOTS)."""
    return [str(root / part) for root in roots for part in PARTS if os.path.lexists(root / part)]


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
    for verb in ("proven", "keyed"):
        sub.add_parser(verb).add_argument("--root", type=Path, action="append", required=True)
    a = ap.parse_args(argv)
    if a.verb == "tools":
        print("\n".join(tools()))
        return 0
    if a.verb in ("proven", "keyed"):
        roots = [r.resolve() for r in a.root]
        print("\n".join(proven(roots) if a.verb == "proven" else keyed(roots)))
        return 0
    print(key(a.mode, a.root.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
