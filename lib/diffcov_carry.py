"""lib/diffcov_carry.py -- a land's uncovered changed lines, carried to the next ship.

GOH.DIFFCOVCARRY.

THE COST. Since GOH.DIFFCOVLAND a land with uncovered changed Rust lines lands with a warning
(lib/cov_risk.py) and the ship's diff coverage finds the same lines after its 15-16 min suite:
v0.140.3, v0.142.0 and v0.143.0 each went red there, each a red ship, a fix lane and a full
re-ship. The land stays fast (no measurement is added to it); its warning is acted on instead.

AT THE LAND. The enqueue keeps cov_risk's `carry` on the request (each line `{file, line, text,
why, own}` at the judged tip `head`), and the landing record carries it. When the land lands with
an `own` carried line (its commits changed it), `file_item` files `<PREFIX>.DIFFCOV<short sha>`
through `loopctl file --json` (status todo, phase SPD, regression, class_of GOH.DIFFCOVCARRY) and
the record names it as `diffcov_item`, so a fix lane starts minutes after the land, not after the
ship. A line an earlier land already carried is that land's item, never filed twice.

AT THE SHIP. `refusal` is `goh ship`'s preflight, before the suite, read in seconds. It refuses on
every carried line of the repo's landed records since the base tag (landing on `tag..HEAD`) still
uncovered, and on every changed line the newest complete run measured uncovered whether a land
carried it or not (lib/cov_newest.py: the ship's own run or the idle lander's, green or red),
naming `file:line  text`, unless --allow-diffcov REASON. A carried line no longer counts when:
  * it is gone: its file is absent at the ship's HEAD, or `git diff -U0 head..HEAD` changed it
    (an edited line is the editing land's own judgment, carried there if still at risk);
  * that newest run measured it, unchanged since, as covered or as no executable line.
ONLY A MEASUREMENT CLEARS ONE (GOH.SUITEREDEARLY). The land of the item filed for it ran plain and
measured nothing, and a later land's judgment that lists no such line is a reading of the same old
profile: both cleared lines until 2026-11-25, and the suite found them. An item that landed since
is said in the refusal, which --allow-diffcov answers naming the test.
A changed line no run has measured is NAMED and never refused (`unmeasured`, below).
A line the repo's diffcov_allow.json names never counts: the ship's diff coverage passes it, so
neither the land carries it nor the preflight refuses it (GOH.SHIPCOVRISK).
Only MEASURED lines count (GOH.DIFFCOVUNMEASURED, cov_risk.measured): a line of a file new since
the profile was never measured by any land, so it files no item and refuses no ship; the ship's
own diff coverage judges it after the suite.
Pinned by tests/test_diffcov_carry.py.

A land's carried uncovered lines to the next ship (GOH.DIFFCOVCARRY): a landing with its own
carried lines files `<PREFIX>.DIFFCOV<sha>` through `loopctl file` (answering a closed class and an
open carry item's repeat refusal), and `goh ship`'s preflight refuses in seconds, before the suite,
while a changed line since the base tag is uncovered by a land's carry or by the newest complete
run (not deleted or edited, not measured covered since: GOH.SUITEREDEARLY), unless
`--allow-diffcov REASON`; only MEASURED lines file or refuse (GOH.DIFFCOVUNMEASURED), an unmeasured
one is named there and the ship's own diff coverage's to judge. Invoked by `tools/landq.py`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cov_newest  # lib/: what the newest complete run measured
import gitrun  # lib/: git under a deadline, aimed at its repository
import land_records  # lib/: the land records' one door (GOH.STATEDB)
import state_paths

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import counted  # the root, for the tui lib

CLASS = "GOH.DIFFCOVCARRY"
SHOWN = 15  # lines named in the refusal; the count is all of them
TRIES = 6  # loopctl refusals answered (a closed class, each open carry item it reads as a repeat)
DUP = re.compile(r"repeats open item (\S+) ")


def _git(top: Path | str, *args: str) -> str | None:
    r = gitrun.run_reader(list(args), repo=str(top), timeout=60)
    return r.stdout if r.returncode == 0 else None


def item_id(outcome: dict, item: str) -> str:
    """`<PREFIX>.DIFFCOV<short sha>`: the landed item's prefix (GOH when it names none)."""
    prefix = item.split(".", 1)[0] if "." in item else "GOH"
    return f"{prefix}.DIFFCOV{(outcome.get('sha') or '')[:8].upper()}"


