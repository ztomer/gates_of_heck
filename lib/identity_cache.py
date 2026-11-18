"""identity_cache.py -- gate_identity's two spawn-heavy answers, memoized across processes.

GOH.IDENTITYPORT (2026-11-18, "measure then port"): tools/spawn_profile.py counted 2,180
`git ls-files` and 712 `bash lib/goh_version.sh --inputs` in ONE land gate, every proven step and
coverage key asking lib/gate_identity.py afresh in a new process. Each answer is a function of a
few things this module reads WITHOUT a spawn, so it is kept in a file keyed on all of them:

  listing(root)  `git ls-files -co --exclude-standard -- PARTS` of a checkout: the names in the
                 index (its stat), the names on disk (each directory's stat under PARTS: an added,
                 removed or renamed entry moves its directory's mtime), and the ignore rules (every
                 .gitignore met, info/exclude, the config files and any excludesfile/include path
                 they name, HOME and XDG_CONFIG_HOME). File BYTES are no input: it lists names.
  inputs(root)   `goh_version.sh --inputs ROOT HEAD`: read from HEAD's commit alone, so keyed by
                 the commit id (read from the refs by hand) and the script's own bytes. Replace
                 refs or a reftable store make the commit id no key: no cache then.

Anything unreadable makes the key None and the caller spawns as before. The store is
$GOH_IDENTITY_CACHE, default ~/.cache/goh/identity, at most KEEP entries (oldest dropped).
Pinned by tests/test_identity_cache.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

KEEP = 512
_CFG_PATH = re.compile(r"^\s*(?:excludesfile|path)\s*=\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _dir() -> Path:
    d = os.environ.get("GOH_IDENTITY_CACHE")
    if d:
        return Path(d)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "goh" / "identity"


def _sig(p: Path | str) -> str:
    try:
        st = os.lstat(p)
    except OSError:
        return "-"
    return f"{st.st_ino}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}:{st.st_mode}"


def _bytes(p: Path | str) -> bytes:
    try:
        return Path(p).read_bytes()
    except OSError:
        return b"-"


def gitdirs(root: Path) -> tuple[Path, Path] | None:
    """(the checkout's git dir, the common dir), or None when .git is neither form."""
    dot = root / ".git"
    if dot.is_dir():
        gd = dot
    elif dot.is_file():
        text = dot.read_text(errors="replace").strip()
        if not text.startswith("gitdir:"):
            return None
        gd = (root / text[len("gitdir:") :].strip()).resolve()
    else:
        return None
    common = gd
    if (gd / "commondir").is_file():
        common = (gd / (gd / "commondir").read_text().strip()).resolve()
    return gd, common


def _configs(common: Path, gd: Path) -> list[str]:
    home = Path(os.environ.get("HOME") or Path.home())
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    files = [common / "config", gd / "config.worktree", home / ".gitconfig", xdg / "git" / "config",
             xdg / "git" / "ignore", common / "info" / "exclude", Path("/etc/gitconfig"),
             Path("/opt/homebrew/etc/gitconfig"), Path("/usr/local/etc/gitconfig")]  # fmt: skip
    out = [f"env {os.environ.get('HOME', '')} {os.environ.get('XDG_CONFIG_HOME', '')}"]
    seen: set[str] = set()
    while files:
        f = files.pop(0)
        if str(f) in seen:
            continue
        seen.add(str(f))
        data = _bytes(f)
        out.append(f"cfg {f} {hashlib.sha256(data).hexdigest()}")
        for m in _CFG_PATH.finditer(data.decode(errors="replace")):
            named = m.group(1).strip('"')
            named = str(home) + named[1:] if named.startswith("~") else named
            files.append(Path(named) if os.path.isabs(named) else f.parent / named)
    return out


def listing_key(root: Path, parts: tuple[str, ...]) -> str | None:
    dirs = gitdirs(root)
    if dirs is None:
        return None
    gd, common = dirs
    lines = ["listing v1", f"root {root.resolve()}", f"parts {' '.join(parts)}",
             f"index {_sig(gd / 'index')}", f"gitignore {_sig(root / '.gitignore')}",
             *_configs(common, gd)]  # fmt: skip
    for part in parts:
        top = root / part
        lines.append(f"top {part} {_sig(top)}")
        if not top.is_dir() or top.is_symlink():
            continue
        for here, subdirs, names in os.walk(top):
            subdirs.sort()
            lines.append(f"d {here} {_sig(here)}")
            if ".gitignore" in names:
                lines.append(
                    f"i {here} {hashlib.sha256(_bytes(Path(here) / '.gitignore')).hexdigest()}"
                )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def head_commit(root: Path) -> str | None:
    """HEAD's commit id from the refs, no spawn; None where that is not the whole answer."""
    dirs = gitdirs(root)
    if dirs is None:
        return None
    gd, common = dirs
    if (common / "reftable").exists() or (common / "refs" / "replace").exists():
        return None
    packed = _bytes(common / "packed-refs").decode(errors="replace")
    if "refs/replace/" in packed:
        return None
    ref = "HEAD"
    for _ in range(5):
        text = None
        for base in (gd, common):
            if (base / ref).is_file():
                text = (base / ref).read_text().strip()
                break
        if text is None:
            hit = re.search(rf"^([0-9a-f]{{40,64}}) {re.escape(ref)}$", packed, re.MULTILINE)
            text = hit.group(1) if hit else None
        if text is None:
            return None
        if text.startswith("ref:"):
            ref = text[4:].strip()
            continue
        return text if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", text) else None
    return None


def inputs_key(root: Path, scripts: list[Path]) -> str | None:
    commit = head_commit(root)
    if commit is None:
        return None
    lines = ["inputs v1", f"commit {commit}"]
    lines += [f"script {s} {hashlib.sha256(_bytes(s)).hexdigest()}" for s in scripts]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def get(kind: str, key: str | None) -> list[str] | None:
    if key is None:
        return None
    try:
        got = json.loads((_dir() / f"{kind}-{key}.json").read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, list) else None


def put(kind: str, key: str | None, value: list[str]) -> None:
    if key is None:
        return
    d = _dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{kind}-{key}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(value))
        os.replace(tmp, d / f"{kind}-{key}.json")
        entries = sorted(d.glob("*.json"), key=_mtime)
        for old in entries[:-KEEP]:
            old.unlink(missing_ok=True)
    except OSError:
        return


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _under(part: str, parts: tuple[str, ...], files: tuple[str, ...]) -> bool:
    """A literal path inside parts (not under a file part): a filter of the parts listing."""
    if not part or re.search(r"[*?\[\]:!\\]", part) or part.startswith("/"):
        return False
    bits = part.rstrip("/").split("/")
    return "." not in bits and ".." not in bits and "" not in bits and bits[0] in parts and (
        len(bits) == 1 or bits[0] not in files)  # fmt: skip


