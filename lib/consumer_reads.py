"""What the estate's consumers read of goh by path, for the pre-step (GOH.LANDMISS1119).

A consumer runs goh's gates through its .gatesrc, and goh's own tests judge those gates. It also
calls goh files by path from its OWN scripts -- sys_updater's tools/check_gate_floors.sh runs
`"$GOH/gates/required_tools.py" --floors` -- and no goh test runs that script: the release canary
does (scripts/release_canary.py, a step of goh's land gate, `goh structural --full` over every
consumer). GOH.PYTESTPIN moved pytest from a floor to a pin, which dropped it from `--floors`; the
pre-step's tests were green and the land gate's canary refused after 502 s (2026-11-19).

So the canary is the TEST of what a consumer reads, and it is chosen like one: a change to a goh
path a consumer names makes tools/affected_tests.py's `--run` run the canary after the tests.

A consumer file NAMES a goh path when its text holds `<root>/<path>`, <root> a shell variable
(`$GOH_DIR`, `${GOH}`, any name: consumers spell the harness many ways) or `gates_of_heck`, the
path tracked in goh and NOT in the consumer itself (its own `$ROOT/tools/load.py` is its own).
Prose (*.md, docs/, changelog/) mentions, never reads. The changed paths a gate never runs --
tests/, docs/, *.md, release_canary's NOT_GATE_INPUTS -- are never looked for.
Pinned by tests/test_consumer_reads.py.

GOH.LANDMISS1119: which consumers NAME a changed goh path from their own files (`<root>/<path>`,
the root a shell variable spelled any way or `gates_of_heck`, the path tracked in goh and not in
the consumer; prose and what no gate runs excepted), over the release canary's estate (registry +
`GOH_ESTATE_ROOT` walk, goh out). No goh test runs a consumer's own script (sys_updater's
`check_gate_floors.sh` reads `required_tools.py --floors`; GOH.PYTESTPIN was green in the pre-step
and red at the land gate's canary), so `tools/affected_tests.py --run` runs
`scripts/release_canary.py` after the tests when one does.
"""

from __future__ import annotations

import concurrent.futures as cf
import os
import re
import subprocess
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:  # gitrun's own bare imports (killtree)
    sys.path.append(str(Path(__file__).resolve().parent))
import estate_registry
import gitrun

# The changed paths no gate runs (scripts/release_canary.py NOT_GATE_INPUTS: docs, tests, *.md).
NOT_READ = ("docs/", "tests/", "changelog/")
PROSE = (":(exclude)*.md", ":(exclude)docs/", ":(exclude)changelog/", ":(exclude)CHANGELOG*")
ROOT = r"(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|gates_of_heck)/"
END = r"(?![A-Za-z0-9_./-])"


def looked_for(changed: list[str]) -> list[str]:
    """The changed paths a consumer could read: none a gate never runs."""
    return [p for p in changed if not p.startswith(NOT_READ) and not p.endswith(".md")]


def estate(goh: Path) -> list[Path]:
    """The canary's consumers (release_canary.consumers): the gated repos of the estate registry
    unioned with the walk of GOH_ESTATE_ROOT (else home Projects/), goh (any worktree) out."""
    root = Path(os.environ.get("GOH_ESTATE_ROOT") or Path.home() / "Projects")
    repos, _ = estate_registry.union(
        estate_registry.walk_repos(root),
        estate_registry.registry_path(estate_registry.state_file()),
    )
    own = _common_dir(goh)
    return [r for r in repos if (r / ".gatesrc").is_file() and _common_dir(r) != own]


def _common_dir(repo: Path) -> str:
    """The repository a work tree belongs to (lib/canary_sides.py common_dir's rule)."""
    out = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return str(Path(out).resolve()) if out else f"?{repo}"


def _git(repo: Path, *args: str) -> str:
    """`git <args>` read in `repo` (lib/gitrun.py: no inherited GIT_DIR answers for it)."""
    try:
        r = gitrun.run_reader(args, repo=repo, timeout=60)
    except subprocess.TimeoutExpired:
        return ""
    return r.stdout if r.returncode in (0, 1) else ""  # 1: git grep found nothing


def named(repo: Path, paths: list[str]) -> list[str]:
    """The PATHS a file of `repo` names under a root (module doc), the repo's own files excepted."""
    words = [w for p in paths for w in ("-e", f"/{p}")]
    out = _git(repo, "grep", "-I", "-h", "-F", *words, "--", ".", *PROSE)
    rx = re.compile(ROOT + "(" + "|".join(re.escape(p) for p in paths) + ")" + END)
    found = sorted({m.group(1) for line in out.splitlines() for m in rx.finditer(line)})
    own = set(_git(repo, "ls-files", "--", *found).split()) if found else set()
    return [p for p in found if p not in own]


def readers(goh: Path, changed: list[str], repos: list[Path] | None = None) -> dict[str, list[str]]:
    """{consumer: the changed paths it names}, for the consumers naming any (`repos`: the estate's
    by default)."""
    paths = looked_for(changed)
    if not paths:
        return {}
    repos = estate(goh) if repos is None else repos
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        hits = dict(zip(repos, pool.map(lambda r: named(r, paths), repos), strict=True))
    return {str(r): hits[r] for r in sorted(hits) if hits[r]}


def land_skip(goh: Path, repos: list[Path], say=print) -> str | None:
    """At a LAND (GOH_GATE_CLASS=land, GOH_CHANGE_BASE main's tip), why no sweep is owed: no
    consumer names a path the land changes (lib/consumer_reads.py, the pre-step's own question,
    tools/affected_tests.py canary). None: sweep -- not a land, no base, git cannot say, or a
    consumer names one. A consumer runs goh's gates through its .gatesrc, which goh's own tests
    judge; the release canary is the test of what a consumer runs BY PATH. It cost a median 100
    s (362 cpu-s) of every land whose gates changed (GOH.GATEFLOOR); the push and the sweep
    still run it whole."""
    base = os.environ.get("GOH_CHANGE_BASE", "")
    if os.environ.get("GOH_GATE_CLASS") != "land" or not base:
        return None
    try:
        r = gitrun.run_reader(("diff", "--name-only", base, "HEAD", "--"), repo=goh, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    changed = r.stdout.split()
    if found := readers(goh, changed, repos):
        paths = sorted({p for v in found.values() for p in v})
        n = len(found)
        say(f"release canary: {n} consumer{'s' * (n != 1)} name {', '.join(paths)} -- swept")
        return None
    return (f"no consumer names a path this land changes ({len(changed)} changed since "
            f"{base[:8]}, lib/consumer_reads.py)")  # fmt: skip