def _repo_name(repo: str) -> str:
    root = Path(os.environ.get("LOOPCTL_ROOT") or state_paths.repos_root())
    try:
        return str(Path(repo).resolve().relative_to(root.resolve()))
    except ValueError:
        return Path(repo).name


def _item(iid: str, outcome: dict, item: str, own: list[dict]) -> dict:
    named = "; ".join(f"{c['file']}:{c['line']} `{c['text']}`" for c in own[:SHOWN])
    more = f" (and {len(own) - SHOWN} more)" if len(own) > SHOWN else ""
    sha = (outcome.get("sha") or "")[:12]
    return {"id": iid, "repo": _repo_name(outcome.get("repo") or ""), "status": "todo",
            "phase": "SPD", "regression": True, "depends": [],
            "source": f"goh lander's diff coverage carry (lib/diffcov_carry.py) at {sha}",
            "item": f"The land of {item or '(no item)'} at {sha} left "
            f"{counted(len(own), 'changed Rust line')} no test covered in the last ship's "
            f"coverage profile: {named}{more}. `goh ship` refuses in preflight until they are "
            "covered; landing this item clears them.",
            "metric": "changed Rust lines carried uncovered from a land to the next ship's "
            "preflight (goh ship's diff coverage carry)",
            "predicted": f"{len(own)} -> 0: each line covered by a test, or deleted",
            "verify_after": {"ships": 1},
            "done_state": "a test runs each named line (or the line is gone), landed under this "
            "item, and the next ship's diff coverage is green on them",
            "class_of": CLASS}  # fmt: skip


def file_item(outcome: dict, req: dict | None, loopctl: str, item: str = "") -> dict:
    """At a landing: file the item for the land's own carried lines; `{"diffcov_item": {...}}`
    naming it (or why not), or {} when the land carried none. Never raises."""
    import cov_risk  # lib/: what a measured carried line is

    carried = ((req or {}).get("cov_risk") or {}).get("carry") or []
    own = [c for c in carried if c.get("own") and cov_risk.measured(c)]
    if outcome.get("state") != "landed" or not own:
        return {}
    iid = item_id(outcome, item)
    if not os.path.isfile(loopctl):
        return {"diffcov_item": {"id": iid, "said": f"not filed: no loopctl at {loopctl}"}}
    import reuse_fault  # lib/: `loopctl file --json` under GOH_LOOP_QUEUE (one funnel)

    fields = _item(iid, outcome, item, own)
    try:
        for _ in range(TRIES):
            code, said = reuse_fault._file(loopctl, fields)
            if code and "is done, so its check missed" in said:  # the class closed: own instance
                fields.update(class_of="none", class_why=f"{CLASS} is done; a land's own carry")
            elif code and (dup := DUP.search(said)):  # another land's carry: its own lines
                why = f"another land's own uncovered lines; this is {iid}'s land"
                fields.setdefault("not_dup_of", {})[dup.group(1)] = why
            else:
                break
    except Exception as e:  # noqa: BLE001 -- filing is never the reason a landing is lost
        code, said = 1, str(e)
    if code == 0 or f"{iid} already exists" in said:
        return {"diffcov_item": {"id": iid, "said": "filed" if code == 0 else "already filed"}}
    return {"diffcov_item": {"id": iid, "said": f"not filed: {said[-300:]}"}}


