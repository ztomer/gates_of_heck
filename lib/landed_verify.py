"""Is a subagent's `landed` true? Each sha its report names, checked against GitHub and its CI.

Read by `scripts/agent_audit.py` (tests/test_agent_audit_verify.py). An agent's report is a claim:
published evidence finds 45-76% of agent failures look like success. For each landed report:

1. Its sha-shaped words are looked up in every git repo under the estate root (one
   `git cat-file --batch-check` per repo). A word that resolves is a sha. A word that does not, but
   mixes letters and digits as a sha does, is a claimed sha no repo has: `silent-failure`. An
   all-digit or all-letter word that resolves nowhere is a CI run id or an English word, not a sha.
2. Each sha must be on its repo's GitHub default branch (`compare/<sha>...<branch>` is `identical`
   or `ahead`); HTTP 404 or a sha the branch does not contain is `silent-failure`.
3. Each head among them (a sha no other named sha descends from) needs CI that concluded success:
   the runs for that sha, else the earliest run on the default branch whose head contains it (a
   push of several commits runs CI on the newest only). A failed latest run per workflow is
   `silent-failure`; a run still going, or none yet, is `unverified`. A repo with no GitHub
   workflow at all (goh itself) has its pre-push gate as its CI: on the branch is enough.

Offline-safe: gh missing, the network down, or `offline=True` makes the outcome `unverified` --
never a pass. Only an explicit HTTP 404/422 from GitHub counts as "the remote does not have it".
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SHA_WORD = re.compile(r"\b[0-9a-f]{7,40}\b")
GITHUB = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
CI_OK = {"success", "skipped", "neutral"}
ON_BRANCH = {"identical", "ahead"}  # compare/<sha>...<branch>: the branch contains the sha


class Unreachable(Exception):
    """GitHub could not be asked (no gh, no network, auth): the answer is unknown, not no."""


def _run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, input=stdin if stdin is not None else "", capture_output=True,
                          text=True, timeout=60, check=False)  # fmt: skip


def git_repos(root: Path) -> list[Path]:
    """Repos at depth one or two under `root` (a `.git` DIRECTORY: a worktree's objects are its
    main checkout's, so linked worktrees add nothing)."""
    found = []
    for pattern in ("*/.git", "*/*/.git"):
        found += [p.parent for p in sorted(root.glob(pattern)) if p.is_dir()]
    return found


def locate(words: list[str], repos: list[Path]) -> dict[str, tuple[Path, str]]:
    """word -> (repo, full sha) for every word that names a commit in one of `repos`."""
    found: dict[str, tuple[Path, str]] = {}
    for repo in repos:
        todo = [w for w in words if w not in found]
        if not todo:
            break
        query = "".join(f"{w}^{{commit}}\n" for w in todo)
        r = _run(["git", "-C", str(repo), "cat-file", "--batch-check"], stdin=query)
        for w, line in zip(todo, r.stdout.splitlines(), strict=False):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "commit":
                found[w] = (repo, parts[0])
    return found


def _is_ancestor(repo: Path, a: str, b: str) -> bool:
    return _run(["git", "-C", str(repo), "merge-base", "--is-ancestor", a, b]).returncode == 0


def _slug(repo: Path) -> str | None:
    """`owner/name` of the repo's GitHub remote: `origin` when it is on GitHub, else the first that
    is (ZoneWM's is `v2origin`)."""
    found: dict[str, str] = {}
    for line in _run(["git", "-C", str(repo), "remote", "-v"]).stdout.splitlines():
        parts = line.split()
        m = GITHUB.search(parts[1]) if len(parts) >= 2 else None
        if m:
            found.setdefault(parts[0], f"{m.group(1)}/{m.group(2)}")
    return found.get("origin") or next(iter(found.values()), None)


class Gh:
    """`gh api` with the three answers a check needs: the output, absent (404/422), unreachable."""

    def __init__(self, command: str) -> None:
        self.cmd = shlex.split(command)

    def api(self, path: str, jq: str) -> str | None:
        """The output; None when GitHub says the thing is not there. Raises Unreachable."""
        try:
            r = _run([*self.cmd, "api", path, "--jq", jq])
        except OSError as e:
            raise Unreachable(f"gh could not run ({e})") from e
        if r.returncode == 0:
            return r.stdout
        if re.search(r"HTTP 40[4]|HTTP 422", r.stderr):
            return None
        raise Unreachable(r.stderr.strip()[:200] or f"gh exited {r.returncode}")


@dataclass
class Verdict:
    verdict: str  # verified | silent-failure | unverified | no-shas
    shas: list[str] = field(default_factory=list)
    why: str = ""


def _latest_per_workflow(lines: str) -> list[list[str]]:
    """Rows `id name ... status conclusion`, keeping each workflow's newest run (by id)."""
    best: dict[str, list[str]] = {}
    for line in lines.splitlines():
        row = line.split("\t")
        newer = len(row) >= 4 and row[0].isdigit()
        if newer and (row[1] not in best or int(row[0]) > int(best[row[1]][0])):
            best[row[1]] = row
    return list(best.values())


def _ci(gh: Gh, slug: str, branch: str, repo: Path, head: str) -> tuple[str, str]:
    """(verified | silent-failure | unverified, why) for the CI that covers `head`."""
    runs = gh.api(f"repos/{slug}/actions/runs?head_sha={head}&per_page=20",
                  ".workflow_runs[] | [.id, .name, .status, .conclusion] | @tsv") or ""  # fmt: skip
    rows = _latest_per_workflow(runs)
    where = head[:9]
    if not rows:
        later = gh.api(f"repos/{slug}/actions/runs?branch={branch}&per_page=50",
                       ".workflow_runs[] | [.id, .name, .head_sha, .status, .conclusion] | @tsv") or ""  # fmt: skip
        cover = sorted(
            (int(r[0]), r[2]) for r in (ln.split("\t") for ln in later.splitlines())
            if len(r) >= 5 and r[0].isdigit() and _is_ancestor(repo, head, r[2])
        )  # fmt: skip
        if not cover:
            flows = (gh.api(f"repos/{slug}/actions/workflows", ".total_count") or "").strip()
            if flows == "0":  # no GitHub CI: the pre-push gate is the CI, and the push got through
                return "verified", ""
            return "unverified", f"{slug}: no CI run covers {where} yet"
        first = cover[0][1]
        rows = [[r[0], r[1], r[3], r[4]] for r in (ln.split("\t") for ln in later.splitlines())
                if len(r) >= 5 and r[2] == first]  # fmt: skip
        rows = _latest_per_workflow("\n".join("\t".join(r) for r in rows))
        where = f"{where} (via {first[:9]})"
    for run_id, name, status, conclusion in (r[:4] for r in rows):
        if status != "completed":
            return "unverified", f"{slug}: CI run {run_id} ({name}) on {where} is {status}"
        if conclusion not in CI_OK:
            return "silent-failure", f"{slug}: CI run {run_id} ({name}) on {where}: {conclusion}"
    return "verified", ""


def verify(report: str, repos: list[Path], gh: Gh | None) -> Verdict:
    """The verdict on one landed report. `gh=None` is offline: anything to check is unverified."""
    words = list(dict.fromkeys(SHA_WORD.findall(report.lower())))
    found = locate(words, repos)
    claimed = [w for w in words if w not in found
               and re.search(r"[0-9]", w) and re.search(r"[a-f]", w)]  # fmt: skip
    shas = [found[w][1] for w in words if w in found]
    if claimed:
        where = f"{len(repos)} repo(s) under the estate root"
        return Verdict("silent-failure", shas, f"{', '.join(claimed)}: in none of {where}")
    if not shas:
        return Verdict("no-shas")
    if gh is None:
        return Verdict("unverified", shas, "offline")
    by_repo: dict[Path, list[str]] = {}
    for w in words:
        if w in found:
            by_repo.setdefault(found[w][0], []).append(found[w][1])
    unknown: list[str] = []  # why a repo could not be judged; a silent failure elsewhere wins
    for repo, mine in by_repo.items():
        verdict, why = _repo(gh, repo, mine)
        if verdict == "silent-failure":
            return Verdict(verdict, shas, why)
        if verdict == "unverified":
            unknown.append(why)
    if unknown:
        return Verdict("unverified", shas, "; ".join(unknown))
    return Verdict("verified", shas)


def _repo(gh: Gh, repo: Path, mine: list[str]) -> tuple[str, str]:
    """(verified | silent-failure | unverified, why) for the shas one repo holds."""
    slug = _slug(repo)
    if slug is None:
        return "unverified", f"{repo.name}: origin is not on GitHub"
    try:
        branch = (gh.api(f"repos/{slug}", ".default_branch") or "").strip()
        if not branch:
            return "silent-failure", f"{slug}: GitHub has no such repo"
        for sha in mine:
            status = (gh.api(f"repos/{slug}/compare/{sha}...{branch}", ".status") or "").strip()
            if status not in ON_BRANCH:
                return "silent-failure", f"{sha[:9]} not on {slug} {branch}"
        heads = [s for s in mine if not any(o != s and _is_ancestor(repo, s, o) for o in mine)]
        for head in heads:
            verdict, why = _ci(gh, slug, branch, repo, head)
            if verdict != "verified":
                return verdict, why
    except Unreachable as e:
        return "unverified", f"{slug}: GitHub unreachable: {e}"
    return "verified", ""
