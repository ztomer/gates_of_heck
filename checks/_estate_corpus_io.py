"""How the R3 sweep builds a corpus and runs a checker over it -- as the corpus's OWNER would.

Split out of `check_estate_corpus.py` at the 500-line cap, at a real boundary: that file decides
WHICH estate is judged and what a verdict means; this one makes the scratch copy, picks the file
the plant lands in, and runs the checker. The owner's `GOH_EXCLUDE` belongs here, because it is a
fact about how the corpus is judged: app_updates vendored a crate whose docs carry a dead anchor,
its own gate exempts the tree, and the sweep -- running the check bare -- called the corpus "NOT
clean" and went red on a repo that was green (2026-10-08).
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env, listed_files, scratch_git  # noqa: E402

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


def materialise(source, files, dest):
    """Copy the real subtree into a scratch repo. Nothing ever runs in another working tree."""
    os.makedirs(dest)
    for rel in files:
        src = os.path.join(source, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst) or dest, exist_ok=True)
        shutil.copyfile(src, dst)
    scratch_git(dest)  # one empty .git copied, not init + config x2 (checks/_gitutil.py)
    for args in (("add", "-A"), (*_IDENTITY, "commit", "-qm", "corpus")):
        subprocess.run(
            ["git", "-C", dest, *args], capture_output=True, check=False, env=foreign_repo_env()
        )


@functools.cache
def takes_exclude(checker):
    """Whether a house check reads `GOH_EXCLUDE` -- asked of the binary's own `--help`, so no list
    here can drift from the checks (docs/config.md's GOH_EXCLUDE row names the same set)."""
    got = subprocess.run(
        ["bash", os.path.join(GOH, "gates", "goh.sh"), checker, "--help"],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
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
    )
    return result.returncode, (result.stdout + result.stderr)
