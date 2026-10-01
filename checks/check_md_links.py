#!/usr/bin/env python3
"""Every relative markdown link in a repo's own docs must resolve — file AND anchor.

    [the shape](ROADMAP.md#48-what-90-can-actually-do-measured)
                               ^ a heading that must exist in that file

## THE DEFECT, and why hand-computing an anchor is the wrong workflow

An audit of `app_updates` (2026-10-01) had to link INTO another file in the
same repo and hand-derived the heading anchor
`#48-what-90-can-actually-do-measured` from a heading numbered 4.8 reading
"What `.90` can actually do, measured". It had already written a wrong one
before that.

Both halves of that are silent failures. A markdown link that names a heading
that does not exist still renders, still looks like a link, and 404s only when
clicked — so nothing in a CI suite, a linter, or a review of the diff notices.
And the anchor is not the heading: GitHub's rule lowercases, drops the
punctuation, and turns every space into a hyphen, so the derivation is a
six-step transformation a human must get right every time. The wrong answer is
indistinguishable from the right one in the file, which is exactly why it needs
a gate and not a habit.

The class: **a name checked against nothing.** A cross-file anchor is a claim
about another file's contents, made from a file that says nothing about the
other file's contents.

    check_md_links.py                 # every tracked *.md in this repo
    check_md_links.py --staged        # the INDEX, for pre-commit scope
    check_md_links.py --exclude RE    # exempt vendored/generated doc trees
    check_md_links.py --probe         # prove this gate can go red

## WHAT IS CHECKED, and what is deliberately not

* A relative path (`ROADMAP.md`, `docs/config.md`) must EXIST in the repo.
* A fragment (`#some-anchor`) must be one of the anchors that file generates:
  its headings, their GitHub slugs with duplicate counters, any explicit
  `{#custom-id}`, and any `<a id=…>` / `<a name=…>`.
* An http(s) URL is out of scope by definition — nothing here can know whether
  a remote document still exists, and a link-rot checker that pretends to is a
  checker that cries wolf.
* A site-absolute path (`/x/y`) is a named skip: its base is the host's root,
  not this repo, so it is not resolvable from the tree.
* Fenced code blocks and inline code spans are NOT scanned. Documentation about
  markdown link syntax contains links that are meant not to resolve, and a
  checker that reads them invents findings nobody can fix without deleting the
  example.

Exit codes: 0 clean (or a named non-run), 1 findings, 2 usage/environment.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, foreign_repo_env, listed_files, repo_root  # noqa: E402

# The parsing is its own module: it is the part worth testing in isolation and
# the part most likely to be re-implemented by hand -- and a hand-rolled anchor
# derivation is exactly the defect this checker exists to end.
from _md_text import anchors, classify, links_in, split_target  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok  # noqa: E402

def check(root: Path, exclude: str | None, staged: bool):
    """(findings, notes, examined_files, skipped_links).

    Raises ValueError on an unparsable `--exclude`: a config error the caller
    turns into exit 2, because exempting the whole tree while still claiming to
    have examined it is the worse of the two answers.

    `skipped_links` is reported on success precisely so that a file full of
    http links does not print the same sentence as a file with nothing to check.
    """
    findings: list[str] = []
    notes: list[str] = []
    files = [f for f in listed_files(str(root), staged=staged) if f.lower().endswith(".md")]
    if exclude:
        # Exit 2, not a note: an unparsable `--exclude` is a CONFIG error, and
        # continuing with the pattern dropped exempts the whole tree from the
        # gate while the run still says it examined things. The caller forwards
        # this straight from `GOH_EXCLUDE`.
        try:
            pattern = re.compile(exclude)
        except re.error as e:
            raise ValueError(f"bad --exclude regex {exclude!r}: {e}") from e
        files = [f for f in files if not pattern.search(f)]
    if not files:
        notes.append("no tracked markdown files in scope — nothing to check")
        return findings, notes, 0, 0

    # Every file's anchors, read once: a doc set links back and forth, and
    # re-reading per link is O(links x files) git spawns on a full run.
    anchor_cache: dict[str, set[str] | None] = {}

    def anchors_of(rel: str) -> set[str] | None:
        if rel not in anchor_cache:
            blob = content_bytes(str(root), rel, staged=staged)
            if blob is None or b"\0" in blob[:8000]:
                anchor_cache[rel] = None
            else:
                anchor_cache[rel] = anchors(blob.decode("utf-8", "replace"))
        return anchor_cache[rel]

    skipped = 0
    for rel in files:
        own = anchors_of(rel)
        if own is None:
            notes.append(f"{rel}: unreadable or binary — not checked")
            continue
        for number, target in links_in(
            content_bytes(str(root), rel, staged=staged).decode("utf-8", "replace")
        ):
            verdict, why = classify(target)
            if verdict == "skip":
                skipped += 1
                continue
            path, fragment = split_target(target)
            if not path:
                target_file, target_anchors = rel, own
            else:
                # Relative to the LINKING file, not the repo root: a link from
                # `docs/` to `../README.md` is the ordinary spelling and
                # resolving it from the root finds nothing.
                resolved = os.path.normpath(os.path.join(os.path.dirname(rel), path))
                if resolved.startswith(".."):
                    findings.append(
                        f"{rel}:{number}: {target} points outside this repository — "
                        f"not resolvable from the tree")
                    continue
                # A DIRECTORY link resolves too: `[docs/decisions/](docs/decisions/)`
                # is the ordinary way to point at a folder of records, and GitHub
                # renders it as a tree listing. Requiring a file would report
                # every such link in the estate as broken.
                exists = os.path.exists(os.path.join(str(root), resolved))
                if not exists and resolved not in anchor_cache:
                    # In --staged scope a file may exist in the INDEX and not on
                    # disk yet; anchors_of is the honest existence question.
                    exists = anchors_of(resolved) is not None
                if not exists:
                    findings.append(
                        f"{rel}:{number}: {target} — no such file in this repo")
                    continue
                if not fragment:
                    continue
                target_anchors = anchors_of(resolved)
                if target_anchors is None:
                    continue
                target_file = resolved
            if fragment and fragment not in target_anchors:
                findings.append(
                    f"{rel}:{number}: {target} — {target_file} has no anchor "
                    f"#{fragment}")
    return findings, notes, len(files), skipped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="repository to read (default: cwd's repo)")
    parser.add_argument("--staged", action="store_true",
                        help="judge the INDEX, not the working tree (pre-commit scope)")
    parser.add_argument("--exclude", default=None,
                        help="regex on repo-relative paths to exempt (vendored docs)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = parser.parse_args(argv)
    if args.probe:
        return probe()

    root = Path(args.root or repo_root() or ".").resolve()
    if not root.is_dir():
        err(f"[md_links] not a directory: {root}")
        return 2

    try:
        findings, notes, examined, skipped = check(root, args.exclude, args.staged)
    except ValueError as e:
        err(f"[md_links] {e}")
        return 2

    if args.json:
        import json

        print(json.dumps({"findings": findings, "notes": notes, "examined": examined,
                          "skipped": skipped}, indent=2))
        return 1 if findings else 0
    for note in notes:
        info(f"[md_links] {note}")
    for finding in findings:
        err(f"[md_links] {finding}")
    if findings:
        err(f"--- {len(findings)} finding(s): a markdown link that resolves to nothing ---")
        return 1
    if examined == 0:
        info("[md_links] no markdown examined — not applicable")
        return 0
    tail = f", {skipped} out-of-scope link(s) skipped" if skipped else ""
    ok(f"[md_links] OK — {examined} markdown file(s), every relative link resolves "
       f"to a file and an anchor{tail}")
    return 0


# ── the probe ────────────────────────────────────────────────────────────────

PROBE_DOCS = {
    "README.md": (
        "# Fixture\n\n"
        "[a real file](docs/guide.md)\n"
        "[a real anchor](docs/guide.md#48-what-90-can-actually-do-measured)\n"
        "[a duplicate heading](docs/guide.md#notes-1)\n"
        "[an explicit id](docs/guide.md#my-own-id)\n"
        "[a remote](https://example.invalid/page)\n"
        "[a mail](mailto:someone@example.invalid)\n"
        "[absolute](/somewhere/else)\n"
        "[same file](#fixture)\n"
        "[bare path](docs/guide.md)\n\n"
        # Documentation about markdown link syntax: all three are EXAMPLES and
        # none is meant to resolve. A checker that reads them invents findings
        # nobody can fix without deleting the example, so each shape is here
        # rather than only in the pytest file -- breaking the code-span,
        # fence or indentation handling has to go red.
        "Inline `[x](inline-nope.md)` is an example.\n\n"
        "```\n[x](fenced-nope.md)\n```\n\n"
        "    [x](indented-nope.md)\n"
    ),
    "docs/guide.md": (
        "# Guide\n\n"
        "## 4.8. What `.90` can actually do, measured\n\n"
        "## Notes\n\n## Notes\n\n"
        "<a id=\"manual-anchor\"></a>\n\n"
        "## Custom {#my-own-id}\n"
    ),
}


def _probe_tree(base: Path) -> Path:
    """A git repo holding the fixture docs -- `listed_files` reads git scope,
    so a directory on disk is not a repository this checker can see."""
    root = base / "repo"
    for rel, body in PROBE_DOCS.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _scratch(root, "init", "-q", "-b", "main")
    _scratch(root, "add", "-A")
    return root


def _scratch(root: Path, *args: str) -> None:
    """git on a PROBE's OWN fixture -- never the repo being gated (contract 12)."""
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env=foreign_repo_env())


