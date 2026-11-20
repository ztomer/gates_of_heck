"""changelog_scan.py -- goh's own content scanners over the release text, BEFORE the release gate
(GOH.SHIPTITLESCAN).

v0.140.3's first release attempt wrote a CHANGELOG.md heading from `goh ship --summary` text that
held a home path. The text first met goh's scanners inside the release step's full push gate, where
tests/test_self_walk.py counted it (`no hard-coded home paths`, 136 -> 137), 1019 s in. The text a
release writes is known before anything is pushed: the heading's title and the entries the cut
moves out of Unreleased (fragments already folded in by `changelog_cut.py ready`). This composes
exactly that and runs the scanners the self-walk counts that read a markdown changelog, THE
BINARY'S OWN (`gates/goh.sh`, never a copy of their patterns), over it as a file named CHANGELOG.md
in a scratch repository of its own: nothing in the checkout is written, so a refusal leaves nothing
to undo. The scratch repository has no .gatesrc, seed or exclusion, which is the self-walk's view.

Not run, and why: `plural-hedge` reads string literals in .py/.sh/.rs/.swift, never markdown;
`private-names` is no `goh.sh` check; the bracket balance of the heading's title is
`changelog_cut.title_findings`, asked beside this.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tui.lib import err  # the house's mark, never a hand-rolled one

ROOT = Path(__file__).resolve().parents[2]

# goh.sh check -> the registry label the self-walk counts it under.
SCANNERS = {
    "home-paths": "no hard-coded home paths",
    "emoji": "no disallowed emoji",
    "secrets": "no committed secrets",
    "markers": "no conflict markers",
}
SITE = re.compile(r"^\s*CHANGELOG\.md:(\d+)(?::\d+)?:?\s*(.*)$")
BOUND_S = 60


def release_text(version: str, title: str | None, moved: list[str]) -> list[str]:
    """The lines a release of VERSION adds to the changelog: its heading (when a title is given)
    and the entries moved out of Unreleased."""
    return ([f"## v{version} -- {title}", ""] if title else []) + list(moved)


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def scan(text: list[str]) -> list[str]:
    """Findings, one string each, naming the scanner and the line of `text`; [] when clean. A
    scanner that cannot run is a finding too: a scan that stopped reading must not read as clean."""
    if not any(ln.strip() for ln in text):
        return []
    sys.path.insert(0, str(ROOT / "lib"))
    import tmp_root  # every temp root under $TMPDIR/goh.noindex (GOH.TMPNOINDEX)

    out: list[str] = []
    with tempfile.TemporaryDirectory(dir=tmp_root.goh_tmp(), prefix="goh-release-text.") as d:
        (Path(d) / "CHANGELOG.md").write_text("\n".join(text) + "\n", encoding="utf-8")
        env = _env()
        for cmd in (["git", "init", "-q", "--template="], ["git", "add", "CHANGELOG.md"]):
            subprocess.run(cmd, cwd=d, env=env, stdin=subprocess.DEVNULL, check=True,
                           capture_output=True, timeout=BOUND_S)  # fmt: skip
        for check, label in SCANNERS.items():
            r = subprocess.run(["bash", str(ROOT / "gates" / "goh.sh"), check], cwd=d, env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=BOUND_S, check=False)  # fmt: skip
            if r.returncode == 0:
                continue
            sites = [m for ln in (r.stdout + r.stderr).splitlines() if (m := SITE.match(ln))]
            for m in sites:
                n = int(m.group(1))
                line = text[n - 1].strip()[:120]
                out.append(f"{label}: line {n} of the release text ({line!r}): {m.group(2)}")
            if not sites or r.returncode != 1:
                said = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()][-3:]
                out.append(f"{label}: goh {check} exited {r.returncode}: " + " | ".join(said))
    return out


def report(path: Path, version: str, title: str | None, moved: list[str]) -> int:
    """`changelog_cut.py ready`'s verdict on the release's text: 1 after naming each finding on
    stderr, else 0."""
    bad = scan(release_text(version, title, moved))
    for f in bad:
        err(f"changelog: {path}: {f}")
    return 1 if bad else 0
