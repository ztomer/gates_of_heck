"""docs/BACKLOG.md's plan of record holds only items that are ready to start, and only what is open.

BACKLOG's own rule says an item is not ready unless it carries its measured baseline, its exit
number, the red-first test that proves it can fail, and the ways it can lie. Nothing checked it.
After v0.24.0 the file had no plan of record at all, and six of its seven "open" items had
already landed (`22c8d49`, `06afca4`, `ae8b768`, `2642cbe`, `3ede8a3`, `8edb913`) without being
pruned (2026-10-08). So the roadmap is read here: every open item carries the four fields, and
every done item names the commit that did it, or it goes to the landed table.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

FIELDS = ("Baseline:", "Exit:", "Red-first:", "Lies:")


def _roadmap() -> str:
    text = (REPO_ROOT / "docs" / "BACKLOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## Roadmap to v\d+\.\d+\.\d+.*?$(.*?)(?=^## )", text, re.M | re.S)
    assert m, "BACKLOG.md has no `## Roadmap to vX.Y.Z` section: there is no plan of record"
    return m.group(1)


def _items(section: str) -> list[tuple[str, str]]:
    """`(status, body)` for every `- [ ]` / `- [x]` / `- [~]` item, continuation lines included."""
    out, cur = [], None
    for line in section.splitlines():
        m = re.match(r"^- \[([ x~])\] (.*)", line)
        if m:
            cur = [m.group(1), m.group(2)]
            out.append(cur)
        elif cur and line.startswith("  "):
            cur[1] += " " + line.strip()
        else:
            cur = None
    return [(s, b) for s, b in out]


def test_the_roadmap_has_items() -> None:
    assert len(_items(_roadmap())) > 0


def test_every_open_item_is_ready_to_start() -> None:
    unready = [
        f"{body[:70]}... lacks {', '.join(f for f in FIELDS if f not in body)}"
        for status, body in _items(_roadmap())
        if status == " " and not all(f in body for f in FIELDS)
    ]
    assert unready == [], "\n".join(unready)


def test_every_done_item_names_its_commit() -> None:
    bare = [
        body[:70]
        for status, body in _items(_roadmap())
        if status == "x" and not re.search(r"`[0-9a-f]{7,40}`", body)
    ]
    assert bare == [], "a done item names the commit that did it:\n" + "\n".join(bare)
