"""`goh md-links` derives the same anchors and finds the same links as `_md_text` (Phase N1).

The whole-repo tests of `test_check_md_links.py` run on both tiers, but a link only exercises the
anchors it names. So the grammar itself is compared: every tracked `.md` file here, and the
shapes the slug is hard on -- numbered and duplicate headings, setext, explicit ids, HTML anchors,
accented and DECOMPOSED letters (NFC), unbalanced backtick runs. Measured 2026-10-05 over 627
`.md` files in 30 local repos: identical.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

from reference_kit import load_reference  # noqa: E402

_md = load_reference("_md_text")
anchors, links_in = _md.anchors, _md.links_in

SHAPES = [
    "### 4.8. What `.90` can actually do, measured\n",
    "## Notes\n\n## Notes\n\n## Notes\n",
    "Measured results\n================\n\nUnder\n---\n",
    "## Custom {#my-own-id}\n",
    '<a id="manual-anchor"></a>\n\n<a name="other">x</a>\n',
    "## Café numbers\n\n## Café decomposed\n",
    "## a `` ` `` b\n\n[x](#a--b) and ``` [y](z.md) `` and `[w](q.md)\n",
    "## Tab\there <b>bold</b> *em* _u_ ~s~\n",
    "```\n## not a heading\n[l](nowhere.md)\n```\n\n    [indented](nowhere.md)\n",
    '[ref]: <./a b.md>\n[x](c.md "title") ![img](d.png)\n',
    "# Ünïcödé — dash ✓ mark\n",
]


def native(goh: Path, text: str) -> dict:
    r = subprocess.run(
        [str(goh), "md-links", "--anchors"], input=text, capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def reference(text: str) -> dict:
    return {"anchors": sorted(anchors(text)), "links": [list(x) for x in links_in(text)]}


@pytest.mark.parametrize("text", SHAPES)
def test_the_hard_shapes_read_the_same(goh: Path, text: str) -> None:
    assert native(goh, text) == reference(text)


def test_every_markdown_file_here_reads_the_same(goh: Path) -> None:
    files = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "*.md"], capture_output=True, text=True
    ).stdout.split()
    assert len(files) > 10, files
    differ = []
    for rel in files:
        text = (REPO_ROOT / rel).read_bytes().decode("utf-8", "replace")
        if native(goh, text) != reference(text):
            differ.append(rel)
    assert not differ, differ