def _landed(q: Path, repo: Path, order: dict[str, int]) -> list[dict]:
    """The repo's landed records on ORDER (tag..HEAD), oldest landing first."""
    real = os.path.realpath(repo)
    which = ("state", "mode", "repo", "sha", "landed")
    at: dict[str, int] = {}
    for run, rec in land_records.records(q, keys=which):
        sha = rec.get("landed") or rec.get("sha") or ""
        mine = sha in order and os.path.realpath(rec.get("repo") or "") == real
        if mine and rec.get("state") == "landed" and rec.get("mode", "land") == "land":
            at[run] = order[sha]
    recs = land_records.records(q, keys=(*which, "item", "enqueued", "cov_risk", "diffcov_item"),
                                runs=at)  # fmt: skip
    return [rec for _, rec in sorted(recs, key=lambda kv: -at[kv[0]])]


class _Tree:
    """The ship's HEAD as the re-judge reads it: each file's lines, each (from, file) diff."""

    def __init__(self, repo: Path, head: str) -> None:
        self.repo, self.head = repo, head
        self.texts: dict[str, list[str] | None] = {}
        self.diffs: dict[tuple[str, str], list[tuple[int, int, int, int]] | None] = {}

    def text(self, rel: str) -> list[str] | None:
        if rel not in self.texts:
            got = _git(self.repo, "show", f"{self.head}:{rel}")
            self.texts[rel] = None if got is None else got.splitlines()
        return self.texts[rel]

    def where(self, frm: str, rel: str, ln: int, text: str) -> int | None:
        """Line LN (reading TEXT) of REL at FRM, at HEAD: None when the file is gone or a hunk
        changed it; FRM not in this clone, TEXT's first line at HEAD, if any."""
        if self.text(rel) is None:
            return None
        if (frm, rel) not in self.diffs:
            import cov_risk  # lib/: the one -U0 hunk reader's pattern

            out = _git(self.repo, "diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
                       frm, self.head, "--", rel)  # fmt: skip
            hunks = None if out is None else [
                tuple(int(m.group(i) or 1) for i in (1, 2, 3, 4))
                for m in map(cov_risk.HUNK.match, out.splitlines()) if m
            ]  # fmt: skip
            self.diffs[(frm, rel)] = hunks
        hunks = self.diffs[(frm, rel)]
        if hunks is None:
            return next(
                (i + 1 for i, t in enumerate(self.text(rel) or []) if t.strip() == text), None
            )
        shift = 0
        for a, b, c, d in hunks:
            if b and a <= ln < a + b:
                return None
            if (a + b if b else a + 1) <= ln:
                shift = (c + d if d else c + 1) - (a + b if b else a + 1)
            else:
                break
        return ln + shift


def open_lines(repo: Path, tag: str, head: str, q: Path | None = None) -> list[dict]:
    """The carried lines of the lands since TAG still uncovered at HEAD, each once, at HEAD's
    numbering: `{file, line, text, land, item}`."""
    if not tag or not head:
        return []
    q = q or state_paths.landq_dir()
    shas = (_git(repo, "rev-list", f"{tag}..{head}") or "").split()
    recs = _landed(q, repo, {s: i for i, s in enumerate(shas)})
    landed_items = {r.get("item") for r in recs if r.get("item")}
    tree = _Tree(repo, head)
    newest = cov_newest.Newest(repo, head)

    import cov_risk  # lib/: what a measured carried line is

    allow = cov_risk.allowed(repo)  # passed by the ship's diff coverage: never refused here
    found: dict[tuple[str, int], dict] = {}
    for rec in recs:
        cr = rec.get("cov_risk") or {}
        frm = cr.get("head") or rec.get("enqueued") or rec.get("sha") or ""
        landed = rec.get("landed") or rec.get("sha") or ""
        filed = (rec.get("diffcov_item") or {}).get("id") or ""
        for c in cr.get("carry") or []:
            if not cov_risk.measured(c):
                continue  # unmeasured, not uncovered: the ship's diff coverage judges it
            rel, text = c["file"], c["text"]
            if (rel, text.strip()) in allow:
                continue  # named with its reason in the allowlist since (GOH.SHIPCOVRISK)
            at = tree.where(frm, rel, c["line"], text)
            lines = tree.text(rel) or []
            if at is None or at > len(lines) or lines[at - 1].strip() != text:
                continue  # gone at HEAD
            if newest.verdict(rel, at) in (cov_newest.COVERED, cov_newest.NOT_JUDGED):
                continue  # measured since: the suite's diff coverage passes it
            found.setdefault((rel, at), {
                "file": rel, "line": at, "text": text, "item": filed,
                "land": f"land {rec.get('item') or landed[:8]}",
                "fixed": bool(c.get("own")) and filed in landed_items,
            })  # fmt: skip
    for rel, ln, text in newest.uncovered(tag):
        found.setdefault((rel, ln), {"file": rel, "line": ln, "text": text, "item": "",
                                     "land": f"measured at {newest.sha[:8]}",
                                     "fixed": False})  # fmt: skip
    return sorted(found.values(), key=lambda c: (c["file"], c["line"]))


