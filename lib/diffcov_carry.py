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

AT THE SHIP. `refusal` is `goh ship`'s preflight, before the suite, read in seconds: every landed
record of the repo since the base tag (its landing on `tag..HEAD`) with a carried line still
uncovered refuses, naming `file:line  text`, unless --allow-diffcov REASON. Re-judged cheaply, a
carried line no longer counts when:
  * it is gone: its file is absent at the ship's HEAD, or `git diff -U0 head..HEAD` changed it
    (an edited line is the editing land's own judgment, carried there if still at risk);
  * the item filed for it landed (the fix lane's land: the ship's suite stays the measurement);
  * a later land's own judgment did not carry it: a record after it whose cov_risk judged a tip
    (`head`) holding this land lists no line of that file with that text (a newer profile, or a
    covered neighbourhood). A lane judged on a tip without this land's change never clears it.
Pinned by tests/test_diffcov_carry.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitrun  # lib/: git under a deadline, aimed at its repository

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
    root = Path(os.environ.get("LOOPCTL_ROOT") or Path.home() / "Projects")
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
    carried = ((req or {}).get("cov_risk") or {}).get("carry") or []
    own = [c for c in carried if c.get("own")]
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
    out = []
    for path in (q / "done").glob("*.json"):
        with contextlib.suppress(OSError, ValueError):
            rec = json.loads(path.read_text())
            sha = rec.get("landed") or rec.get("sha") or ""
            mine = sha in order and os.path.realpath(rec.get("repo") or "") == real
            if mine and rec.get("state") == "landed" and rec.get("mode", "land") == "land":
                out.append({**rec, "_at": order[sha]})
    return sorted(out, key=lambda r: -r["_at"])


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
    q = q or Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")
    shas = (_git(repo, "rev-list", f"{tag}..{head}") or "").split()
    recs = _landed(q, repo, {s: i for i, s in enumerate(shas)})
    landed_items = {r.get("item") for r in recs if r.get("item")}
    tree = _Tree(repo, head)
    seen: dict[tuple[str, str], bool] = {}

    def holds(landed: str, judged: str) -> bool:  # a later judgment saw this land's change
        if (landed, judged) not in seen:
            seen[(landed, judged)] = (
                _git(repo, "merge-base", "--is-ancestor", landed, judged) is not None
            )
        return seen[(landed, judged)]

    found: dict[tuple[str, int], dict] = {}
    cleared: set[tuple[str, int]] = set()
    for n, rec in enumerate(recs):
        cr = rec.get("cov_risk") or {}
        frm = cr.get("head") or rec.get("enqueued") or rec.get("sha") or ""
        landed = rec.get("landed") or rec.get("sha") or ""
        filed = (rec.get("diffcov_item") or {}).get("id")
        later = [j for r in recs[n + 1 :] if (j := r.get("cov_risk") or {}).get("head")
                 and isinstance(j.get("carry"), list)]  # fmt: skip
        for c in cr.get("carry") or []:
            rel, text = c["file"], c["text"]
            at = tree.where(frm, rel, c["line"], text)
            lines = tree.text(rel) or []
            if at is None or at > len(lines) or lines[at - 1].strip() != text:
                continue  # gone at HEAD
            key = (rel, at)
            land = rec.get("item") or landed[:8]
            found.setdefault(
                key, {"file": rel, "line": at, "text": text, "land": land, "item": filed or ""}
            )
            if (c.get("own") and filed in landed_items) or any(
                holds(landed, j["head"])
                and not any(o["file"] == rel and o["text"] == text for o in j["carry"])
                for j in later
            ):
                cleared.add(key)  # its fix lane landed, or a later judgment no longer carries it
    return sorted((c for k, c in found.items() if k not in cleared),
                  key=lambda c: (c["file"], c["line"]))  # fmt: skip


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
        f"    {c['file']}:{c['line']}  {c['text']}  (land {c['land']}"
        + (f", item {c['item']})" if c["item"] else ")")
        for c in left[:SHOWN]
    )
    more = f"\n    ... and {len(left) - SHOWN} more" if len(left) > SHOWN else ""
    return (
        f"diff coverage carry: {counted(len(left), 'changed Rust line')} the lands since {tag} "
        f"recorded uncovered are still uncovered at {head[:8]}; the suite's diff coverage would "
        f"refuse them after its 15 min run (GOH.DIFFCOVCARRY):\n{named}{more}\n  cover each "
        "(landing the item filed for it clears it), delete it, or ship with --allow-diffcov REASON"
    )


def ship_refusal(repo: Path, tag: str, head: str, allow: str | None) -> str | None:
    """tools/ship.py's call: an empty --allow-diffcov REASON is refused; any other skips."""
    if allow is not None:
        return None if allow.strip() else "--allow-diffcov needs a REASON: why they may ship"
    return refusal(repo, tag, head)
