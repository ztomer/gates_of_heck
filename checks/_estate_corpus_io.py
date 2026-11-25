"""How the R3 sweep builds a corpus and runs a checker over it -- as the corpus's OWNER would.

Split out of `check_estate_corpus.py` at the 500-line cap, at a real boundary: that file decides
WHICH estate is judged and what a verdict means; this one makes the scratch copy, picks the file
the plant lands in, and runs the checker. The owner's `GOH_EXCLUDE` belongs here, because it is a
fact about how the corpus is judged: app_updates vendored a crate whose docs carry a dead anchor,
its own gate exempts the tree, and the sweep -- running the check bare -- called the corpus "NOT
clean" and went red on a repo that was green (2026-10-08).

How `check_estate_corpus.py` builds a corpus and runs a checker over it, as the corpus's OWNER
would: the scratch copy, the file the plant lands in, and the owner's `GOH_EXCLUDE` (read from its
`.gatesrc` without running it) passed to every house check whose `--help` takes `--exclude`. A
plant never lands in an exempt file. Pinned by `test_estate_corpus.py`.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import state_paths  # lib/, on the path from the line above
from _gitutil import foreign_repo_env, listed_files, scratch_git

HERE = os.path.dirname(os.path.abspath(__file__))
# The gates checkout whose `goh.sh` runs the native checks: GOH_DIR when the caller says (a copy of
# this file in another tree, as the empty-scope sweep makes), else the checkout this file is in.
GOH = os.environ.get("GOH_DIR") or os.path.dirname(HERE)


def consumer_exclude(root):
    """The corpus owner's `GOH_EXCLUDE`, read from its `.gatesrc` WITHOUT running it (another
    repo's file is data here, never code): `KEY=value`, an optional `export`, shell quoting and a
    trailing comment, the forms `crates/goh/src/gatesrc.rs` reads. Empty when it declares none."""
    try:
        with open(os.path.join(root, ".gatesrc"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ""
    for line in lines:
        m = re.match(r"\s*(?:export\s+)?GOH_EXCLUDE=(.*)$", line)
        if m:
            words = shlex.split(m.group(1), comments=True)
            return words[0] if words else ""
    return ""


def corpus_files(root, scope):
    """Tracked files under any of `scope`, repo-relative. The estate, not a directory listing."""
    out = []
    for rel in listed_files(root, staged=False):
        top = rel.split("/")[0] if "/" in rel else ""
        if scope == (".",) or top in scope:
            out.append(rel)
    return sorted(out)


def plant_in(files, ext, text, exclude=""):
    """The first real file of this language, and its path. Appending puts the violation inside real,
    structurally non-trivial code instead of in a file invented for it. Never a file the owner's
    `GOH_EXCLUDE` exempts: its gate does not judge that file, so neither may the plant's verdict."""
    skip = re.compile(exclude) if exclude else None
    for rel in files:
        if os.path.splitext(rel)[1] == ext and not (skip and skip.search(rel)):
            return rel, text
    return None, text


_IDENTITY = ("-c", "user.email=corpus@example.invalid", "-c", "user.name=corpus")


def borrow_objects(source, dest):
    """`dest`'s git reads `source`'s objects, so `add` there writes only what the owner has not
    committed: a corpus was 3,100 loose objects and fan-out dirs, made and removed (FSEVQUIET)."""
    got = subprocess.run(
        ["git", "-C", source, "rev-parse", "--path-format=absolute", "--git-path", "objects"],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    )
    if got.returncode == 0:
        info = os.path.join(dest, ".git", "objects", "info")
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, "alternates"), "w", encoding="utf-8") as handle:
            handle.write(got.stdout.strip() + "\n")


def materialise(source, files, dest):
    """Copy the real subtree into a scratch repo. Nothing ever runs in another working tree."""
    os.makedirs(dest)
    for rel in files:
        src = os.path.join(source, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst) or dest, exist_ok=True)
        try:
            shutil.copyfile(src, dst)
        except FileNotFoundError:
            # Deleted by its owner between the listing and this copy (another agent's edit in
            # flight): not in the tree any more, so not in the corpus. The floor still counts.
            continue
    scratch_git(dest)  # one empty .git copied, not init + config x2 (checks/_gitutil.py)
    borrow_objects(source, dest)
    for args in (("add", "-A"), (*_IDENTITY, "commit", "-qm", "corpus")):
        subprocess.run(
            ["git", "-C", dest, *args],
            capture_output=True,
            check=False,
            env=foreign_repo_env(),
            stdin=subprocess.DEVNULL,
        )


@functools.cache
def takes_exclude(checker):
    """Whether a house check reads `GOH_EXCLUDE` -- asked of the binary's own `--help`, so no list
    here can drift from the checks (docs/config/GOH_EXCLUDE.md names the same set)."""
    got = subprocess.run(
        ["bash", os.path.join(GOH, "gates", "goh.sh"), checker, "--help"],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    )
    return "--exclude" in got.stdout


def run_checker(checker, root, args, exclude=""):
    """(rc, output). The checker under test, exactly as a consumer runs it: a house check by its
    `goh.sh` name (the Python checkers it named are retired, Phase N3), with the owner's
    `GOH_EXCLUDE` when it reads one -- as the owner's structural gate passes it -- and a `.py` by
    path, the probe's own planted checkers, which cannot be anything else."""
    if checker.endswith(".py"):
        argv = [sys.executable, os.path.join(HERE, checker), *args]
    else:
        argv = ["bash", os.path.join(GOH, "gates", "goh.sh"), checker, *args]
        if exclude and takes_exclude(checker):
            argv += ["--exclude", exclude]
    result = subprocess.run(
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    )
    return result.returncode, (result.stdout + result.stderr)


# A corpus in a PRIVATE repository is named in `ESTATE` by a label, `store:<label>`, never by its
# path: this tree is public, and a private repository's name in it is refused (`goh private-names`,
# GOH.PRIVNAMES3). The label's path is the owner's, in the private seed store beside
# `private_names.txt`: `estate_corpus_roots.json`, `{"roots": {"<label>": "~/Projects/..."}}`.
STORE_ROOTS = "estate_corpus_roots.json"


def store_dir():
    """The private store (`crates/goh/src/estate_seed.rs` `store_dir`): GOH_SEED_DIR, else
    seeds/ in the state root (lib/state_paths.py)."""
    if os.environ.get("GOH_SEED_DIR"):
        return os.path.expanduser(os.environ["GOH_SEED_DIR"])
    return str(state_paths.seed_dir())


def resolve_root(root):
    """An entry's corpus root on this machine: a `~/` path expanded, a `store:<label>` read from
    the private store, None when the store has no such label (or no readable list).
    GOH_ESTATE_HOME stands in for the home of the `~/` roots and has no store: the empty-scope
    sweep names an empty one (GOH.FSEVQUIET: with the real estate, one sweep copied every corpus
    of this machine -- 16,131 file events -- to learn that the gate passes over nothing)."""
    home = os.environ.get("GOH_ESTATE_HOME")
    if home:
        return os.path.join(home, root[2:]) if root.startswith("~/") else None
    if not root.startswith("store:"):
        return os.path.expanduser(root)
    try:
        with open(os.path.join(store_dir(), STORE_ROOTS), encoding="utf-8") as handle:
            path = json.load(handle)["roots"][root[len("store:") :]]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return os.path.expanduser(path) if isinstance(path, str) else None
