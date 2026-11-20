#!/usr/bin/env python3
"""check_worker_share.py -- a consumer's gate script never sizes a pool to the machine.

    check_worker_share.py [repo]      # every gate script the repo tracks; exit 1 on a new pool
    check_worker_share.py --probe     # a planted pool is red, a bounded one green (calibration)

WHY (GOH.POOLSHARE2). GOH.POOLSHARE gave every admitted gate a share of the cores and refused a
machine-sized pool in goh's own scripts, but a consumer's gate script still sized itself to the
machine: a Swift app's push gate ran `swift test` with its unbounded parallel flag (13 xctest
workers, the largest share of the 2026-11-17 load-213 peak) and a Python consumer's Makefile ran
xdist's auto count. A share no consumer script reads bounds nothing. This runs the same shape
(lib/worker_share.py MACHINE_SIZED, the one definition) over every repo goh gates, from
`structural.sh --full`, so a new one is refused at the push that adds it.

THE SCOPE: what a gate runs -- `.gatesrc` (GOH_CI_STEPS and the rest), a root Makefile, every
tracked `*.mk`, and the shell and Python under `tools/`, never a test file (`tests/` or `test_*`:
a test spells the shape to check it). A `#` comment line is prose.

THE FIX is the share: `swift test --parallel --num-workers "$(goh_workers)"`, `pytest -n
"$(goh_workers)"`, `make -j"$(goh_workers)"` (lib/worker_share.sh, sourced from $GOH_DIR/lib), or
from a Makefile or Python `python3 "$GOH_DIR/lib/worker_share.py" share`. Under an admitted gate
xdist's auto count already reads PYTEST_XDIST_AUTO_NUM_WORKERS, but outside one (a developer's
`make test`) it is every core again: say the count.

THE ESCAPE: `# workers-ok: <reason>` on the line or on the comment lines directly above it, for a
count that is not a pool (a load average's denominator). A bare marker is red
(checks/_marker_reason.py).

THE SEED: checks/worker_share_seed.json holds, per repo, how many such lines each file had when
this gate was born, so the estate's other repos are not red at their next push for a pool they did
not add. A repo is keyed by its origin's name (a lane worktree's directory is not its name) or, for
a private repo this public tree must not name, by `root:<its first commit>`. A file over its count
is red; one under it is told to lower the entry, and the entry goes when the repo reads the share --
the seed only shrinks (close-the-class).
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from _goh_tmp import goh_tmp  # the ONE temp parent (GOH.FSCHURN3)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(HERE.parent))
from _gitutil import foreign_repo_env  # noqa: E402
from _marker_reason import is_justified, reason_after  # noqa: E402
from worker_share import MACHINE_SIZED  # noqa: E402

from tui.lib import counted, err, ok, warn  # noqa: E402

MARKER = "workers-ok:"
SEED = HERE / "worker_share_seed.json"
SUFFIXES = (".sh", ".bash", ".py", ".mk")


def in_scope(rel: str) -> bool:
    """A gate script: .gatesrc, a root Makefile, any *.mk, shell or Python under tools/."""
    parts = rel.split("/")
    if "tests" in parts or parts[-1].startswith("test_"):
        return False
    if rel in (".gatesrc", "Makefile", "GNUmakefile") or rel.endswith(".mk"):
        return True
    return parts[0] == "tools" and rel.endswith(SUFFIXES)


def tracked(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    ).stdout
    return sorted(rel for rel in out.split("\0") if rel and in_scope(rel))


def _excused(lines: list[str], index: int) -> bool | None:
    """True when a justified marker excuses line `index`, False when a BARE one tries to, None when
    no marker is there: on the line itself or on the comment lines directly above."""
    j = index
    while j >= 0:
        line = lines[j]
        if reason_after(line, MARKER) is not None:
            return is_justified(line, MARKER)
        j -= 1
        if j < 0 or not lines[j].lstrip().startswith("#"):
            return None
    return None


def findings(text: str) -> list[tuple[int, str, bool | None]]:
    """(line number, line, excused) for every machine-sized pool outside a comment line."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") or not MACHINE_SIZED.search(line):
            continue
        out.append((i + 1, line.strip(), _excused(lines, i)))
    return out


