"""goh's self-walk: every consumer scanner in the registry, run over goh itself with an EMPTY seed.

THE CLASS (GOH.CLASS.SELF): goh breaks the rules it enforces on consumers -- a hedged plural, a file
at the line cap, a hand-rolled TUI mark, a changelog heading no tag names. Each was found by hand,
one at a time, because goh's own run judges goh THROUGH its seeds, its grace and its opt-outs, and
a consumer meets none of those. The walk removes all three:

  * the tree is a clone of this checkout AS IT STANDS (HEAD plus the working-tree change), with
    every root seed and allowlist (`*_seed.json`, `*_allow.json`, `*_baseline.json`) deleted and
    an empty private store (`GOH_SEED_DIR`), so a seeded site is a finding again;
  * `GOH_GRACE=over`, so a shape still inside its week of grace is judged;
  * `GOH_TAIL` unbounded, so a long finding list is counted whole.
  * run as a hosted runner runs it (`walk_env`): a HOME of its own holding none of the owner's
    state, and `GITHUB_ACTIONS=true`, so the verdict is the tree's, the same here and on CI.

THE REGISTRY is the binary's (`goh shape-diff --registry`, the shape table's own `registry()`):
every structural step and every subject scanner. A step is read from `goh structural --full
--keep-going`; a subject scanner named `goh <cmd>` that is no structural step is run as that
command. A scanner added to the registry is walked with no edit here.

THE SEED, `tests/self_walk_seed.json`, holds goh's own violations per scanner, counted as the
distinct `path:line` sites the scanner printed. It only shrinks: a scanner that finds more, finds
fewer (lower the count), or finds none (delete the entry) is red, and so is an entry for no scanner.
Done is an empty `findings`. Its other sections are not debt: `enable` turns an opt-in scanner on
for the walk, `not_run` names a scanner with nothing to judge in goh (with why), `unjudged` one
that ran and said it could not judge here.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import suite_home
from timing_bounds import patience

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED = REPO_ROOT / "tests" / "self_walk_seed.json"
# The seed and allowlist files a scanner reads at a repo root: deleted from the walked tree.
SEED_FILE = re.compile(r"^[a-z0-9_]+_(seed|allow|baseline)\.json$")
# A step's announce line: `<mark> <label> (<=Ns)`, the label as structural_steps.txt names it
# (`file length <= 500` is the `file length` step).
ANNOUNCE = re.compile(r"^\S+ (?P<label>.+?) \(≤\d+s\)$")
# A site a scanner names: `path:line` (shell lint's `path#SC1234:line` included).
SITE = re.compile(r"(?<![\w./#-])(?=[\w.#-]*[./])([\w.][\w./#-]*):(\d+)\b")
UNJUDGED = re.compile(r"not judged|UNSCOPED", re.IGNORECASE)
WALK_S = 600


@dataclass
class Result:
    """One scanner's outcome over the walked tree."""

    ran: bool = False
    failed: bool = False
    unjudged: bool = False
    sites: set[str] = field(default_factory=set)

    @property
    def count(self) -> int:
        """Its findings: the sites it named, or one for a red that named none."""
        return len(self.sites) or int(self.failed)


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Its output as ONE stream: a step announces on stdout and reports on stderr, and two pipes
    read apart would put every finding after the last announce."""
    return subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, stdin=subprocess.DEVNULL, check=False,
                          timeout=patience(WALK_S))  # fmt: skip


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=True, timeout=patience(120))  # fmt: skip
    return out.stdout


def registry(goh: Path) -> list[dict]:
    """The scanner registry, from the binary (`goh shape-diff --registry`)."""
    out = subprocess.run([str(goh), "shape-diff", "--registry"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=True, timeout=patience(60))  # fmt: skip
    return json.loads(out.stdout)


def load_seed(path: Path = SEED) -> dict:
    return json.loads(path.read_text())


def walked_tree(dest: Path, seed: dict, plants: dict[str, str] | None = None) -> Path:
    """A clone of this checkout as it stands, seeds gone, opt-ins on, `plants` written in."""
    clone = ["git", "clone", "-q", "--shared", str(REPO_ROOT), str(dest)]
    subprocess.run(clone, check=True, capture_output=True, stdin=subprocess.DEVNULL,
                   timeout=patience(120))  # fmt: skip
    _git(dest, "checkout", "-q", "-B", "self-walk")
    diff = subprocess.run(["git", "-C", str(REPO_ROOT), "diff", "HEAD", "--binary"],
                          capture_output=True, check=True, stdin=subprocess.DEVNULL,
                          timeout=patience(120)).stdout  # fmt: skip
    if diff:
        subprocess.run(["git", "-C", str(dest), "apply", "--whitespace=nowarn"], input=diff,
                       check=True, capture_output=True, timeout=patience(120))  # fmt: skip
    # The untracked files too, as `structural --full` lists them: the WHOLE tree, never a part of it
    # (a part is the fixture-copy class, tests/fixture_copy_lint.py), carried by tar.
    others = _git(REPO_ROOT, "ls-files", "-z", "--others", "--exclude-standard")
    if others:
        pack = subprocess.run(["tar", "-C", str(REPO_ROOT), "--null", "-T", "-", "-cf", "-"],
                              input=others.encode(), capture_output=True, check=True,
                              timeout=patience(120)).stdout  # fmt: skip
        subprocess.run(["tar", "-C", str(dest), "-xf", "-"], input=pack, capture_output=True,
                       check=True, timeout=patience(120))  # fmt: skip
    for p in dest.iterdir():
        if p.is_file() and SEED_FILE.match(p.name):
            p.unlink()
    enable = "".join(f"{line}\n" for line in seed.get("enable", {}).values())
    if enable:
        with (dest / ".gatesrc").open("a") as rc:
            rc.write("\n# goh's self-walk: every opt-in scanner on (tests/self_walk_seed.json)\n")
            rc.write(enable)
    for rel, text in (plants or {}).items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text(text)
    _git(dest, "add", "-A")
    _git(dest, "-c", "user.name=walk", "-c", "user.email=walk@walk", "commit", "-q", "--no-verify",
         "--allow-empty", "-m", "the tree under walk")  # fmt: skip
    return dest


def walk_env(base: dict[str, str], place: Path) -> dict[str, str]:
    """`base` run as a hosted runner runs it, on any machine: an empty private store, a HOME of the
    walk's own under `place` holding none of the owner's state, and `GITHUB_ACTIONS=true`.

    The suite's home links the owner's ~/Projects (the umbrella queue `one roadmap` reads, the
    estate the corpus sweep plants into) and ~/.claude/skills (goh's GOH_SKILLS_ROOT), which a
    hosted runner has no copy of: `skills corpus` ran here and not there, and `one roadmap` judged
    its queue rule here and not there, so the walk was green on this Mac and red on CI
    (GOH.SELFWALKCI, house rule 17). With the owner's state gone, the estate sweep says NOT
    APPLICABLE only where the run is hosted (lib/not_applicable.py), so the walk says it is,
    here as there: its verdict is the TREE's, and the one judge of the estate stays goh's push."""
    store = place / "seed-store"
    store.mkdir(parents=True, exist_ok=True)
    home = suite_home.furnish(place / "home", reads=())
    return {**base, "HOME": str(home), "GITHUB_ACTIONS": "true", "GOH_SEED_DIR": str(store),
            "GOH_GRACE": "over", "GOH_TAIL": "1000000"}  # fmt: skip


def _names(announced: str, label: str) -> bool:
    return announced == label or announced.startswith(f"{label} ")


def parse_structural(text: str, labels: list[str]) -> dict[str, Result]:
    """Each registry label's section of a `structural --full --keep-going` log."""
    by_len = sorted(labels, key=len, reverse=True)
    out: dict[str, Result] = {}
    cur: Result | None = None
    announced = ""
    for line in text.splitlines():
        m = ANNOUNCE.match(line)
        if m:
            announced = m.group("label")
            name = next((lb for lb in by_len if _names(announced, lb)), None)
            cur = out.setdefault(name, Result()) if name else None
            if cur is not None:
                cur.ran = True
            continue
        if cur is None:
            continue
        if f"structural: {announced} failed" in line:
            cur.failed = True
        if UNJUDGED.search(line):
            cur.unjudged = True
        cur.sites.update(m.group(0) for m in SITE.finditer(line))
    return out


def lint(goh: Path, tree: Path, env: dict[str, str]) -> None:
    """`goh shell-lint` over `tree`: its verdicts recorded in `env`'s store before another walk
    reads them (the walk's red, if any, is its own run's to report)."""
    _run([str(goh), "shell-lint"], tree, env)


def shellchecked(log: Path, root: Path) -> dict[str, Counter[str]]:
    """Each shell file a walk under `root` handed shellcheck, by its path in the tree: how many
    times in each tree (`goh`, `planted`). A `--version` probe is no lint."""
    out: dict[str, Counter[str]] = {}
    lines = log.read_text().splitlines() if log.exists() else []
    for line in lines:
        rel = Path(line)
        if not rel.is_absolute() or not rel.is_relative_to(root):
            continue
        tree, *rest = rel.relative_to(root).parts
        out.setdefault("/".join(rest), Counter())[tree] += 1
    return out


def walk(goh: Path, tree: Path, env: dict[str, str], reg: list[dict]) -> dict[str, Result]:
    """Every registry scanner's Result over `tree`."""
    labels = [r["scanner"] for r in reg]
    log = _run([str(goh), "structural", "--full", "--keep-going"], tree, env)
    (tree.parent / f"{tree.name}.log").write_text(log.stdout)  # the evidence, beside the tree
    results = parse_structural(log.stdout, labels)
    for label in labels:
        if label in results or not label.startswith("goh "):
            continue
        done = _run([str(goh), *label.split()[1:]], tree, env)
        r = results[label] = Result(ran=True, failed=done.returncode != 0)
        for line in done.stdout.splitlines():
            r.unjudged |= bool(UNJUDGED.search(line))
            r.sites.update(m.group(0) for m in SITE.finditer(line))
    return results


def judge(labels: list[str], results: dict[str, Result], seed: dict) -> list[str]:
    """Every way the walk disagrees with the seed; empty is green."""
    reds: list[str] = []
    findings, not_run, unjudged = seed["findings"], seed["not_run"], seed["unjudged"]
    for section in ("findings", "not_run", "unjudged", "enable"):
        for name in seed.get(section, {}):
            if name not in labels:
                reds.append(f"{section}: `{name}` is no scanner in the registry: delete it")
    for name, entry in findings.items():
        if not str(entry.get("why", "")).strip():
            reds.append(f"findings: `{name}` carries no why")
    for name in labels:
        r = results.get(name, Result())
        if not r.ran:
            if name not in not_run:
                reds.append(f"`{name}` did not run over goh: enable it, or say why in not_run")
            continue
        if name in not_run:
            reds.append(f"`{name}` ran over goh: its not_run entry is stale, delete it")
        if r.unjudged != (name in unjudged):
            reds.append(
                f"`{name}` "
                + (
                    "said it could not judge goh: name why in unjudged"
                    if r.unjudged
                    else "judged goh: its unjudged entry is stale"
                )
            )
        seeded = findings.get(name, {}).get("count", 0)
        if r.count > seeded:
            reds.append(
                f"`{name}` finds {r.count} over goh, seeded {seeded}: a new violation "
                f"(fix it; goh obeys its own scanners): {sorted(r.sites)[:5]}"
            )
        elif r.count < seeded:
            reds.append(
                f"`{name}` finds {r.count} over goh, seeded {seeded}: lower the seed"
                + (" (delete the entry)" if r.count == 0 else "")
            )
    return reds
