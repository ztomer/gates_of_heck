#!/usr/bin/env python3
"""wf_pin.py -- `goh wf-pin`: every estate pin goh owns that is older than goh's, and the bump that
`goh ship` runs after each goh release (GOH.WFPIN, GOH.CLASS.ROLLOUT, GOH.PINPROPAGATE).

The pins goh owns are one manifest, lib/consumer_pins.py PINS: the goh release a workflow checks out
(read here) and goh's ruff (`ruff==X.Y.Z` in a pyproject, requirements or workflow; read there).

A consumer's hosted gate checks out goh at a NAMED release (CI.PUBLIC: never main), and nothing
moved that name when goh moved: on 2026-11-12 every estate workflow still pinned v0.95.0 or v0.96.0,
so "the local gate is identical to CI" was broken by drift no gate saw. A copy that can go stale is
the class; here the copy is one token per workflow, so the release that makes it stale moves it:
goh's own `.gatesrc` GOH_SHIP_INSTALL runs `--bump` once the tag is pushed.
What the bump could not move, each repo's own gate refuses: `goh goh-pins`, a structural step
(crates/goh/src/gohpin/), fails a pin more than ten minor releases behind the running goh, a goh
checkout at no release, and a tag-reading gate job on a shallow clone.

    goh wf-pin                    # list: one line per stale pin; exit 1 if any
    goh wf-pin --bump [--tag vX.Y.Z] [--except REPO]...   # rewrite, commit and push each repo's

THE PINS IT READS, in a tracked `.github/workflows/*.yml|yaml` that names goh's repository:
  * `repository: OWNER/gates_of_heck` with `ref: vX.Y.Z` in the same `with:` mapping;
  * `GOH_TAG: vX.Y.Z` (the clone reads "$GOH_TAG");
  * `git clone ... --branch vX.Y.Z https://github.com/OWNER/gates_of_heck` on one line.
A goh checkout or clone with no pin at all is listed as UNPINNED and never rewritten: choosing its
first pin is the repo's decision. Comments are prose and are not rewritten.

THE BUMP writes and commits through lib/estate_commit.py, the one door into another repo: the pin
files alone (and the uv.lock a ruff move relocks), in one commit through the repo's own hooks, or
nothing (set aside, named: on hold, off its default branch, a land holding its lock, pin files
uncommitted). Then it PUSHES through the repo's own push gate, with any pin commit an earlier bump
left unpushed (GOH.PINPROPAGATE: after v0.121.0 seventeen repos sat ahead of origin with one): a pin
commit left behind is the next writer's rebase. An owner's unpushed commit is never pushed by goh;
the pin then waits on it, named. Each repo's outcome goes to the ship's record (GOH_SHIP_RESULT,
consumer_pins.record_path), which goh ship carries into its result. A set-aside or refused repo
stays stale and is listed by the next run, so the bump exits 0 unless it could not read the estate.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import counted, err, info, ok, warn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import consumer_pins
import estate_commit
import estate_registry
from consumer_pins import Pin

GOH = Path(__file__).resolve().parent.parent
TAG = r"v\d+\.\d+\.\d+"
bumped = consumer_pins.bumped  # the rewrite is the manifest's; tests read it under this name too
# consumer_pins.SUBJECT recognises every pin commit by its first words: keep them.
MESSAGE = "ci: pin goh {tag} in the hosted gate{ruff} -- goh wf-pin --bump (GOH.PINPROPAGATE)"


@dataclass(frozen=True)
class Scan:
    pins: list[Pin]
    unpinned: list[int]  # lines that check out or clone goh at no named release


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def _mapping(lines: list[str], i: int) -> range:
    """The lines of the YAML mapping holding line I: its siblings at the same indent, up to the
    nearest line indented less (or a `- ` list item at that indent, which starts another)."""
    ind = _indent(lines[i])

    def inside(j: int) -> bool:
        s = lines[j]
        return not s.strip() or s.lstrip().startswith("#") or _indent(s) >= ind

    lo = i
    while lo > 0 and inside(lo - 1) and not lines[lo].lstrip().startswith("- "):
        lo -= 1
    hi = i
    while hi + 1 < len(lines) and inside(hi + 1) and not lines[hi + 1].lstrip().startswith("- "):
        hi += 1
    return range(lo, hi + 1)


def scan(text: str, slug: str) -> Scan:
    lines = text.splitlines()
    pins: list[Pin] = []
    unpinned: list[int] = []
    s = re.escape(slug)
    repo_line = re.compile(rf"^\s*(?:-\s+)?repository:\s*['\"]?{s}['\"]?\s*(?:#.*)?$")
    ref_line = re.compile(rf"^\s*ref:\s*['\"]?({TAG})['\"]?\s*(?:#.*)?$")
    any_ref = re.compile(r"^\s*ref:")
    goh_tag = re.compile(rf"^\s*GOH_TAG:\s*['\"]?({TAG})['\"]?\s*(?:#.*)?$")
    clone = re.compile(rf"\bgit clone\b.*github\.com/{s}(?:\.git)?\b")
    branch = re.compile(rf"--branch[ =]['\"]?({TAG})['\"]?")
    for i, line in enumerate(lines):
        if repo_line.match(line):
            refs = [j for j in _mapping(lines, i) if any_ref.match(lines[j])
                    and _indent(lines[j]) == _indent(line.replace("- ", "  ", 1))]  # fmt: skip
            m = ref_line.match(lines[refs[0]]) if refs else None
            if m:
                pins.append(Pin(refs[0] + 1, m.start(1), m.end(1), m.group(1)))
            elif not refs:
                unpinned.append(i + 1)
        elif m := goh_tag.match(line):
            pins.append(Pin(i + 1, m.start(1), m.end(1), m.group(1)))
        elif clone.search(line) and not line.lstrip().startswith("#"):
            if m := branch.search(line):
                pins.append(Pin(i + 1, m.start(1), m.end(1), m.group(1)))
            elif "$GOH_TAG" not in line and "${GOH_TAG}" not in line:
                unpinned.append(i + 1)
    return Scan(pins, unpinned)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return estate_commit.git(repo, *args)


def workflows(repo: Path) -> list[str]:
    out = _git(repo, "ls-files", "-z", "--", ".github/workflows").stdout
    return sorted(f for f in out.split("\0") if f.endswith((".yml", ".yaml")))


def newest_tag(goh: Path) -> str | None:
    tags = _git(goh, "tag", "--list", "v*", "--sort=-v:refname").stdout.split()
    return next((t for t in tags if re.fullmatch(TAG, t)), None)


def goh_slug(goh: Path) -> str | None:
    url = _git(goh, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def stale(repo: Path, slug: str, tag: str) -> tuple[dict[str, list[Pin]], dict[str, list[int]]]:
    """({workflow: its pins not on TAG}, {workflow: its unpinned lines})."""
    old: dict[str, list[Pin]] = {}
    loose: dict[str, list[int]] = {}
    for rel in workflows(repo):
        try:
            text = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        s = scan(text, slug)
        if behind := [p for p in s.pins if p.tag != tag]:
            old[rel] = behind
        if s.unpinned:
            loose[rel] = s.unpinned
    return old, loose


def bump(repo: Path, old: dict[str, list[Pin]], rold: dict[str, list[Pin]], tag: str,
         ruff: str) -> tuple[str, str]:  # fmt: skip
    """Move REPO's stale goh pins (OLD) and ruff pins (ROLD) in one commit, then push it:
    (pushed | committed | refused | set_aside, one line saying what happened)."""
    lock, relock = consumer_pins.relock(repo, sorted(rold), ruff)
    paths = sorted({*old, *rold, *lock})
    if why := estate_commit.set_aside(repo, paths):
        return "set_aside", f"set aside: {why}"
    with consumer_pins.land_held(repo) as holder:
        if holder:
            return "set_aside", f"set aside: a land holds its lock ({holder})"

        def write() -> str | None:
            for rel in sorted({*old, *rold}):
                p = repo / rel
                moves = [(q, tag) for q in old.get(rel, [])] + [
                    (q, ruff) for q in rold.get(rel, [])
                ]
                p.write_text(consumer_pins.rewrite(p.read_text(encoding="utf-8"), moves),
                             encoding="utf-8")  # fmt: skip
            return relock()

        moved = [tag] * bool(old) + [f"ruff {ruff}"] * bool(rold)
        msg = MESSAGE.format(tag=tag, ruff=f" and ruff {ruff}" if rold else "")
        done, said = estate_commit.commit_paths(repo, paths, msg, write)
        if not done:
            return "refused", said
        return consumer_pins.push(
            repo, f"pinned {', '.join(moved)} {said.removeprefix('committed ')}"
        )


def stranded(repo: Path) -> bool:
    """REPO's branch holds a pin commit its upstream lacks (an earlier bump's, never pushed)."""
    _, span = consumer_pins.ahead(repo)
    return any(consumer_pins.is_pin_commit(repo, sha, s) for sha, s in span)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("GOH_ESTATE_ROOT", Path.home() / "Projects")),
    )
    ap.add_argument("--tag", help="the goh release to pin (default: goh's newest vX.Y.Z tag)")
    ap.add_argument("--slug", help="goh's GitHub OWNER/NAME (default: from goh's origin)")
    ap.add_argument("--bump", action="store_true", help="rewrite, commit and push each stale pin")
    ap.add_argument(
        "--record",
        default=os.environ.get("GOH_SHIP_RESULT"),
        help="the goh ship result whose pins record this writes (GOH_SHIP_RESULT)",
    )
    ap.add_argument(
        "--budget",
        type=float,
        default=consumer_pins.BUDGET,
        help="seconds the sweep's pushes may take in all (default %(default)s)",
    )
    ap.add_argument("--except", dest="kept", action="append", type=Path, default=[],
                    help="a repo to list but never bump (repeatable)")  # fmt: skip
    a = ap.parse_args(argv)
    tag = a.tag or newest_tag(GOH)
    slug = a.slug or goh_slug(GOH)
    if not tag or not re.fullmatch(TAG, tag):
        err(f"wf-pin: no goh release to pin ({tag or 'goh has no vX.Y.Z tag'}); pass --tag vX.Y.Z")
        return 2
    if not slug:
        err("wf-pin: goh's origin is not a GitHub URL; pass --slug OWNER/NAME")
        return 2
    root = a.root.resolve()
    walked = estate_registry.walk_repos(root, estate_registry.REPO_DEPTH)
    repos, _ = estate_registry.union(
        walked, estate_registry.registry_path(estate_registry.state_file())
    )
    kept = {p.expanduser().resolve() for p in a.kept}
    ruff = consumer_pins.ruff_pin()
    consumer_pins.budget(a.budget)
    n_stale = 0
    record: dict[str, dict[str, str]] = {}
    for repo in repos:
        if repo.resolve() == GOH.resolve():
            continue  # goh's own workflow runs this checkout, not a release
        old, loose = stale(repo, slug, tag)
        rold = consumer_pins.stale_ruff(repo, workflows(repo), ruff)
        where = estate_registry_rel(repo, root)
        for rel, lines in loose.items():
            warn(f"{where}: {rel}:{','.join(map(str, lines))} checks out goh at no named release")
        if not old and not rold:
            if a.bump and stranded(repo):  # an earlier bump's commit: land it now
                status, said = consumer_pins.push(repo, "")
                record[where] = {"status": status, "said": said}
                (ok if status == "pushed" else err)(f"{where}: {said}")
            continue
        n_stale += 1
        have = sorted({p.tag for pins in old.values() for p in pins})
        rhave = sorted({p.tag for pins in rold.values() for p in pins})
        named = "; ".join([f"{', '.join(old)} pin {', '.join(have)}, not {tag}"] * bool(old)
                          + [f"{', '.join(rold)} pin ruff {', '.join(rhave)}, not {ruff}"]
                          * bool(rold))  # fmt: skip
        if not a.bump:
            warn(f"{where}: {named}")
        elif repo.resolve() in kept:
            info(f"{where}: {named}; set aside (--except): left for its owner")
            record[where] = {"status": "kept", "said": "--except: left for its owner"}
        else:
            status, said = bump(repo, old, rold, tag, ruff)
            record[where] = {"status": status, "said": said}
            (ok if status == "pushed" else err)(f"{where}: {said}")
    if a.bump and a.record:
        consumer_pins.write_record(a.record, tag, ruff, record)
    if not n_stale:
        ok(f"every goh-owned pin in {counted(len(repos), 'estate repo')} is current "
           f"(goh {tag}, ruff {ruff})")  # fmt: skip
        return 0
    if not a.bump:
        info(f"move them: goh wf-pin --bump  ({counted(n_stale, 'repo')} behind {tag})")
        return 1
    return 0


def estate_registry_rel(repo: Path, root: Path) -> str:
    try:
        return repo.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(repo)


if __name__ == "__main__":
    sys.exit(main())
