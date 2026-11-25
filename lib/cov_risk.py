"""lib/cov_risk.py -- at land, the changed Rust lines the next ship's diff coverage may fail on.

GOH.DIFFCOVLAND. Since GOH.COVOFFLAND the diff coverage (gates/diff_cov.py) runs only in the
ship's instrumented full run, so no land sees it: v0.140.3 went red on 10 of 29 changed lines and
v0.142.0 on 46 of 345 (the new crates/goh/src/toolshell/ arms only the external suite, or nothing,
ran). A land-side verdict was rejected (33 s warm, 167 s cold, false reds without the 642 s
external suite), so this is a WARNING, never a refusal, read from what the last ship measured:

KEEP. After its run the ship's suite step (lib/ship_full_run.py) folds the run's lcov parts into
`<ship dir>/<repo>/covsrc.json`: the commit measured, and per file its executable lines and the
lines the in-process tests (`part-workspace*`, every part not `part-external*`) covered. A line
covered by the external suite alone is the one a land cannot predict and a profile carry may lose.
Only a COMPLETE run is kept (GOH.SUITEREDEARLY: every part begun has its `.ok`): on 2026-11-23 a
run whose external suite was red was, and the next lands carried 482 lines as uncovered that the
next complete run put at 3. `fresh` empties the parts before a run, so none is kept as a later
commit's, and the idle lander's run is kept as the ship's is (`keep_run`): the newest measurement.

PREDICT. A changed line since the last ship (the newest v-tag, the ship's own GOH_CHANGE_BASE) is
AT RISK when, in the kept profile:
  * the line is unchanged since the measured commit, executable there, and no in-process test ran
    it (external suite only, or nothing);
  * it was edited since: no in-process-covered executable line within WINDOW lines of where its
    hunk sat in the measured commit (an untested neighbourhood);
  * its file is new since the measured commit, and not named as test code (`tests.rs`,
    `*_test(s).rs`, `tests/`); a file the measured commit had but the profile lacks is excluded
    from measurement, and the ship never judges it.
A line the repo's diffcov_allow.json names (with its reason) is passed by the ship's diff
coverage, so it is never at risk (GOH.SHIPCOVRISK). No profile, no tag, or a measured commit this
clone lacks is said and skipped. Rust only: the
ship's diff coverage judges `*.rs` alone. Printed by `goh land --check` and at enqueue, and kept
on the land record as `cov_risk`; it never changes either verdict.

CARRY (GOH.DIFFCOVCARRY). The warning alone cost three red ships (v0.140.3, v0.142.0, v0.143.0):
the land went on, and the ship found the same lines after its 15 min suite. So the record also
keeps `carry`, each line the next ship's diff coverage would refuse as `{file, line, text, why,
own}` at the judged tip `head` (lines on `base`, the last ship's tag): the at-risk lines that read
as code (no blank, comment, attribute, `use` or `mod x;` line; an in-file test module's lines are
judged by the ship too: v0.143.0 refused an untaken closure arm in one), less those the profile
saw covered by the external suite (`cov`: the ship's diff coverage counts every part, so those
pass it). `own` marks the lines this land's commits
changed (since its merge base with main): the lander files an item for those, and `goh ship`
refuses before its suite while any carried line is still uncovered (lib/diffcov_carry.py).

UNMEASURED IS NOT UNCOVERED (GOH.DIFFCOVUNMEASURED). A line of a file new since the profile was
never measured: no land can measure Rust coverage, only the ship's run does. The land of
GOH.SELFWALKFILTER (0cfcb5a1) carried 428 such lines, filed GOH.DIFFCOV0CFCB5A1 and refused the
next ship until --allow-diffcov, so every ship carrying new Rust needed the flag. Each carried line
says `measured` (`measured(c)` reads a record kept before the field from its `why`): only a
measured one files an item or refuses a ship; an unmeasured one is noted and left to the ship's
own diff coverage, which judges it after the suite.

The land's diff coverage WARNING (GOH.DIFFCOVLAND), never a verdict: the ship's suite step keeps
its run's per-line coverage sources (`<ship dir>/<repo>/covsrc.json`: executable lines, and those
an in-process `part-*` covered, not `part-external*`), and `goh land --check` and the enqueue name
the Rust lines changed since the last v-tag that no in-process test ran in that profile (external
suite only, no test, an untested neighbourhood, or a file not in it), keeping them as `cov_risk` on
the land record, in well under 5 s with no instrumented build; with `carry` (GOH.DIFFCOVCARRY),
each line the ship's diff coverage would refuse as file, line and text, `own` for the land's own
commits' lines (the profile's `cov` drops lines the external suite covered), `measured: false` for
a line of a file new since the profile (GOH.DIFFCOVUNMEASURED: unmeasured is not uncovered; noted,
never filed or refused). Invoked by `tools/landq.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lib"), str(ROOT / "gates")]
import ship_records  # noqa: E402 -- lib/, on the path from the line above

from lib import gitrun  # noqa: E402
from tui.lib import counted  # noqa: E402

NAME = "covsrc.json"
PARTS = "target/llvm-cov/lcov-parts"  # an instrumented run's parts, in its worktree
WINDOW = 3
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
TESTS = re.compile(r"(^|/)tests?(/|\.rs$)|_tests?\.rs$")
SHOWN = 12  # files named in the warning; the record keeps them all
NEW = "new since the last ship's profile"  # the why of a line no profile measured


def summary_path(repo: str | Path) -> Path:
    """The coverage summary beside a repo's ship records: a file, no record (GOH.STATEDB S2)."""
    return ship_records.ship_dir() / Path(repo).name / NAME


