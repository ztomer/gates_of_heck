"""lib/cov_newest.py -- what the newest complete instrumented run measured, asked at a ship's HEAD.

GOH.SUITEREDEARLY. `goh ship`'s preflight (lib/diffcov_carry.py) names what its suite's diff
coverage would refuse, and only a MEASUREMENT answers that: the profile the newest complete
instrumented run left (lib/cov_risk.py `keep_run`: the ship's own run or the idle lander's, green
or red). A line unchanged since the measured commit reads as it did there:

    verdict(rel, line)   at HEAD's numbering: COVERED (a part ran it), UNCOVERED (executable, no
                         part ran it), NOT_JUDGED (no executable line there, or a file left out of
                         the measurement: the ship's diff coverage has no record of it either), or
                         "" -- unmeasured: changed since, its file new since, or no profile
    uncovered(tag)       the Rust lines changed in TAG..HEAD it reads UNCOVERED: `(file, line,
                         text)`, what the suite would print after its 10-20 min
    unmeasured(tag)      how many lines changed in TAG..HEAD it has no word of, per file: the
                         suite's to judge, never a refusal (GOH.DIFFCOVUNMEASURED)

A test landed since the measured commit may cover a line it reads UNCOVERED; the next complete run
says so, and nothing else does. Pinned by tests/test_cov_newest.py.

What the newest complete instrumented run (the ship's or the idle lander's) measured of a line at a
ship's HEAD (GOH.SUITEREDEARLY): covered, uncovered, no executable line, or unmeasured. The ship's
preflight refuses on the changed lines it measured uncovered, clears a land's carried line only by
it, and names the changed lines no run has measured. Invoked by `lib/diffcov_carry.py`. Pinned by
`test_cov_newest.py`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cov_risk  # lib/: where the profile is kept, and the one -U0 hunk reader
import gitrun  # lib/: git under a deadline, aimed at its repository

COVERED, UNCOVERED, NOT_JUDGED = "covered", "uncovered", "not judged"


def _git(top: Path | str, *args: str) -> str | None:
    r = gitrun.run_reader(list(args), repo=str(top), timeout=60)
    return r.stdout if r.returncode == 0 else None


class Newest:
    """The newest kept profile of REPO, read at HEAD."""

    def __init__(self, repo: Path, head: str) -> None:
        self.repo, self.head, self.data = repo, head, {}
        try:
            self.data = json.loads(cov_risk.summary_path(repo).read_text())
        except (OSError, ValueError):
            self.data = {}
        self.sha = self.data.get("sha") or ""
        if self.sha and _git(repo, "cat-file", "-e", f"{self.sha}^{{commit}}") is None:
            self.sha = ""  # measured in a history this clone lacks
        self.since = cov_risk.hunks(str(repo), self.sha, head) if self.sha else {}
        self.there: dict[str, bool] = {}
        self.lines: dict[str, tuple[set[int], set[int]]] = {}

    def verdict(self, rel: str, ln: int) -> str:
        if not self.sha:
            return ""
        old_rel, hs = self.since.get(rel, (rel, []))
        if not old_rel:
            return ""  # its file is new since the measured commit
        old, same = cov_risk.old_line(hs, ln)
        if not same:
            return ""
        if old_rel not in self.there:
            at = f"{self.sha}:{old_rel}"
            self.there[old_rel] = _git(self.repo, "cat-file", "-e", at) is not None
        if not self.there[old_rel]:
            return ""
        prof = (self.data.get("files") or {}).get(old_rel)
        if prof is None or "cov" not in prof:  # kept before GOH.DIFFCOVCARRY: no word of any part
            return NOT_JUDGED if prof is None else ""
        if old_rel not in self.lines:
            self.lines[old_rel] = (set(prof.get("exec") or ()), set(prof["cov"]))
        executable, cov = self.lines[old_rel]
        return COVERED if old in cov else UNCOVERED if old in executable else NOT_JUDGED

    def _changed(self, tag: str) -> list[tuple[str, int]]:
        found = cov_risk.hunks(str(self.repo), tag, self.head)
        return [(rel, ln) for rel, (_o, hs) in sorted(found.items())
                for ln in sorted({n for _a, _b, c, d in hs for n in range(c, c + d)})]  # fmt: skip

    def uncovered(self, tag: str) -> list[tuple[str, int, str]]:
        hit = [(r, n) for r, n in self._changed(tag) if self.verdict(r, n) == UNCOVERED]
        allow = cov_risk.allowed(self.repo) if hit else set()
        texts = {r: (_git(self.repo, "show", f"{self.head}:{r}") or "").splitlines()
                 for r in {r for r, _n in hit}}  # fmt: skip
        rows = [(r, n, texts[r][n - 1].strip() if n <= len(texts[r]) else "") for r, n in hit]
        return [row for row in rows if (row[0], row[2]) not in allow]

    def unmeasured(self, tag: str) -> dict[str, list[int]]:
        """Per file, the lines changed in TAG..HEAD with no word, less those that read as no code
        (blank, comment, attribute, `use`) and files named as test code."""
        out: dict[str, list[int]] = {}
        for rel, ln in self._changed(tag):
            if not cov_risk.TESTS.search(rel) and self.verdict(rel, ln) == "":
                out.setdefault(rel, []).append(ln)
        for rel in list(out):
            text = (_git(self.repo, "show", f"{self.head}:{rel}") or "").splitlines()
            out[rel] = [n for n in out[rel] if n <= len(text) and _code(text[n - 1].strip())]
        return {rel: lns for rel, lns in out.items() if lns}


def _code(line: str) -> bool:
    return bool(line) and not cov_risk.NOT_CODE.match(line)
