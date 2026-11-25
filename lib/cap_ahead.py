"""lib/cap_ahead.py -- a land that grows a file into its cap's band files the file's split
(GOH.CAPAHEAD).

THE COST. 76 fault lines from 61 agents (2026-11-01 .. 11-21) met a file at its line cap in the
middle of an unrelated change ('tools/loopctl.py is at the 500-line cap', 'tests/conftest.py sits
at exactly 500'), and each lane split the file under time pressure inside an item about something
else -- or patched around it (GOH.FLAKYSCOPEDCACHE kept a live-tree key in a test because conftest
had no line left). The cap was found at the moment of the edit.

AT THE LAND. When a land lands, `file_items` reads the land's own commits (the enqueued range,
counted, at the commits main holds for it) and every measured source file (crates/goh/src/length.rs:
the cap's own suffixes and names) whose line count went from at or under the BAND to over it: the
band is the last BAND lines below the repo's `GOH_MAX_LINES` (goh's 450, the headroom line of
tests/test_cap_headroom.py, at a cap of 500). Each such file files `<PREFIX>.SPLIT<NAME>` through
`loopctl file --json` (status todo, phase HY), naming the file, its count, its cap and its
responsibilities (its top-level definitions), so the split is its own change before the next edit
needs the room. The record names each as `cap_items`. A repo with no cap files nothing.

`sweep ROOT...` files the same item for every file of each repo already in the band's last NEAR
lines (at or within 25 of the cap): today's files, once.

Pinned by tests/test_cap_ahead.py.

A land that grows a measured file over its cap's band (the last 50 lines under `GOH_MAX_LINES`;
goh's 450 headroom) files `<PREFIX>.SPLIT<NAME>` through `loopctl file`, naming the file, its count
and its top-level definitions, so the split is its own change before the next edit needs the room
(GOH.CAPAHEAD); `sweep REPO...` files the same for every file within 25 lines of the cap.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _cl
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _cl.suppress(ModuleNotFoundError):  # a copy outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitrun  # lib/: git under a deadline, aimed at its repository

ROOT = Path(__file__).resolve().parent.parent
BAND = 50  # the band: the last BAND lines under the cap (goh's 450 headroom at 500)
NEAR = 25  # the sweep: files at or within NEAR lines of the cap
SHOWN = 12  # responsibilities named
TRIES = 80  # `loopctl file` repeats answered: each names one open split item the text overlaps
SPLIT_PACKAGE = (
    3  # the ship package "File splits under the line cap" (GOH.FILERSTAMP); loopctl wants one named
)
DUP = re.compile(r"repeats open item (\S+) ")
ASKED = re.compile(r'"([A-Z][\w-]*\.[\w.-]+)": "<why it differs>"')
GENERIC = {"tests", "mod", "lib", "main", "init", "util", "utils"}  # stems a parent disambiguates
MAX_LINES = re.compile(r"^\s*GOH_MAX_LINES=['\"]?(\d+)", re.MULTILINE)
DEFS = (
    re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)", re.MULTILINE),  # Python
    re.compile(
        r"^(?:pub(?:\([\w:]+\))?\s+)?(?:const\s+)?(?:fn|struct|enum|trait|mod)\s+(\w+)",
        re.MULTILINE,
    ),  # fmt: skip  -- Rust
    re.compile(r"^(?:function\s+)?([A-Za-z_][\w:-]*)\s*\(\)\s*\{?", re.MULTILINE),  # shell
)


def _git(repo: Path | str, *args: str) -> str | None:
    r = gitrun.run_reader(list(args), repo=str(repo), timeout=60)
    return r.stdout if r.returncode == 0 else None


def scope() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The cap's suffixes and names, read from the one list (crates/goh/src/length.rs)."""
    src = (ROOT / "crates" / "goh" / "src" / "length.rs").read_text(encoding="utf-8")
    out = []
    for name in ("SOURCE_SUFFIXES", "SOURCE_NAMES"):
        body = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", src, re.DOTALL)
        out.append(tuple(re.findall(r'"([^"]*)"', body.group(1) if body else "")))
    return out[0], out[1]