def repo_name(repo: Path) -> str:
    """The repo's name as the seed keys it: its origin's last component (a lane worktree's
    directory is not its name), else the work tree's directory."""
    r = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    )
    url = r.stdout.strip().rstrip("/")
    if r.returncode == 0 and url:
        name = url.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        return name.removesuffix(".git")
    return repo.resolve().name


def root_commit(repo: Path) -> str:
    """`root:<sha>` of the repo's first commit: its identity without its name."""
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    )
    roots = r.stdout.split()
    return f"root:{roots[-1]}" if r.returncode == 0 and roots else ""


def allowance(repo: Path, seed: dict[str, dict[str, int]]) -> dict[str, int]:
    """The seed's entry for this repo, by its origin's name or its first commit."""
    for key in (repo_name(repo), root_commit(repo)):
        if key and key in seed:
            return seed[key]
    return {}


def load_seed(path: Path = SEED) -> dict[str, dict[str, int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def judge(repo: Path, seed: dict[str, dict[str, int]]) -> int:
    allowed = allowance(repo, seed)
    red = 0
    counts: Counter[str] = Counter()
    files = tracked(repo)
    for rel in files:
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line, excused in findings(text):
            if excused is True:
                continue
            if excused is False:
                err(f"{rel}:{number}: a bare `{MARKER}` excuses nothing -- say why: {line[:120]}")
                red += 1
                continue
            counts[rel] += 1
            if counts[rel] > allowed.get(rel, 0):
                err(f"{rel}:{number}: a pool sized to the machine: {line[:120]}")
                red += 1
    for rel, n in sorted(allowed.items()):
        if counts[rel] < n:
            warn(
                f"{rel}: {counted(counts[rel], 'machine-sized pool')} where "
                f"checks/worker_share_seed.json allows {n} -- lower the entry in goh (the seed "
                "only shrinks)"
            )
    if red:
        err(
            '  size it from the gate\'s share: `--num-workers "$(goh_workers)"`, '
            '`-n "$(goh_workers)"`, `-j"$(goh_workers)"` (. "$GOH_DIR/lib/worker_share.sh"), '
            'or `python3 "$GOH_DIR/lib/worker_share.py" share`; a count that is not a pool says '
            f"`# {MARKER} <reason>`"
        )
        return 1
    read = counted(len(files), "gate script")
    ok(f"worker share: no gate script sizes a pool to the machine ({read} read)")
    return 0


PROBE = (
    ("tools/gate.sh", "python3 -m pytest -n auto -q\n", 1),
    ("tools/gate.sh", 'python3 -m pytest -n "$(goh_workers)" -q\n', 0),
    ("Makefile", "test:\n\tswift test --parallel\n", 1),
    ("Makefile", "test:\n\tswift test --parallel --num-workers 4\n", 0),
    ("tools/x.sh", "# workers-ok: the denominator of a load reading\nc=$(sysctl -n hw.ncpu)\n", 0),
    ("tools/x.sh", "# workers-ok:\nc=$(sysctl -n hw.ncpu)\n", 1),
    ("tools/tests/test_x.py", 'LINE = "pytest -n auto"\n', 0),
    ("src/build.sh", "make -j\n", 0),
)


def probe() -> int:
    """Each planted file alone in a fresh repo: the verdict it must reach."""
    wrong = 0
    for rel, body, want in PROBE:
        with tempfile.TemporaryDirectory(dir=goh_tmp(), prefix="goh-worker-share-") as d:
            repo = Path(d)
            subprocess.run(
                ["git", "init", "-q", d],
                check=True,
                env=foreign_repo_env(),
                stdin=subprocess.DEVNULL,
            )
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(body)
            subprocess.run(
                ["git", "-C", d, "add", "-A"],
                check=True,
                env=foreign_repo_env(),
                stdin=subprocess.DEVNULL,
            )
            said = io.StringIO()
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                got = judge(repo, {})
        if got != want:
            print(f"probe: {rel} {body!r} gave {got}, wants {want}", file=sys.stderr)
            wrong += 1
    if not wrong:
        ok(f"worker share probe: {len(PROBE)} planted files reach their verdicts")
    return 1 if wrong else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--probe"]:
        return probe()
    repo = Path(argv[0] if argv else ".").resolve()
    return judge(repo, load_seed())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