def unmeasured(repo: Path, tag: str, head: str) -> str:
    """The note on the changed Rust lines no complete run has measured, or "" when there are none:
    named so a suite red on one is no surprise, never a refusal (GOH.DIFFCOVUNMEASURED)."""
    newest = cov_newest.Newest(repo, head)
    left = newest.unmeasured(tag)
    if not left:
        return ""
    import cov_risk  # lib/: the one spelling of a file's line ranges

    n = sum(len(lns) for lns in left.values())
    named = [f"    {rel}:{cov_risk.ranges(lns)}" for rel, lns in sorted(left.items())]
    by = f"the newest profile ({newest.sha[:8]})" if newest.sha else "any profile (none is kept)"
    said = f"diff coverage: {counted(n, 'changed Rust line')} since {tag} not measured by {by}"
    return "\n".join([f"{said}; this ship's suite judges them:", *named])


def refusal(repo: Path, tag: str, head: str, q: Path | None = None) -> str | None:
    """`goh ship`'s preflight: why the ship is refused before its suite, or None."""
    try:
        left = open_lines(repo, tag, head, q)
    except Exception as e:  # noqa: BLE001 -- an unreadable carry is said, never a refusal
        print(f"diff coverage carry: not read ({e})", file=sys.stderr)
        return None
    if not left:
        return None
    named = "\n".join(
        f"    {c['file']}:{c['line']}  {c['text']}  ({c['land']}"
        + (f", item {c['item']}" if c["item"] else "")
        + (", landed, not measured since)" if c["fixed"] else ")")
        for c in left[:SHOWN]
    )
    more = f"\n    ... and {len(left) - SHOWN} more" if len(left) > SHOWN else ""
    return (
        f"diff coverage carry: {counted(len(left), 'changed Rust line')} since {tag} "
        f"{'is' if len(left) == 1 else 'are'} uncovered by every measurement kept at {head[:8]}; "
        "the suite's diff coverage would refuse them after its 15 min run "
        f"(GOH.DIFFCOVCARRY):\n{named}{more}\n  cover each (the next complete instrumented run "
        "of main -- the idle lander's -- clears it, its item's land alone does not), delete it, "
        "or ship with --allow-diffcov REASON naming the test that covers it"
    )


def ship_refusal(repo: Path, tag: str, head: str, allow: str | None) -> str | None:
    """tools/ship.py's call: an empty --allow-diffcov REASON is refused; any other skips."""
    if allow is not None:
        return None if allow.strip() else "--allow-diffcov needs a REASON: why they may ship"
    if why := refusal(repo, tag, head):
        return why
    if tag and (note := _note(repo, tag, head)):
        print(note, file=sys.stderr)
    return None


def _note(repo: Path, tag: str, head: str) -> str:
    try:
        return unmeasured(repo, tag, head)
    except Exception as e:  # noqa: BLE001 -- a note, never a refusal
        return f"diff coverage: the unmeasured lines not read ({e})"