def listed(root: Path, want: tuple[str, ...], parts: tuple[str, ...], files: tuple[str, ...],
           git: Callable[..., subprocess.CompletedProcess[bytes]]) -> set[str] | None:  # fmt: skip
    """`git ls-files -co --exclude-standard -- want` for root: a want inside parts is read from ONE
    listing of parts, kept keyed on what it depends on (listing_key); else asked directly."""

    def ls(ps: tuple[str, ...]) -> set[str] | None:
        got = git(root, "ls-files", "-z", "-co", "--exclude-standard", "--", *ps)
        return {r for r in got.stdout.decode().split("\0") if r} if got.returncode == 0 else None

    if not all(_under(p, parts, files) for p in want):
        return ls(want)
    key = listing_key(root, parts)
    full = get("ls", key)
    if full is None:
        got = ls(parts)
        if got is None:
            return None
        full = sorted(got)
        put("ls", key, full)
    cut = [p.rstrip("/") for p in want]
    return {r for r in full if any(r == p or r.startswith(p + "/") for p in cut)}


def stamp_inputs(root: Path, script: Path, *reads: Path) -> list[str]:
    """`bash script --inputs root HEAD` (lib/goh_version.sh), kept keyed on HEAD's commit and the
    bytes of script and what it reads; empty when it fails."""
    key = inputs_key(root, [script, *reads])
    if (hit := get("inputs", key)) is not None:
        return hit
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run(["bash", str(script), "--inputs", str(root), "HEAD"], capture_output=True,
                         text=True, env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    listed = out.stdout.split() if out.returncode == 0 else []
    if listed:
        put("inputs", key, listed)
    return listed
