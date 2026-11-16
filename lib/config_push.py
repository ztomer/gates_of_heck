"""The push gate's floor rule: a push that changes only scanner floors re-runs the structural gate.

GOH.CFGPUSHSCOPE (owner, 2026-11-16: "prioritize GOH.CFGPUSHSCOPE before the 137-floor sweep"). A
consumer push whose whole diff was one `.gatesrc` line -- a scanner floor,
`GOH_DEAD_PORT_MIN_FILES=57` -- ran the repo's whole push gate: the dead-port floor sweep pushed
18 repos in ~100 min, 17 s to 1002 s each (scripts 1002 s, game_asset_factory 894 s, koffee_big
610 s) at load 44-140. A floor is read by one reader, the structural pipeline's last step
(`crate::subject`, judged at full scope), so every other step of that gate judged bytes it had
judged on the parent.

    config_push.py decide --root DIR --base SHA [--goh GOH]   # `config BASE` or `full REASON`

gates/push_gate.sh asks `decide` for each pushed commit with BASE the remote's tip of the ref, and
runs `gates/structural.sh --full` alone (which judges every floor, and every key's single
assignment) only when ALL hold; anything else is the full gate, and the push says why:

  * BASE is the remote's tip (a new branch has none) and an ancestor of the commit;
  * BASE has a green push verdict: the push gate appends `<sha> <how>` to `goh-pushed` in the
    common git dir after every gated ref passed and none moved (push_record_pushed in
    lib/push_runs.sh), so a red push and a push no gate ran (`--no-verify`, another machine) are
    both absent. The record is the tree's, not the gates': a commit the remote already holds is not
    re-gated under newer gates (gates/push_gate.sh, "WHAT IS NOT GATED"), and BASE is exactly such
    a commit, so the floor push certifies what the remote GAINS -- the floor lines -- under today's
    structural gate;
  * the diff BASE..commit is `.gatesrc` and nothing else;
  * every line that diff adds or removes is blank, a comment, or `KEY=<digits>` with an optional
    trailing comment, KEY a scanner floor: one of `crate::subject`'s keys, read from the gates'
    own crates/goh/src/subject.rs (one list, so a new scanner's floor joins by being declared).
    A value in quotes, an `export`, a substitution or any other key (one that turns a layer on,
    GOH_CI_STEPS, GOH_MAX_LINES) is the full gate: the gate itself reads those;
  * no tracked file but `.gatesrc` names a changed key: a file that spells it may read it (a
    repo's own floor check, a test that pins its value), and only the full gate runs that file.

A floor push that passes records its commit too, so successive floor pushes chain. Pinned by
tests/test_push_config_reuse.py, each refusal by the reason it names.
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
import os
import re
import subprocess
import sys
from pathlib import Path

GATESRC = ".gatesrc"
PUSHED = "goh-pushed"
SUBJECTS = Path("crates") / "goh" / "src" / "subject.rs"
SUBJECT_KEY = re.compile(r'^\s*key:\s*"(GOH_[A-Z0-9_]+)",\s*$', re.MULTILINE)
FLOOR_LINE = re.compile(r"^(GOH_[A-Z0-9_]+)=([0-9]+)(?:\s+#.*)?\s*$")
INERT_LINE = re.compile(r"^\s*(?:#.*)?$")
ZERO = "0" * 40
GOH_ROOT = Path(__file__).resolve().parent.parent


def floor_keys(goh: Path) -> set[str]:
    """Every scanner floor key `crate::subject` declares, from the gates' own source."""
    return set(SUBJECT_KEY.findall((goh / SUBJECTS).read_text(encoding="utf-8")))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def pushed(root: Path, sha: str) -> bool:
    """True when the push gate recorded sha green in `goh-pushed` (common git dir)."""
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    try:
        lines = (Path(common) / PUSHED).read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return any(ln.split()[:1] == [sha] for ln in lines)


def changed_lines(root: Path, base: str, head: str) -> list[str]:
    """The lines the diff base..head adds or removes in .gatesrc, without their +/- mark."""
    out = _git(root, "diff", "--no-color", "--no-ext-diff", "-U0", base, head, "--", GATESRC)
    return [
        line[1:]
        for line in out.stdout.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]


def decide(root: Path, base: str, goh: Path, head: str = "HEAD") -> tuple[str, str]:
    """("config", BASE) when head changes only scanner floors since a push-gated BASE; ("full",
    REASON) for a .gatesrc change the rule refuses -- the reason is what the push says -- and
    ("quiet", REASON) for a push it does not concern (a new ref, a diff without .gatesrc)."""
    if not base or base == ZERO:
        return "quiet", "no earlier pushed tree of this ref to compare with (a new ref)"
    sha = _git(root, "rev-parse", "--verify", "--quiet", f"{head}^{{commit}}").stdout.strip()
    if _git(root, "merge-base", "--is-ancestor", base, sha).returncode != 0:
        return "quiet", f"the remote's {base[:8]} is not an ancestor of {sha[:8]}"
    diff = _git(root, "diff", "--no-renames", "--name-only", base, sha)
    paths = [p for p in diff.stdout.splitlines() if p]
    if diff.returncode != 0 or GATESRC not in paths:
        return "quiet", f"it does not change {GATESRC} since {base[:8]}"
    if not pushed(root, base):
        return "full", f"the remote's {base[:8]} has no green push verdict ({PUSHED})"
    other = next((p for p in paths if p != GATESRC), None)
    if other is not None:
        return "full", f"{other} is not {GATESRC} (the diff since {base[:8]})"
    try:
        floors = floor_keys(goh)
    except OSError as e:
        return "full", f"the scanner floors could not be read ({e})"
    keys: set[str] = set()
    for line in changed_lines(root, base, sha):
        m = FLOOR_LINE.match(line)
        if m and m.group(1) in floors:
            keys.add(m.group(1))
        elif not INERT_LINE.match(line):
            return "full", f"`{line.strip()[:60]}` is not a scanner floor line ({GATESRC})"
    for key in sorted(keys):
        hits = _git(root, "grep", "-l", "-F", "-e", key, sha, "--", ".", f":(exclude){GATESRC}")
        named = [h.split(":", 1)[1] for h in hits.stdout.splitlines() if ":" in h]
        if named:
            return "full", f"{named[0]} names {key}, so it may read it"
    return "config", base


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("decide")
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--base", required=True)
    p.add_argument("--goh", type=Path, default=GOH_ROOT)
    a = ap.parse_args(argv)
    print(" ".join(decide(a.root.resolve(), a.base, a.goh.resolve())))  # verb, then base or reason
    return 0


if __name__ == "__main__":
    sys.exit(main())
