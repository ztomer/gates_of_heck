#!/usr/bin/env python3
"""xctrace_profile.py — rank a Time Profiler export: self and inclusive time by frame, by thread,
by the leaf's binary, and optionally the tree below one frame.

The input is today's `xctrace export` of the `time-profile` table (`analyze_trace.sh` makes it):

    xcrun xctrace export --input run.trace --output tp.xml \\
        --xpath '/trace-toc/run[@number="1"]/data/table[@schema="time-profile"]'

Every thread, process, weight, backtrace, frame and binary in it is written in full ONCE, with an
`id`, and named by `ref` everywhere after, so a row read on its own is mostly pointers. The XML is
streamed and each row cleared once read: an all-processes recording exports to hundreds of MB.

    xctrace_profile.py tp.xml --process ZoneWM --under readNow --tree 6
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TREE_SHARE = 0.02  # a tree branch under 2% of the time below the frame is noise
NS_PER_MS = 1e6


@dataclasses.dataclass
class Summary:
    total: int = 0
    self_time: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    inclusive: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    threads: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    binaries: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    tree: collections.Counter = dataclasses.field(default_factory=collections.Counter)


def _key(el: ET.Element | None) -> str | None:
    return None if el is None else (el.get("ref") or el.get("id"))


def summarise(
    xml: Path | str, process: str = "", under: str | None = None, tree: int = 0
) -> Summary:
    """Rows of every process whose name contains `process` ("" is all); with `under`, only the
    stacks through a frame containing it, and with `tree` the paths that deep below its outermost
    such frame."""
    fmt: dict[str, str] = {}  # thread and process id -> its display text
    binaries: dict[str, str] = {}
    frames: dict[str, tuple[str, str]] = {}  # frame id -> (name, binary)
    stacks: dict[str, list[str]] = {}  # backtrace id -> frame ids, leaf first
    weights: dict[str, int] = {}
    s = Summary()
    for _, el in ET.iterparse(xml, events=("end",)):
        tag, ident = el.tag, el.get("id")
        if tag == "binary" and ident:
            binaries[ident] = el.get("name", "?")
        elif tag == "frame" and ident:
            frames[ident] = (
                el.get("name") or el.get("addr", "?"),
                binaries.get(_key(el.find("binary")) or "", "?"),
            )
        elif tag in ("backtrace", "tagged-backtrace") and ident:
            stacks[ident] = [k for k in map(_key, el.findall("frame")) if k]
        elif tag == "weight" and ident:
            weights[ident] = int(el.text or 0)
        elif tag in ("thread", "process") and ident:
            fmt[ident] = el.get("fmt", "")
        elif tag == "row":
            _row(el, s, fmt, frames, stacks, weights, process, under, tree)
            el.clear()
    return s


def _row(el, s, fmt, frames, stacks, weights, process, under, tree) -> None:
    if process not in fmt.get(_key(el.find("process")) or "", ""):
        return
    bt = el.find("tagged-backtrace")
    bt = bt if bt is not None else el.find("backtrace")
    names = [frames.get(k, ("?", "?")) for k in stacks.get(_key(bt) or "", [])]
    if under and not any(under in n for n, _ in names):
        return
    weight = weights.get(_key(el.find("weight")) or "", 0)
    s.total += weight
    if under and tree:
        root = max(i for i, (n, _) in enumerate(names) if under in n)
        path = tuple(n for n, _ in reversed(names[max(0, root - tree) : root]))
        for depth in range(1, len(path) + 1):
            s.tree[path[:depth]] += weight
    thread = fmt.get(_key(el.find("thread")) or "", "")
    s.threads[thread.split(" (0x")[0]] += weight
    if names:
        s.self_time[names[0][0]] += weight
        s.binaries[names[0][1]] += weight
    for n in {n for n, _ in names}:
        s.inclusive[n] += weight


def report(s: Summary, label: str, under: str | None = None, top: int = 40) -> str:
    def ms(v: int) -> str:
        return f"{v / NS_PER_MS:9.1f} ms"

    lines = [f"total {ms(s.total)} in {label or 'every process'}"]
    if s.tree:
        lines.append(f"\n== tree below {under}")
        lines += [
            f"{ms(v)}  {'  ' * (len(path) - 1)}{path[-1][:110]}"
            for path, v in sorted(s.tree.items())
            if v >= s.total * TREE_SHARE
        ]
    for title, c in (
        ("by thread", s.threads),
        ("self, by binary", s.binaries),
        ("self", s.self_time),
        ("inclusive", s.inclusive),
    ):
        lines.append(f"\n== {title}")
        lines += [f"{ms(v)}  {k[:150]}" for k, v in c.most_common(top)]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("xml", help="an `xctrace export` of the time-profile table")
    ap.add_argument("--process", default="", help="substring of the process's name (default: all)")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--under", help="only stacks through a frame containing this")
    ap.add_argument("--tree", type=int, default=0, help="with --under: the tree this deep below it")
    a = ap.parse_args()
    s = summarise(a.xml, a.process, a.under, a.tree)
    print(report(s, a.process, a.under, a.top))
    return 0 if s.total else 1


if __name__ == "__main__":
    sys.exit(main())
