"""`git worktree add -q [-b NAME | --detach] PATH [HEAD]` of a test, made with no git.

git makes it under a lock and in a child `git reset --hard`: HEAD.lock, the branch's lock,
index.lock, ORIG_HEAD and its lock, AUTO_MERGE.lock and packed-refs.lock twice, `locked`, an empty
`refs`: 28 file events for a tree of one file, 10 of them for what stays (GOH.FSEVQUIET). Here what
stays is written, from the work tree the command runs in: its index is HEAD's tree and each file is
its entry's own bytes; else git makes it, as for anything tests/_quiet_git.py would guess at.
"""

from __future__ import annotations

import os
import re
import stat
import struct
from pathlib import Path
from typing import TYPE_CHECKING

from _quiet_pack import _put, _sha

if TYPE_CHECKING:
    from _quiet_git import Repo

_MOST = 400  # entries read and written here
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*")


def _parsed(args: list[str], declined: type[Exception]) -> tuple[str, str | None, str]:
    """(PATH, the new branch or None: detached, the start or "")"""
    rest, branch, quiet, detach, i = [], None, False, False, 0
    while i < len(args):
        if args[i] == "-b":
            branch, i = args[i + 1], i + 1
        elif args[i] in ("-q", "--quiet"):
            quiet = True
        elif args[i] == "--detach":
            detach = True
        elif args[i].startswith("-"):
            raise declined(args[i])
        else:
            rest.append(args[i])
        i += 1
    if not quiet or bool(branch) == detach or not 1 <= len(rest) <= 2:
        raise declined("another form")
    if branch and not _WORD.fullmatch(branch):
        raise declined("no plain branch")
    return rest[0], branch, rest[1] if len(rest) > 1 else ""


def _contents(repo: Repo, declined: type[Exception]) -> dict[bytes, bytes]:
    """Each entry's bytes, as the work tree holds them: the index's own."""
    out = {}
    if len(repo.entries) > _MOST:
        raise declined("a large tree")
    for name, (_, mode, sha) in repo.entries.items():
        path = os.path.join(os.fsencode(repo.top), name)
        st = os.lstat(path)
        if os.path.basename(name) in (b".gitattributes", b".gitmodules"):
            raise declined("attributes")
        if stat.S_ISLNK(st.st_mode) and mode == 0o120000:
            out[name] = os.readlink(path)
        elif stat.S_ISREG(st.st_mode) and mode == (0o100755 if st.st_mode & 0o100 else 0o100644):
            out[name] = Path(os.fsdecode(path)).read_bytes()
        else:
            raise declined("another kind")
        if _sha("blob", out[name]) != sha:
            raise declined("an edit")
    return out


def add(repo: Repo, args: list[str], declined: type[Exception], root: str) -> None:
    path, branch, start = _parsed(args, declined)
    ref, at = repo.branch()
    if repo.git != repo.common or not re.fullmatch(r"[0-9a-f]{40}", at):
        raise declined("no branch of the main work tree")
    if start not in ("", "HEAD", at, ref.removeprefix("refs/heads/")):
        raise declined("another commit")
    hooks = repo.conf.get("core.hookspath") or os.path.join(repo.common, "hooks")
    hooks = os.path.join(repo.top, hooks.replace("~", repo.env.get("HOME", ""), 1))
    if any(os.path.lexists(os.path.join(hooks, h))
           for h in ("post-checkout", "reference-transaction", "post-index-change")):  # fmt: skip
        raise declined("a hook")
    to = os.path.join(repo.top, path)
    to = os.path.join(os.path.realpath(os.path.dirname(to)), os.path.basename(to))
    name, inside = os.path.basename(to), f"{to}/".startswith(f"{repo.git}/")
    mine, heads = os.path.join(repo.git, "worktrees", name), os.path.join(repo.git, "refs/heads")
    if inside or not to.startswith(root + os.sep) or not os.path.isdir(os.path.dirname(to)):
        raise declined("another place")
    if os.path.lexists(to) or os.path.lexists(mine) or not _WORD.fullmatch(name):
        raise declined("taken")
    packed = Path(repo.git, "packed-refs")
    refs = packed.read_text(encoding="utf-8") if packed.is_file() else ""
    if name.endswith(".lock") or (branch and (
        branch.endswith(".lock") or f" refs/heads/{branch}\n" in refs
        or f" refs/heads/{branch}/" in refs or os.path.lexists(os.path.join(heads, branch))
    )):  # fmt: skip
        raise declined("a branch there is")
    if any(os.path.lexists(p) for p in (f"{packed}.lock", os.path.join(heads, f"{branch}.lock"))):
        raise declined("locked")
    if repo.trees()[0] != repo.tree_of(at):
        raise declined("a staged change")
    held = _contents(repo, declined)
    os.makedirs(to)
    for entry, data in held.items():
        _, mode, sha = repo.entries[entry]
        file = os.path.join(os.fsencode(to), entry)
        os.makedirs(os.path.dirname(file), exist_ok=True)
        if mode == 0o120000:
            os.symlink(data, file)
        else:
            _put(os.fsdecode(file), data)
            if mode == 0o100755:
                os.chmod(file, stat.S_IMODE(os.lstat(file).st_mode) | 0o111)
        repo.entries[entry] = stat_words(os.lstat(file), mode), mode, sha
    os.makedirs(mine)
    _put(os.path.join(mine, "commondir"), b"../..\n")
    _put(os.path.join(mine, "gitdir"), os.fsencode(f"{to}/.git\n"))
    _put(
        os.path.join(mine, "HEAD"),
        (f"ref: refs/heads/{branch}\n" if branch else f"{at}\n").encode(),
    )
    _put(os.path.join(to, ".git"), os.fsencode(f"gitdir: {mine}\n"))
    repo.git = mine
    repo.write_index()
    if branch:
        os.makedirs(heads, exist_ok=True)
        _put(os.path.join(heads, branch), f"{at}\n".encode())


def stat_words(st: os.stat_result, mode: int) -> bytes:
    """The index's ten stat words of a file."""
    words = (int(st.st_ctime), st.st_ctime_ns % 10**9, int(st.st_mtime), st.st_mtime_ns % 10**9,
             st.st_dev, st.st_ino, mode, st.st_uid, st.st_gid, st.st_size)  # fmt: skip
    return struct.pack(">10I", *(w & 0xFFFFFFFF for w in words))