def probe() -> int:
    """A wrong anchor and a missing file must both be RED, and the honest
    fixtures around them must stay quiet."""
    import tempfile


    bad = 0
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        clean = _probe_tree(base / "clean")
        green_findings, _, green_examined, green_skipped = check(clean, None, False)

        # The three example shapes, each isolated, so a break in the code-span,
        # fence or INDENTED-block handling is a red line of its own rather than a
        # finding buried in the count.
        for label, needle, example in (
            ("a link inside an inline code span is an example",
             "inline code span", "Inline `[x](x.md)` is an example."),
            ("a link inside a fenced block is an example",
             "fenced block", "```\n[x](x.md)\n```"),
            ("a link inside an indented block is an example",
             "indented block", "    [x](x.md)"),
        ):
            only = _probe_tree(base / f"only-{needle.split()[0]}")
            (only / "README.md").write_text(example + "\n", encoding="utf-8")
            _scratch(only, "add", "-A")
            only_findings, _, _, _ = check(only, None, False)
            if only_findings:
                err(f"probe: {label} (got {only_findings!r})")
                bad += 1
            else:
                ok(f"probe: {label}")

        # The audit's own error: an anchor hand-derived from a heading and left
        # MISSING the leading section number -- the transformation step every
        # hand derivation gets wrong.
        wrong = _probe_tree(base / "wrong")
        (wrong / "README.md").write_text(
            PROBE_DOCS["README.md"].replace(
                "#48-what-90-can-actually-do-measured", "#what-90-can-actually-do-measured"),
            encoding="utf-8")
        _scratch(wrong, "add", "-A")
        wrong_findings, _, _, _ = check(wrong, None, False)

        missing = _probe_tree(base / "missing")
        (missing / "README.md").write_text(
            PROBE_DOCS["README.md"] + "\n[gone](docs/deleted.md)\n", encoding="utf-8")
        _scratch(missing, "add", "-A")
        missing_findings, _, _, _ = check(missing, None, False)

        empty = _probe_tree(base / "empty")
        for stale in (empty / "docs").glob("*.md"):
            stale.unlink()
        (empty / "README.md").write_text("# no links here\n", encoding="utf-8")
        _scratch(empty, "add", "-A")
        empty_findings, empty_notes, empty_examined, _ = check(empty, None, False)

        for label, want, got in (
            ("a fixture of real links is GREEN", 0, len(green_findings)),
            ("...and every markdown file was examined", 2, green_examined),
            ("http, mailto and site-absolute links are SKIPPED, not judged",
             True, green_skipped >= 3),
            ("a hand-mis-derived anchor is RED",
             (True, True), (len(wrong_findings) == 1,
                            bool(wrong_findings) and "no anchor" in wrong_findings[0])),
            ("a missing file is RED",
             (True, True), (len(missing_findings) == 1,
                            bool(missing_findings) and "no such file" in missing_findings[0])),
            ("a markdown file with no links is GREEN, and says what it skipped",
             (0, 1), (len(empty_findings), empty_examined)),
        ):
            if want != got:
                err(f"probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                ok(f"probe: {label}")

    if bad:
        err(f"check_md_links --probe: {bad} case(s) wrong")
        return 1
    ok("check_md_links --probe: a wrong anchor and a missing file both go red, "
       "real links stay green")
    return 0


if __name__ == "__main__":
    sys.exit(main())