def _git(top: str | Path, *args: str) -> str | None:
    r = gitrun.run_reader(list(args), repo=str(top), timeout=60)
    return r.stdout if r.returncode == 0 else None


def fresh(wt: Path) -> None:
    """WT's parts of an earlier run gone: one that stops before it measures keeps none of them."""
    shutil.rmtree(wt / PARTS, ignore_errors=True)


def keep_run(wt: Path, dest: Path) -> str:
    """The instrumented run in WT kept as the profile DEST (GOH.DIFFCOVLAND): "" or why not. Never
    raises: the profile is a hint, the run's verdict stands."""
    try:
        kept = keep(wt / PARTS, wt, dest)
    except Exception as exc:  # noqa: BLE001 -- said by the caller, never a verdict
        return f"coverage profile not kept: {exc}"
    return "" if kept else "no complete lcov parts, no coverage profile kept"


def keep(parts_dir: Path, top: Path, dest: Path) -> dict | None:
    """Fold PARTS_DIR's lcov parts (measured in checkout TOP) into DEST; the summary, or None when
    there are none or a part was begun (its `.log`) and never finished (no `.ok`): what that part
    covers is unmeasured, never uncovered (GOH.SUITEREDEARLY)."""
    from lcov_merge import parse_part

    if any(not lg.with_suffix(".ok").exists() for lg in parts_dir.glob("part-*.info.log")):
        return None

    sha = (_git(top, "rev-parse", "HEAD") or "").strip()
    parts = sorted(parts_dir.glob("part-*.info"))
    if not sha or not parts:
        return None
    every: dict = defaultdict(int)
    own: dict = defaultdict(int)  # the in-process parts; `cov`, any part (GOH.DIFFCOVCARRY)
    for pf in parts:
        parse_part(str(pf), every)
        if not pf.name.startswith("part-external"):
            parse_part(str(pf), own)
    real = os.path.realpath(top)
    files: dict[str, dict[str, list[int]]] = {}
    for (sf, ln), hits in every.items():
        rel = os.path.relpath(
            os.path.realpath(sf if os.path.isabs(sf) else os.path.join(real, sf)), real
        )
        if rel.startswith(".."):
            continue
        f = files.setdefault(rel, {"exec": [], "own": [], "cov": []})
        f["exec"].append(ln)
        if own.get((sf, ln), 0) > 0:
            f["own"].append(ln)
        if hits > 0:
            f["cov"].append(ln)
    for f in files.values():
        for k in ("exec", "own", "cov"):
            f[k].sort()
    data = {"sha": sha, "files": files}
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(dest)
    return data


def hunks(
    top: str, base: str, head: str = "HEAD"
) -> dict[str, tuple[str, list[tuple[int, int, int, int]]]]:
    """new path -> (old path, [(old start, old count, new start, new count)]) of BASE..HEAD."""
    out = _git(
        top, "-c", "core.quotePath=false", "diff", "-U0", "--no-color", "--no-ext-diff",
        "--no-textconv", "--no-prefix", "-M", "--diff-filter=d",
        base, head, "--", ":(glob)**/*.rs",
    )  # fmt: skip
    import diff_cov  # gates/, on the path above: the one reader of a diff's hunks

    found: dict[str, tuple[str, list]] = {}
    for old, new, hunk in diff_cov.hunks(out or ""):
        found.setdefault(new, (old or "", []))[1].append(hunk)
    return found


