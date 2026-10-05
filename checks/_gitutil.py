"""Shared git plumbing for the checkers. Two contracts live here:

1. File listing: the worktree — tracked plus untracked-but-not-ignored files —
   for full runs, staged files for pre-commit scope.
2. Content truth: a STAGED check measures THE INDEX — what will actually be
   committed — never the worktree, which may hold unrelated scratch edits.
   Full-tree runs measure the worktree, because that is what exists now.

The class this fixes: every checker used to open() working-tree paths even in
--staged mode, so a file staged clean then dirtied in the editor could block
an innocent commit, and a file staged dirty then cleaned in the editor could
slip one through. One implementation, all checkers.
"""

import functools
import os
import subprocess


@functools.lru_cache(maxsize=1)
def local_env_vars() -> tuple:
    """The variables that bind a git process to ONE repository, as git itself lists them.

    `git rev-parse --local-env-vars` is the list git clears when it crosses into a submodule;
    asking git (rather than keeping a copy) means a variable a future git adds is covered the day
    it ships. Works outside any repo and with a GIT_DIR that names nothing."""
    out = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
    )
    return tuple(out.stdout.split())


def foreign_repo_env(base=None) -> dict:
    """An environment for git on a repository that is NOT the one a hook is running for.

    A hook exports GIT_DIR and GIT_INDEX_FILE. Under a LINKED worktree GIT_DIR is absolute
    (`<main>/.git/worktrees/<name>`), so a child `git init <tmpdir>` that inherits it
    RE-INITIALISES THE REAL REPOSITORY instead -- and with extensions.worktreeConfig on, writes
    `core.bare = true` into the shared config, after which no checkout of that repo works
    (zinc, 2026-09-27). `git -C tmp config` / `add` / `commit` land in the real repo too. Every
    git call on a scratch, fixture or skeleton repo -- and every process run INSIDE one -- takes
    this env. Never used for the repo being gated: there GIT_INDEX_FILE names the index being
    committed, which is exactly the tree to police."""
    env = dict(os.environ if base is None else base)
    for name in local_env_vars():
        env.pop(name, None)
    return env


def repo_root() -> str:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return out.stdout.strip()


def listed_files(root: str, staged: bool) -> list[str]:
    """Repo-root-relative file list. Staged mode lists Added/Copied/Modified
    index entries; full mode lists the WORKTREE: everything tracked plus every
    untracked file git does not ignore (`--cached --others --exclude-standard`).

    Full mode used to be `ls-files` alone — tracked only — so a brand-new
    oversized or emoji-bearing file was invisible to `ci.sh` / `--full` right up
    until it was staged, when pre-commit finally saw it (2026-09-13: two repos,
    twice in one day). "Full" answers "is my tree green"; an untracked file IS
    the tree. Ignored files (build output, caches) stay out, as before.

    No `pathspec` parameter, and it used to have one that was accepted and
    IGNORED: every caller got the whole tree whatever it asked for. A parameter
    that lies is worse than no parameter — the next caller passes a scope, gets
    everything, and does not find out. A caller that needs a PATHSPEC wants a
    different question anyway: this lists the staged DIFF, so it cannot answer
    "what does this commit contain", which is what a tree-scope count needs.
    `checks/_claim_derive.tree_files` asks git that question directly.

    `-z` + NUL splitting is load-bearing: without it git QUOTES paths holding
    non-ASCII or control characters ("caf\\303\\251.md", "we\\nird.md"), names
    no consumer could resolve — those files were silently unpoliced. decode
    with 'replace' keeps a hostile byte sequence from killing the gate."""
    cmd = (
        ["git", "-C", root, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACM"]
        if staged
        else ["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0:
        # A silent [] here is the worst possible answer: every consumer reports "0 files clean"
        # and exits 0, so a corrupt index or an unreadable object database reads exactly like a
        # spotless repo. Callers guard the "not a git repo" case themselves via repo_root(); this
        # is git ANSWERING and failing, which nothing was distinguishing from an empty tree.
        raise RuntimeError(
            "git %s failed (exit %d): %s"
            % (cmd[3], out.returncode, out.stderr.decode("utf-8", "replace").strip()[:200])
        )
    return [n.decode("utf-8", "replace") for n in out.stdout.split(b"\0") if n]


# Directories a FALLBACK walk never descends: build output and VCS internals. Only consulted
# when git cannot name the tree; inside a work tree, git's own ignore rules decide.
PRUNE = frozenset({".git", "target", ".build", "build", "node_modules", "__pycache__", ".venv"})


def tree_files(root: str) -> list:
    """Root-relative paths of every file the TREE under `root` holds, without reading build output.

    Inside a git work tree this is git's listing of the worktree (`listed_files`): tracked plus
    untracked-but-not-ignored, relative to `root` even when `root` is a subdirectory. Git already
    knows which directories are build output, so nothing under an ignored `target/` is ever
    opened -- measured 2026-10-05, `rglob("Cargo.toml")` in one crate descended 50,950
    directories of `target/` (2.27 s of system time) and then threw the results away. Outside a
    work tree (a fixture, a skills corpus) it walks, PRUNING `PRUNE` instead of filtering after.
    """
    try:
        return listed_files(root, staged=False)
    except RuntimeError:
        pass
    found = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE)
        rel = os.path.relpath(current, root)
        for name in sorted(filenames):
            found.append(name if rel == "." else f"{rel}/{name}".replace(os.sep, "/"))
    return found


def cargo_manifests(root, skip=()) -> list:
    """Every `Cargo.toml` in the tree (`tree_files`), as Paths, minus any whose path has a part in
    `skip` (each caller's own policy: vendored trees, reference copies)."""
    from pathlib import Path

    base = Path(root)
    skip = set(skip)
    return sorted(
        base / rel
        for rel in tree_files(str(root))
        if rel.rsplit("/", 1)[-1] == "Cargo.toml"
        and not skip.intersection(rel.split("/"))
        and (base / rel).is_file()  # a tracked manifest deleted from the worktree is not there
    )


def content_bytes(root: str, rel: str, staged: bool):
    """The bytes this check should police: the index blob when staged, the
    worktree otherwise. None means 'nothing to police' (deleted/binary-unreadable
    is the caller's call — this returns raw bytes either way when present).

    No PEP 604 unions in annotations here on purpose: hooks resolve whatever
    `python3` is on PATH (often the OS-bundled 3.9), and `bytes | None`
    evaluates at def time there."""
    if staged:
        out = subprocess.run(
            ["git", "-C", root, "show", f":{rel}"],
            capture_output=True,
        )
        if out.returncode == 0:
            return out.stdout
        # Not resolvable from the index (race with a re-staged delete):
        # fall through to whatever the worktree has.
    try:
        with open(f"{root}/{rel}", "rb") as fh:
            return fh.read()
    except OSError:
        return None


def line_count(blob) -> int:
    """Lines in a blob, by the ONE definition the gates share.

    A trailing newline TERMINATES the last line, it does not begin another, so
    "a\nb\n" is 2 lines and "a\nb" is also 2. Empty is 0.

    This lives here because it was written twice: `check_file_length` had it
    right and `check_exclusion_has_ceiling` used a plain `count + 1`, which
    reads every newline-terminated file as one line longer. At the boundary the
    two gates would then disagree about the same file -- one calling a 500-line
    file compliant while the other demanded a ceiling for it -- and a
    disagreement between gates is read as a bug in the file, not in the gates.
    """
    return blob.count(b"\n") + (0 if blob.endswith(b"\n") or not blob else 1)