def measured(rel: str, sc: tuple[tuple[str, ...], tuple[str, ...]]) -> bool:
    return rel.endswith(sc[0]) or rel.rsplit("/", 1)[-1] in sc[1]


def cap_at(repo: Path | str, rev: str) -> int | None:
    """The repo's `GOH_MAX_LINES` at REV, or None (no cap: nothing to be near)."""
    m = MAX_LINES.search(_git(repo, "show", f"{rev}:.gatesrc") or "")
    return int(m.group(1)) if m else None


def lines_at(repo: Path | str, rev: str, rel: str) -> int:
    text = _git(repo, "show", f"{rev}:{rel}")
    return 0 if text is None else len(text.splitlines())


def entered(repo: Path | str, before: str, after: str, cap: int) -> list[tuple[str, int, int]]:
    """`(file, lines before, lines after)`: each measured file BEFORE..AFTER took over the band."""
    line = cap - BAND
    sc = scope()
    names = (_git(repo, "diff", "--name-only", "--no-renames", "-z", before, after) or "").split(
        "\0"
    )
    out = []
    for rel in filter(None, names):
        if not measured(rel, sc):
            continue
        was, now = lines_at(repo, before, rel), lines_at(repo, after, rel)
        if was <= line < now:
            out.append((rel, was, now))
    return out


def responsibilities(text: str) -> list[str]:
    """The file's top-level definitions, in order: what a split divides."""
    found: list[tuple[int, str]] = []
    for rx in DEFS:
        found += [(m.start(), m.group(1)) for m in rx.finditer(text)]
    return list(dict.fromkeys(n for _, n in sorted(found) if not n.startswith("__")))


def item_id(rel: str, item: str = "") -> str:
    prefix = item.split(".", 1)[0] if "." in item else "GOH"
    parts = rel.split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    if stem.strip("_") in GENERIC and len(parts) > 1:  # movingtag/tests.rs, not every tests.rs
        stem = parts[-2] + stem
    return f"{prefix}.SPLIT" + "".join(c for c in stem.upper() if c.isalnum())


def _item(iid: str, repo: str, rel: str, now: int, cap: int, why: str, defs: list[str]) -> dict:
    named = ", ".join(defs[:SHOWN]) + (
        f" (and {len(defs) - SHOWN} more)" if len(defs) > SHOWN else ""
    )
    import diffcov_carry  # lib/: the repo as the queue names it

    return {"id": iid, "repo": diffcov_carry._repo_name(repo), "status": "todo", "phase": "HY",
            "depends": [], "source": f"goh cap-ahead (lib/cap_ahead.py): {why}",
            "item": f"Split {rel}: {now} lines against its cap of {cap} ({why}), so the next "
            "change that must edit it is not the one that splits it under time pressure. Its "
            f"responsibilities: {named or 'none read'}. Split along them, each part under "
            f"{cap - BAND} lines.",
            "done_state": f"{rel} and each part split from it at or under {cap - BAND} lines, "
            "the tests unchanged in what they pin, landed",
            "class_of": "none",
            "class_why": "one file's own growth toward its cap; GOH.CAPAHEAD files one per file",
            }  # fmt: skip


def _file_one(loopctl: str, fields: dict) -> str:
    """File FIELDS; another file's split item reads as a repeat (one template, one class), so
    each it names is answered as not a duplicate, as `loopctl file` asks, and filed again."""
    import reuse_fault  # lib/: `loopctl file --json` under GOH_LOOP_QUEUE (one funnel)

    fields = {"package": SPLIT_PACKAGE, **fields, "regression": False}
    rel = fields["item"].split(":", 1)[0].removeprefix("Split ")
    for _ in range(TRIES):
        code, said = reuse_fault._file(loopctl, fields)
        named = set(DUP.findall(said) + ASKED.findall(said)) - {fields["id"]} if code else set()
        if not named or named <= set(fields.get("not_dup_of") or ()):
            break
        why = f"another file's split; this one splits {rel}"
        fields.setdefault("not_dup_of", {}).update(dict.fromkeys(named, why))
    if code == 0:
        return "filed"
    if f"{fields['id']} already exists" in said:
        return "already filed"
    return f"not filed: {said[-200:]}"


