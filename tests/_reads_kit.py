"""A fixture's seeded suite record says its tests were watched (lib/read_sets.py, GOH.READUNION): a
record that says nothing of what they read is carried from by no land."""

from __future__ import annotations


def seen(nodes) -> list:
    """`reuse.record_suite`'s `reads`: NODES ran watched, and read nothing under the tree."""
    return [(list(nodes), {n.split("::")[0]: [] for n in nodes})]