def old_line(hunks: list[tuple[int, int, int, int]], ln: int) -> tuple[int, bool]:
    """New line LN's line in the old side: (line, unchanged) -- an edited line maps to its hunk's
    old start."""
    shift = 0
    for a, b, c, d in hunks:
        if d and c <= ln < c + d:
            return a, False
        if (c + d if d else c + 1) <= ln:
            shift = (a + b if b else a + 1) - (c + d if d else c + 1)
        else:
            break
    return ln + shift, True


def allowed(top: str | Path) -> set[tuple[str, str]]:
    """(file, stripped text) of each entry of TOP's diff coverage allowlist (gates/diff_cov.py
    load_allow, its one reader): lines the ship's diff coverage passes, so no land warns of or
    carries them (GOH.SHIPCOVRISK). An unreadable list is the ship's to refuse: none here."""
    import diff_cov  # gates/, on the path above

    try:
        return {(e["file"], e["text"].strip()) for e in diff_cov.load_allow(str(top))[1]}
    except diff_cov.Refused:
        return set()


def _unallowed(top: str, risky: list[tuple[str, int, str, bool]]) -> list:
    """RISKY less the lines TOP's allowlist names."""
    allow = allowed(top)
    texts: dict[str, list[str]] = {}
    for rel in {r[0] for r in risky} & {f for f, _t in allow}:
        try:
            texts[rel] = (Path(top) / rel).read_text(errors="replace").splitlines()
        except OSError:
            texts[rel] = []

    def named(rel: str, ln: int) -> bool:
        lines = texts.get(rel) or []
        return ln <= len(lines) and (rel, lines[ln - 1].strip()) in allow

    return [r for r in risky if not named(r[0], r[1])]