def _upstream(repo: str, req: dict, enq: str, after: str) -> str | None:
    """Where the land's own commits start on main: the lane forked from the main its pre-step
    judged against (`pre.base`), else from `origin/HEAD`; its N commits are the last N of AFTER
    (land.sh replays them on main) unless main holds the enqueued tip itself."""
    for below in (((req.get("pre") or {}).get("base")), "origin/HEAD"):
        fork = _git(repo, "merge-base", below, enq) if below else None
        if not fork:
            continue
        fork = fork.strip()
        if _git(repo, "merge-base", "--is-ancestor", enq, after) is not None:
            return fork
        n = _git(repo, "rev-list", "--count", f"{fork}..{enq}")
        return f"{after}~{int(n)}" if n else None
    return None


def file_items(outcome: dict, req: dict | None, loopctl: str, item: str = "") -> dict:
    """At a landing: a split item per file the land grew into its cap's band; `{"cap_items":
    [...]}` naming each and what filing said, or {} when none entered. Never raises."""
    if outcome.get("state") != "landed" or not outcome.get("landed"):
        return {}
    try:
        repo, after = outcome.get("repo") or "", outcome["landed"]
        cap = cap_at(repo, after)
        enq = (req or {}).get("sha") or outcome.get("enqueued") or ""
        upstream = _upstream(repo, req or {}, enq, after) if cap is not None and enq else None
        if not upstream:
            return {}
        grown = entered(repo, upstream, after, cap)
        out = []
        for rel, _was, now in grown:
            iid = item_id(rel, item)
            text = _git(repo, "show", f"{after}:{rel}") or ""
            why = f"the land of {item or after[:8]} took it over {cap - BAND}"
            said = (f"not filed: no loopctl at {loopctl}" if not os.path.isfile(loopctl)
                    else _file_one(loopctl, _item(iid, repo, rel, now, cap, why,
                                                  responsibilities(text))))  # fmt: skip
            out.append({"id": iid, "file": rel, "lines": now, "said": said})
    except Exception as e:  # noqa: BLE001 -- filing is never the reason a landing is lost
        return {"cap_items": [{"said": f"not read: {e}"}]}
    return {"cap_items": out} if out else {}


def sweep(repo: Path, loopctl: str, prefix: str = "GOH") -> list[str]:
    """Today's files: a split item for each file of REPO at HEAD within NEAR lines of its cap."""
    cap = cap_at(repo, "HEAD")
    if cap is None:
        return [f"{repo}: no GOH_MAX_LINES, nothing to sweep"]
    sc = scope()
    said = []
    for rel in filter(None, (_git(repo, "ls-files", "-z") or "").split("\0")):
        if not measured(rel, sc) or not (repo / rel).is_file():
            continue
        text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        n = len(text.splitlines())
        if cap - NEAR <= n <= cap:
            iid = item_id(rel, f"{prefix}.")
            why = f"at {n} of {cap} on {Path(repo).name}'s HEAD (the GOH.CAPAHEAD sweep)"
            res = _file_one(
                loopctl, _item(iid, str(repo), rel, n, cap, why, responsibilities(text))
            )
            said.append(f"{rel} ({n}): {iid} {res}")
    return said


def main(argv: list[str]) -> int:
    """`sweep [--prefix P] REPO...`: the queue prefix of the items (default GOH)."""
    prefix = "GOH"
    if len(argv) > 2 and argv[1] == "--prefix":
        prefix, argv = argv[2], [argv[0], *argv[3:]]
    if len(argv) < 2 or argv[0] != "sweep":
        print("usage: cap_ahead.py sweep [--prefix GOH] REPO...")
        return 2
    loopctl = os.environ.get("GOH_LOOPCTL") or str(Path.home() / "Projects/.loop/tools/loopctl.py")
    for r in argv[1:]:
        for line in sweep(Path(r).resolve(), loopctl, prefix):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
