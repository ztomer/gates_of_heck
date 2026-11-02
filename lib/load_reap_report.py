"""What one backstop sweep did, and the words the log needs (GOH.REAPLOG, split out of
load_reap.py for the 500-line cap): the report, the kill it records, the rule names."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

# The rule that killed a process, as the log names it (GOH.REAPLOG): the registry the tool wrote,
# the house's own spinner shape, and the hand-rolled busy loop burning a core.
RULE_REGISTRY = "registry"
RULE_HOUSE = "house-shape"
RULE_HAND = "hand-rolled-shape"


class Seen(int):
    """A pid the sweep has READ BACK, carrying the command line it read (GOH.REAPLOG).

    It IS the pid (an int), so the exempted line `outcomes = [(pid, kill(pid)) for pid in ...]`
    stays as the house's kill-by-name rule pins it; the command rides along so the log can say
    WHAT was killed without a second, racing `ps` after the kill.
    """

    command: str

    def __new__(cls, pid: int, command: str) -> Self:
        self = super().__new__(cls, pid)
        self.command = command
        return self


def record(outcomes: list[tuple[int, bool]], rule: str, report: Report) -> None:
    """Put each outcome in the report, a kill with the rule that did it and the command it ran."""
    for pid, gone in outcomes:
        if gone:
            report.killed.append(int(pid))
            report.kills.append(Kill(int(pid), rule, getattr(pid, "command", "?")))
        else:
            report.failed.append(int(pid))


@dataclass
class Kill:
    """One process the backstop killed: which, the command it was running, and the rule."""

    pid: int
    rule: str
    command: str


@dataclass
class Report:
    """What one sweep did, in the words the job's log needs."""

    killed: list[int] = field(default_factory=list)
    kills: list[Kill] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)
    dropped: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    swept: list[str] = field(default_factory=list)
    blind: list[str] = field(default_factory=list)

    @property
    def touched(self) -> int:
        return len(self.killed) + len(self.kept) + len(self.dropped) + len(self.failed)

    def summary(self) -> str:
        return (
            f"killed {len(self.killed)}, kept {len(self.kept)}, dropped {len(self.dropped)}, "
            f"failed {len(self.failed)}, registries removed {len(self.removed)}"
        )
