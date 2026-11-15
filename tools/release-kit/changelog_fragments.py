"""changelog_fragments.py -- one file per change, folded into the changelog at release.

The class (GOH.CLASS.CHECKOUT part d): one shared file, concurrent writers. Every goh lane that
landed on 2026-11-14 wrote its entry into the same `## Unreleased` stanza of CHANGELOG.md, so each
land after the first conflicted on it -- four times in one night, each one a hand rebase. Two
changes never share a line when each change writes its OWN file: `changelog/<ID>.md` beside the
changelog, holding that change's entries (top-level `* ` items, no heading). The release step
(`changelog_cut.py cut --fragments DIR`) folds every fragment into Unreleased, in name order, and
cuts the stanza from it; the release commit deletes the fragments it folded.

A repo opts in with `.gatesrc` GOH_CHANGELOG_FRAGMENTS=on. Then the commit gate keeps CHANGELOG.md's
Unreleased stanza EMPTY at every commit (the structural step `no changelog entry under Unreleased`,
crates/goh/src/changelog_fragments.rs): an entry written there by hand is refused, so the shared
stanza is never a place two lanes write.
A release's cut leaves it empty, so the release commit passes the same rule.

The directory's README.md is its documentation, never a fragment. Pinned by
tests/test_changelog_fragments.py.
"""

from __future__ import annotations

from pathlib import Path

DIR_NAME = "changelog"
SKIP = frozenset({"README.md"})


def directory(changelog: Path) -> Path:
    """The fragment directory of `changelog`: `changelog/` beside it."""
    return changelog.parent / DIR_NAME


def paths(d: Path) -> list[Path]:
    """Every fragment in `d`, in name order: each `*.md` but the README."""
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if p.is_file() and p.name not in SKIP)


def read(d: Path) -> tuple[list[tuple[Path, list[str]]], list[str]]:
    """([(fragment, its lines)], findings). A fragment is entries only: a heading in one would
    split the stanza it is folded into, and a file with no `* `/`- ` item at column 0 adds
    nothing an entry check can see."""
    out: list[tuple[Path, list[str]]] = []
    findings: list[str] = []
    for p in paths(d):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            findings.append(f"{p}: cannot be read as UTF-8 text: {e}")
            continue
        if any(ln.startswith("#") for ln in lines):
            findings.append(f"{p}: holds a heading; a fragment is entries only (the release "
                            "step writes the heading)")  # fmt: skip
        elif not any(ln.startswith(("* ", "- ")) for ln in lines):
            findings.append(f"{p}: holds no entry (a `* ` item at column 0)")
        else:
            out.append((p, lines))
    return out, findings