def _judge(top: str, summary: Path) -> tuple[list[tuple[str, int, str, bool]], str, str]:
    """([(file, line, why, carried)], said, tag): each changed Rust line since the last ship at
    risk, and whether the ship's diff coverage would refuse it (not covered by any part); TAG is
    empty when nothing was judged."""
    try:
        data = json.loads(summary.read_text())
    except (OSError, ValueError):
        return [], f"no kept ship coverage profile ({summary})", ""
    sha = data.get("sha", "")
    if not sha or _git(top, "cat-file", "-e", f"{sha}^{{commit}}") is None:
        return [], f"the profile's commit {sha[:8] or '(none)'} is not in this clone", ""
    tag = (
        _git(top, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD") or ""
    ).strip()
    if not tag:
        return [], "no v-tag below HEAD: no last ship to diff from", ""
    changed = hunks(top, tag)
    since = hunks(top, sha)
    files = data.get("files", {})
    risky: list[tuple[str, int, str, bool]] = []
    for rel, (_o, hs) in sorted(changed.items()):
        lines = sorted({ln for _a, _b, c, d in hs for ln in range(c, c + d)})
        if not lines:
            continue
        old_rel, shs = since.get(rel, (rel, []))
        prof = files.get(old_rel or rel)
        if prof is None:
            # Measured then and absent: never measured (an excluded file), so never judged. New
            # since: unknown, unless its name says test code, which the report never carries.
            if old_rel or TESTS.search(rel):
                continue
            risky += [(rel, ln, NEW, True) for ln in lines]
            continue
        exe, own = set(prof["exec"]), set(prof["own"])
        cov = set(prof["cov"]) if "cov" in prof else set()  # a profile kept before GOH.DIFFCOVCARRY
        for ln in lines:
            o, same = old_line(shs, ln)
            near = [o] if same else range(o - WINDOW, o + WINDOW + 1)
            if any(n in exe for n in near) and not any(n in own for n in near):
                carried = not any(n in cov for n in near)
                risky.append((rel, ln, "external suite only, or no test", carried))
    return _unallowed(top, risky), f"since {tag}, profile {sha[:8]}", tag


def at_risk(top: str | Path, summary: Path) -> tuple[list[tuple[str, list[int], str]], str]:
    """([(file, lines, why)], said): the changed Rust lines since the last ship at risk."""
    found, said, _tag = _judge(str(top), summary)
    return at_risk_from(found), said


NOT_CODE = re.compile(r"^(//|/\*|\*(/|\s|$)|#!?\[|(pub(\([\w:]+\))?\s+)?(use\s|mod\s+\w+\s*;))")


def carry(top: str | Path, repo: str | Path, found: list[tuple[str, int, str, bool]]) -> list[dict]:
    """The carried lines of FOUND as code reads them, `own` when this land's commits changed them
    (since the merge base of TOP's HEAD with REPO's): what the next ship's diff coverage refuses."""
    top = str(top)
    main = (_git(str(repo), "rev-parse", "HEAD") or "").strip()
    base = (_git(top, "merge-base", "HEAD", main) or "").strip() if main else ""
    mine = {
        rel: {ln for _a, _b, c, d in hs for ln in range(c, c + d)}
        for rel, (_o, hs) in (hunks(top, base) if base else {}).items()
    }
    texts: dict[str, list[str]] = {}
    out: list[dict] = []
    for rel, ln, why, carried in found:
        if not carried:
            continue
        if rel not in texts:
            try:
                texts[rel] = (Path(top) / rel).read_text(errors="replace").splitlines()
            except OSError:
                texts[rel] = []
        text = texts[rel]
        line = text[ln - 1].strip() if ln <= len(text) else ""
        if not line or NOT_CODE.match(line):
            continue
        out.append({"file": rel, "line": ln, "text": line, "why": why,
                    "own": ln in mine.get(rel, ()), "measured": why != NEW})  # fmt: skip
    return out


def measured(c: dict) -> bool:
    """Carried line C was measured uncovered (a record kept before GOH.DIFFCOVUNMEASURED holds no
    `measured`: its `why` says)."""
    return bool(c.get("measured", c.get("why") != NEW))


def ranges(lines: list[int]) -> str:
    out, start, prev = [], lines[0], lines[0]
    for ln in [*lines[1:], None]:
        if ln is not None and ln == prev + 1:
            prev = ln
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        if ln is not None:
            start = prev = ln
    return ",".join(out)


def report(top: str | Path, repo: str | Path) -> tuple[list[str], dict]:
    """(lines to print, the record's `cov_risk`): with `head`, `base` and `carry` when judged."""
    found, said, tag = _judge(str(top), summary_path(repo))
    risky = at_risk_from(found)
    n = sum(len(ls) for _f, ls, _w in risky)
    rec: dict = {"lines": n, "said": said, "files": {f: ranges(ls) for f, ls, _w in risky}}
    if tag:
        rec["head"] = (_git(str(top), "rev-parse", "HEAD") or "").strip()
        rec["base"] = tag
        rec["carry"] = carry(top, repo, found)
    if not risky:
        return [f"diff coverage risk: none ({said})"], rec
    head = (
        f"diff coverage risk: {counted(n, 'changed Rust line')} no in-process test ran in the last "
        f"ship's profile ({said}) -- the next ship's diff coverage may fail on them:"
    )
    out = [head]
    out += [f"    {f}:{ranges(ls)}  ({w})" for f, ls, w in risky[:SHOWN]]
    if len(risky) > SHOWN:
        out.append(f"    ... and {counted(len(risky) - SHOWN, 'more file')}")
    kept = rec.get("carry") or []
    if hit := [c for c in kept if c["measured"]]:
        mine = sum(1 for c in hit if c["own"])
        out.append(
            f"diff coverage carry: {counted(len(hit), 'line')} no test covered in the profile "
            f"({mine} this land's own) -- recorded; `goh ship` refuses before its suite until "
            "they are covered (GOH.DIFFCOVCARRY)"
        )
    if left := len(kept) - len(hit):
        out.append(
            f"diff coverage carry: {counted(left, 'line')} unmeasured (new since the profile) -- "
            "noted; the ship's diff coverage judges them after its suite (GOH.DIFFCOVUNMEASURED)"
        )
    return out, rec


def at_risk_from(found: list[tuple[str, int, str, bool]]) -> list[tuple[str, list[int], str]]:
    grouped: dict[str, tuple[list[int], str]] = {}
    for rel, ln, why, _c in found:
        grouped.setdefault(rel, ([], why))[0].append(ln)
    return [(rel, ls, why) for rel, (ls, why) in grouped.items()]


def say(top: str | Path, repo: str | Path, emit=print) -> dict:
    """Print the warning through EMIT and return the record; never raises."""
    try:
        lines, rec = report(top, repo)
    except Exception as exc:  # noqa: BLE001 -- a warning never breaks a land
        lines, rec = [f"diff coverage risk: not read ({exc})"], {"said": str(exc)}
    for ln in lines:
        emit(ln)
    return rec
