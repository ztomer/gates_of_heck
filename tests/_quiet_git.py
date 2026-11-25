"""A test's own `git add`, `git commit -q` and `git config KEY VALUE`, made with no git process and
few files (GOH.FSEVQUIET).

Half of the suite's file events were git's: each add and commit of a fixture made an index.lock, a
fan-out directory and a temp file per loose object, HEAD.lock, the branch's lock, AUTO_MERGE.lock,
packed-refs.lock and COMMIT_EDITMSG, and the test's end removed what stayed: about 35 for one
commit of one file. `serve(argv, cwd, env)` makes what git would have made -- the same blobs, trees,
commit, branch and index, byte for byte (tests/test_quiet_git.py holds the two together) -- as one
pack, its idx, the index and the branch. tests/_suite_plugins.py asks it for every process a test
starts itself; a command a gate or a script starts is git's.

git itself runs wherever this would have to guess: a hook, an attribute or filter, a setting it does
not know, a submodule, objects borrowed from another repository, a conflict, a
merge under way, an option it does not read, a GIT_* variable, a `git` of the test's own on PATH,
GATES_OF_HECK_REAL_GIT=1.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import stat
import struct
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from _quiet_pack import LOCK, _put, _sha, flush, is_quiet, keep, own, quietly, started, watch

REAL = "GATES_OF_HECK_REAL_GIT"
_ENV_READ = {
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE", "GIT_CONFIG_NOSYSTEM", "GIT_OPTIONAL_LOCKS",
    "GIT_TERMINAL_PROMPT", "GIT_EDITOR", "GIT_PAGER",
}  # fmt: skip
_SET = {"user.name", "user.email", "commit.gpgsign=false", "maintenance.auto=false", "gc.auto=0"}
_UNDER_WAY = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply",
              "SQUASH_MSG", "MERGE_MSG", "BISECT_LOG", "info/attributes", "index.lock",
              "HEAD.lock", "objects/info/alternates")  # fmt: skip
_HOOKS = {"pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "post-rewrite",
          "post-index-change", "reference-transaction", "pre-auto-gc"}  # fmt: skip
_UNKNOWN = re.compile(
    r"^\[(?:include|includeif|extensions|filter)|^(?:autocrlf|eol|splitindex|"
    r"sparsecheckout|attributesfile|worktree|excludesfile|fsmonitor|template|cleanup)\b|"
    r"^(?:gpgsign|bare)\s*=\s*true|^(?:filemode|symlinks|ignorecase)\s*=\s*false|^logallrefupdates\s*=\s*always",
    re.IGNORECASE | re.MULTILINE,
)
_TREES: dict[str, str] = {}  # a commit made here -> its tree


class Declined(Exception):
    """git itself makes this one; nothing has been written."""


def _parse(argv: Sequence[str]) -> tuple[str, dict[str, str], str, list[str]]:
    """(-C directory, -c settings, subcommand, its arguments)"""
    here, conf, i = "", {}, 1
    while i < len(argv):
        if argv[i] == "-C" and i + 1 < len(argv):
            here = os.path.join(here, argv[i + 1])
        elif argv[i] == "-c" and i + 1 < len(argv):
            key, _, value = argv[i + 1].partition("=")
            if (
                key.lower() not in _SET
                and key.lower() != "core.hookspath"
                and f"{key.lower()}={value}" not in _SET
            ):
                raise Declined(argv[i + 1])
            conf[key.lower()] = value
        else:
            return here, conf, argv[i], list(argv[i + 1 :])
        i += 2
    raise Declined("no command")


@functools.cache
def _git_on(path: str) -> str | None:
    return shutil.which("git", path=path)


_PATH = os.environ.get("PATH", "")


def _settings(text: str) -> dict[str, str]:
    out, section = {}, ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line.strip("[]").lower()
        elif "=" in line:
            key, _, value = line.partition("=")
            out[f"{section}.{key.strip().lower()}"] = value.strip().strip('"')
    return out


def _linked(pointer: str) -> tuple[str, str]:
    """(its git dir, the repository's) of the linked work tree whose `.git` file is POINTER."""
    text = Path(pointer).read_text(encoding="utf-8").strip()
    git = os.path.join(os.path.dirname(pointer), text.removeprefix("gitdir: "))
    commondir = Path(git, "commondir")
    if not text.startswith("gitdir: ") or not commondir.is_file():
        raise Declined("a submodule, or a git dir elsewhere")
    common = os.path.join(git, commondir.read_text(encoding="utf-8").strip())
    if os.path.lexists(os.path.join(git, "config.worktree")):
        raise Declined("a setting of the work tree's own")
    return os.path.realpath(git), os.path.realpath(common)


class Repo:
    def __init__(self, top: str, conf: dict[str, str], env: Mapping[str, str]) -> None:
        # (git: this work tree's HEAD and index; common: the objects, refs, config and hooks)
        self.top, self.git, self.env = top, os.path.join(top, ".git"), env
        self.common = self.git
        if os.path.isfile(self.git):
            self.git, self.common = _linked(self.git)
        if not os.path.isdir(os.path.join(self.common, "objects")):
            raise Declined("not the top of a work tree")
        if any(os.path.lexists(os.path.join(d, p)) for p in _UNDER_WAY
               for d in (self.git, self.common)):  # fmt: skip
            raise Declined("under way")
        needs = [self.git, *(os.path.join(self.common, p) for p in ("", "objects", "refs"))]
        if not all(os.access(p, os.W_OK) for p in needs):
            raise Declined("sealed")  # a run's template (tests/_tree_clone.py)
        home = env.get("HOME", "")
        files = [os.path.join(env.get("XDG_CONFIG_HOME") or f"{home}/.config", "git", "config"),
                 os.path.join(home, ".gitconfig"), os.path.join(self.common, "config")]  # fmt: skip
        self.conf: dict[str, str] = {}
        for f in files:
            text = Path(f).read_text(encoding="utf-8") if os.path.isfile(f) else ""
            plain = "\n".join(ln.strip() for ln in text.splitlines())
            if _UNKNOWN.search(plain):
                raise Declined(f"{f} holds a setting not read here")
            self.conf |= _settings(text)
        self.conf |= conf
        self.entries = self._index()

    def _index(self) -> dict[bytes, tuple[bytes, int, bytes]]:
        """path -> (its ten stat words, mode, object)"""
        path = os.path.join(self.git, "index")
        if not os.path.exists(path):
            return {}
        data = Path(path).read_bytes()
        kind, version, n = struct.unpack_from(">4sII", data)
        if kind != b"DIRC" or version not in (2, 3):
            raise Declined(f"index version {version}")
        out, at = {}, 12
        for _ in range(n):
            mode, sha, flags = (*struct.unpack_from(">I", data, at + 24),
                                *struct.unpack_from(">20sH", data, at + 40))  # fmt: skip
            end = data.index(b"\0", at + 62)
            if flags & 0xF000 or mode == 0o160000:
                raise Declined("a flagged entry")
            out[data[at + 62 : end]] = data[at : at + 40], mode, sha
            at += (end - at + 8) & ~7
        while at < len(data) - 20:
            name, size = struct.unpack_from(">4sI", data, at)
            if name[:1].islower():
                raise Declined(f"index extension {name!r}")
            at += 8 + size
        return out

    def changed(self, paths: list[str]) -> list[tuple[str, bytes]]:
        """(`?` untracked, `M` or `D` against the index; path). No object is read for it."""
        argv = ["git", "-C", self.top, "ls-files", "-z", "-t", "--others", "--modified",
                "--deleted", "--exclude-standard", *(["--", *paths] if paths else [])]  # fmt: skip
        r = own(argv, {**self.env, "GIT_OPTIONAL_LOCKS": "0"})
        if r.returncode:
            raise Declined(r.stderr.decode(errors="replace"))
        how: dict[bytes, str] = {}  # a deleted one is listed as modified too
        for row in filter(None, r.stdout.split(b"\0")):
            tag, path = {b"? ": "?", b"C ": "M", b"R ": "D"}.get(row[:2]), row[2:]
            if tag is None:
                raise Declined(row.decode(errors="replace"))
            how[path] = "D" if "D" in (tag, how.get(path)) else tag
        out = [(tag, path) for path, tag in how.items()]
        if any(p.endswith((b"/", b".gitattributes", b".gitmodules")) for _, p in out):
            raise Declined("a repository, or attributes, in the tree")
        return out

    def add(self, changed: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
        """CHANGED taken into the index; the blobs that are new to it."""
        if any(os.path.basename(p) == b".gitattributes" for p in self.entries):
            raise Declined("attributes")
        new = []
        for how, name in changed:
            if how == "D":
                del self.entries[name]
                continue
            path = os.path.join(os.fsencode(self.top), name)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                mode, data = 0o120000, os.readlink(path)
            elif stat.S_ISREG(st.st_mode):
                mode = 0o100755 if st.st_mode & 0o100 else 0o100644
                data = Path(os.fsdecode(path)).read_bytes()
            else:
                raise Declined("no file")
            new.append(("blob", data))
            words = (int(st.st_ctime), st.st_ctime_ns % 10**9, int(st.st_mtime),
                     st.st_mtime_ns % 10**9, st.st_dev, st.st_ino, mode, st.st_uid, st.st_gid,
                     st.st_size)  # fmt: skip
            packed = struct.pack(">10I", *(w & 0xFFFFFFFF for w in words))
            self.entries[name] = packed, mode, _sha("blob", data)
        for name in self.entries:
            at = name.find(b"/")
            while at > 0:
                if name[:at] in self.entries:
                    raise Declined("a file where a directory is")
                at = name.find(b"/", at + 1)
        return new

    def write_index(self) -> None:
        out = [struct.pack(">4sII", b"DIRC", 2, len(self.entries))]
        for name, (words, _mode, sha) in sorted(self.entries.items()):
            row = words + sha + struct.pack(">H", min(len(name), 0xFFF)) + name
            out.append(row + b"\0" * (8 - len(row) % 8))
        data = b"".join(out)
        _put(os.path.join(self.git, "index"), data + hashlib.sha1(data).digest())

    def trees(self) -> tuple[str, list[tuple[str, bytes]]]:
        """(the index's tree, the tree objects)"""
        root: dict = {}
        for name, (_, mode, sha) in self.entries.items():
            *dirs, leaf = name.split(b"/")
            at = root
            for d in dirs:
                at = at.setdefault(d, {})
            at[leaf] = (mode, sha)
        made: list[tuple[str, bytes]] = []

        def tree(node: dict) -> bytes:
            rows = []
            for leaf, v in node.items():
                mode, sha = (0o40000, tree(v)) if isinstance(v, dict) else v
                key = leaf + b"/" if isinstance(v, dict) else leaf
                rows.append((key, b"%o " % mode + leaf + b"\0" + sha))
            body = b"".join(r for _, r in sorted(rows))
            made.append(("tree", body))
            return _sha("tree", body)

        return tree(root).hex(), made

    def branch(self) -> tuple[str, str]:
        """(HEAD's branch, its commit or "")"""
        head = Path(self.git, "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: refs/heads/"):
            raise Declined("a detached HEAD")
        ref = head[5:]
        loose, packed = Path(self.common, ref), Path(self.common, "packed-refs")
        if loose.is_file():
            return ref, loose.read_text(encoding="utf-8").strip()
        for line in packed.read_text(encoding="utf-8").splitlines() if packed.is_file() else ():
            if line.endswith(f" {ref}"):
                return ref, line.split()[0]
        return ref, ""

    def who(self, role: str) -> str:
        name = self.env.get(f"GIT_{role}_NAME") or self.conf.get("user.name", "")
        email = self.env.get(f"GIT_{role}_EMAIL") or self.conf.get("user.email", "")
        if not name or not email or re.search(r"[<>\n\\#;\"]|^[ .,:']|[ .,:']$", name):
            raise Declined("no plain identity")
        when = self.env.get(f"GIT_{role}_DATE") or ""
        if when and not re.fullmatch(r"\d{9,} [+-]\d{4}", when):
            raise Declined(when)
        return f"{name} <{email}> {when or f'{int(time.time())} +0000'}"

    def tree_of(self, commit: str) -> str:
        if commit not in _TREES:
            r = subprocess.run(["git", "-C", self.top, "rev-parse", f"{commit}^{{tree}}"],
                               capture_output=True, text=True, check=False, env=dict(self.env),
                               stdin=subprocess.DEVNULL)  # fmt: skip
            if r.returncode:
                raise Declined(r.stderr)
            _TREES[commit] = r.stdout.strip()
        return _TREES[commit]


def _message(parts: list[str]) -> str:
    """As git cleans a -m message (--cleanup=whitespace)."""
    lines = [ln.rstrip() for ln in "\n\n".join(parts).splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    if not text:
        raise Declined("no message")
    return text + "\n"


def _add(repo: Repo, args: list[str]) -> None:
    flags = {"-A", "--all", "-q", "--"}
    paths = [a for a in args if a not in flags and a != "."]
    if any(p.startswith(("-", ":", "/", "..")) or set(p) & set("*?[\\") for p in paths):
        raise Declined("a pathspec")
    if not paths and not {"-A", "--all", "."} & set(args):
        raise Declined("nothing named")
    changed = repo.changed(paths)
    seen = {os.fsdecode(p) for _, p in changed} | {os.fsdecode(p) for p in repo.entries}
    for p in paths:  # an ignored or missing one is git's to refuse
        if not any(s == p or s.startswith(p.rstrip("/") + "/") for s in seen):
            raise Declined(p)
    blobs = repo.add(changed)
    if changed:
        if blobs:
            keep(repo.common, blobs)
        repo.write_index()


def _commit(repo: Repo, args: list[str]) -> None:
    parts, quiet, every, empty, hooks, i = [], False, False, False, True, 0
    while i < len(args):
        a = args[i]
        if re.fullmatch(r"-q?a?m", a) or a in ("-aqm", "--message"):
            parts.append(args[i + 1])
            quiet, every, i = quiet or "q" in a, every or a[1] == "a" or a == "-qam", i + 1
        elif a in ("-q", "--quiet"):
            quiet = True
        elif a == "-a":
            every = True
        elif a in ("--no-verify", "-n"):
            hooks = False
        elif a == "--allow-empty":
            empty = True
        else:
            raise Declined(a)
        i += 1
    there = repo.conf.get("core.hookspath") or os.path.join(repo.common, "hooks")
    there = os.path.join(repo.top, there.replace("~", repo.env.get("HOME", ""), 1))
    found = _HOOKS & set(os.listdir(there)) if os.path.isdir(there) else set()
    if not quiet or found - ({"pre-commit", "commit-msg"} if not hooks else set()):
        raise Declined("a hook, or its summary")
    message = _message(parts)
    objects: list[tuple[str, bytes]] = []
    if every:
        objects = repo.add([c for c in repo.changed([]) if c[0] != "?"])
    ref, parent = repo.branch()
    if not re.fullmatch(r"[0-9a-f]{40}|", parent):
        raise Declined(parent)
    tree, trees = repo.trees()
    if not empty and (tree == repo.tree_of(parent) if parent else not repo.entries):
        raise Declined("nothing to commit")
    if any(os.path.lexists(os.path.join(repo.common, f"{p}.lock")) for p in (ref, "packed-refs")):
        raise Declined("locked")
    body = (f"tree {tree}\n" + (f"parent {parent}\n" if parent else "")
            + f"author {repo.who('AUTHOR')}\ncommitter {repo.who('COMMITTER')}\n\n{message}"
            ).encode()  # fmt: skip
    keep(repo.common, [*objects, *trees, ("commit", body)])
    if every:
        repo.write_index()
    sha = _sha("commit", body).hex()
    _TREES[sha] = tree
    at, packed = os.path.join(repo.common, ref), Path(repo.common, "packed-refs")
    if parent and not os.path.exists(at):  # where it is: no directory, no file
        text = packed.read_text(encoding="utf-8")
        packed.write_text(text.replace(f"{parent} {ref}\n", f"{sha} {ref}\n"), encoding="utf-8")
        return
    os.makedirs(os.path.dirname(at), exist_ok=True)
    _put(at, f"{sha}\n".encode())


def _config(top: str, conf: dict[str, str], args: list[str]) -> None:
    """`git config SECTION.NAME VALUE` of plain words, in the repository's own file."""
    path = os.path.join(top, ".git", "config")
    if conf or len(args) != 2 or not re.fullmatch(r"[A-Za-z]+\.[A-Za-z][A-Za-z0-9]*", args[0]):
        raise Declined("no plain setting")
    if not re.fullmatch(r"[\w./@:+-]+", args[1], re.ASCII) or args[1].startswith("-"):
        raise Declined("no plain value")
    if os.path.lexists(path + ".lock") or not os.access(os.path.dirname(path), os.W_OK):
        raise Declined("locked")
    section, _, name = args[0].partition(".")
    lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
    heads = [i for i, ln in enumerate(lines) if ln.startswith("[")]
    mine = [i for i in heads if lines[i].strip().lower() == f"[{section.lower()}]"]
    odd = any("\\" in ln or "#" in ln or ";" in ln or not ln.endswith("\n") for ln in lines)
    if len(mine) > 1 or odd or any(not ln.startswith(("[", "\t")) for ln in lines):
        raise Declined("a config not written by git alone")
    row = f"\t{name} = {args[1]}\n"
    if not mine:
        lines += [f"[{section}]\n", row]
    else:
        end = min([i for i in heads if i > mine[0]], default=len(lines))
        at = [i for i in range(mine[0] + 1, end)
              if lines[i].split("=")[0].strip().lower() == name.lower()]  # fmt: skip
        if len(at) > 1:
            raise Declined("two of it")
        if at:
            lines[at[0]] = row
        else:
            lines.insert(end, row)
    _put(path, "".join(lines).encode())


def serve(argv: object, cwd: object, env: Mapping[str, str] | None, root: str) -> bool:
    """Whether ARGV, a test's `git add`, `commit -q` or `config` in a repository under ROOT, is
    done."""
    if not isinstance(argv, (list, tuple)) or not argv or argv[0] != "git":
        return False
    try:
        words = [os.fsdecode(a) for a in argv]
        here, conf, command, args = _parse(words)
        if command not in ("add", "commit", "config"):
            return False
        env = os.environ if env is None else env
        if env.get(REAL) or any(k.startswith("GIT_") and k not in _ENV_READ for k in env):
            return False
        path = env.get("PATH", "")
        if path != _PATH and _git_on(path) != _git_on(_PATH):
            return False
        top = os.path.realpath(os.path.join(os.fsdecode(cwd or os.getcwd()), here))
        if not top.startswith(root + os.sep):
            return False
        with LOCK, quietly():
            if command == "config":
                _config(top, conf, args)
                return True
            repo = Repo(top, conf, env)
            if not repo.common.startswith(root + os.sep):  # this checkout's own, linked to
                return False
            (_add if command == "add" else _commit)(repo, args)
    except (Declined, OSError, ValueError, UnicodeError, IndexError):
        return False
    return True


def install(root: str) -> None:
    """Every process started from here on that is such a command is `true`, its work done."""
    root, start = os.path.realpath(root), subprocess.Popen.__init__

    def popen(self, args, *more, **kw):
        if is_quiet():
            return start(self, args, *more, **kw)
        if not more and not kw.get("shell") and serve(args, kw.get("cwd"), kw.get("env"), root):
            return start(self, ["/usr/bin/true"], *more, **kw)
        flush()
        started(self, args)
        return start(self, args, *more, **kw)

    subprocess.Popen.__init__ = popen
    watch()
