"""`goh md-links` — a relative markdown link must resolve to a file AND an anchor.

An audit of `app_updates` (2026-10-01) had to link into another file in the same
repo and hand-derived the heading anchor `#48-what-90-can-actually-do-measured`.
It had already written a wrong one before that. Both outcomes are silent: a link
to a heading that does not exist still renders, still looks like a link, and
fails only when clicked. And the anchor is not the heading — GitHub lowercases,
drops punctuation and turns spaces into hyphens, so the derivation is a
multi-step transformation a human must redo at every link.

The class: a name checked against nothing. A cross-file anchor is a claim about
another file's contents, made from a file that says nothing about them.

Every slug case below is a REAL heading shape from this estate, not a sketch:
a heading numbered 4.8 reading "What .90 can actually do, measured"
(app_updates), a `#### Foo` in a protocol doc, and a setext heading.
"""

import json
import subprocess

import pytest

from conftest import commit_all, git, write
from tier_kit import both_tiers, run_tiered  # noqa: F401  # both_tiers: a fixture

from conftest import native_goh_path  # noqa: E402
from _fast_git import fast_init  # noqa: E402


def anchors(text: str) -> set[str]:
    """The anchors `goh md-links --anchors` reads in `text`."""
    r = subprocess.run(
        [str(native_goh_path()), "md-links", "--anchors"],
        input=text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return set(json.loads(r.stdout)["anchors"])


def slugify(heading: str) -> str:
    """The one anchor a lone `# heading` gets."""
    (slug,) = anchors(f"# {heading}\n")
    return slug


CHECKER = "checks/check_md_links.py"

pytestmark = pytest.mark.usefixtures("both_tiers")


def _run(repo, *args):
    """The checker over `repo`, on the current tier (Python, or `goh md-links`)."""
    return run_tiered(repo, CHECKER, "md-links", *args)


# ── the slug, which is the whole difficulty ──────────────────────────────────


def test_a_numbered_heading_slugs_like_github():
    """app_updates' real heading. The section number becomes part of the slug,
    the backticked `.90` keeps its digits and loses its punctuation, the trailing
    comma goes, and every space becomes a hyphen."""
    assert (
        slugify("4.8. What `.90` can actually do, measured")
        == "48-what-90-can-actually-do-measured"
    )


def test_a_backticked_heading_keeps_its_inner_text():
    """The regression this gate's first draft had, and it rejected the correct
    link it was written to check: blanking inline code spans turned `.90` into
    seven hyphens. Anchors must come from the RAW heading text."""
    slug = slugify("4.8. What `.90` can actually do, measured")
    assert "90" in slug and "---" not in slug, slug


def test_a_duplicate_heading_gets_a_counter():
    """A link to the SECOND `## Notes` has to resolve; the bare slug resolves to
    the first. GitHub numbers them `notes`, `notes-1`, `notes-2`."""
    text = "# T\n\n## Notes\n\na\n\n## Notes\n\nb\n\n## Notes\n"
    assert {"notes", "notes-1", "notes-2"} <= anchors(text), anchors(text)


def test_a_setext_heading_is_a_heading():
    """Underlined headings are real CommonMark, and a regex that only knows `#`
    misses them, so every link into one is reported broken."""
    text = "# Title\n\nSome prose\n\nMeasured Results\n==============\n\nmore\n"
    assert "measured-results" in anchors(text), anchors(text)


def test_an_explicit_id_is_an_anchor():
    """`{#my-own-id}` names the heading directly, so the literal wins and the
    derived slug is not what a link has to use."""
    assert "my-own-id" in anchors("## Custom {#my-own-id}\n"), anchors("## Custom {#my-own-id}\n")


def test_a_raw_html_anchor_is_an_anchor():
    text = '<a id="manual-anchor"></a>\n\n## Something\n'
    assert "manual-anchor" in anchors(text), anchors(text)


def test_accented_headings_keep_their_letters():
    """GitHub keeps `café` as `café`. Normalising to ASCII here would invent a
    broken anchor for every non-English heading in the estate — the precise
    failure this checker exists to end, manufactured by the checker."""
    assert slugify("Café numbers") == "café-numbers", slugify("Café numbers")


def test_a_hash_inside_a_fenced_block_is_not_a_heading():
    text = "# Real\n\n```sh\n# not a heading\necho hi\n```\n\n## Also real\n"
    found = anchors(text)
    assert "real" in found and "also-real" in found, found
    assert not any("not-a-heading" in a for a in found), found


# ── the defect, both ways ────────────────────────────────────────────────────


def test_a_wrong_anchor_is_red(tmp_path):
    """The audit's own error: the anchor derived without the section number."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "[see the measurement](ROADMAP.md#what-90-can-actually-do-measured)\n")
    write(repo, "ROADMAP.md", "## 4.8. What `.90` can actually do, measured\n")
    commit_all(repo)

    r = _run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no anchor #what-90-can-actually-do-measured" in r.stderr, r.stderr
    # Named as `file:line` of the LINKING file -- the place a fix goes.
    assert "README.md:1" in r.stderr, r.stderr


def test_a_missing_file_is_red(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "[the plan](ROADMAP-waf.md#section)\n")
    commit_all(repo)

    r = _run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no such file" in r.stderr, r.stderr
    assert "ROADMAP-waf.md" in r.stderr, r.stderr


def test_the_right_anchor_is_green(tmp_path):
    """app_updates' README, the link that exists and resolves."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(
        repo,
        "README.md",
        "[what .90 can actually do](ROADMAP-waf-and-fleet.md"
        "#48-what-90-can-actually-do-measured)\n",
    )
    write(repo, "ROADMAP-waf-and-fleet.md", "## 4.8. What `.90` can actually do, measured\n")
    commit_all(repo)
    assert _run(repo).returncode == 0, _run(repo).stderr


def test_a_directory_link_resolves(tmp_path):
    """`[docs/decisions/](docs/decisions/)` is how ZoneWM points at a folder of
    records, and GitHub renders it as a tree. Requiring a file reports every
    such link in the estate as broken — which is how this first landed."""
    repo = tmp_path / "zon"
    repo.mkdir()
    fast_init(repo, "main")
    (repo / "docs" / "decisions").mkdir(parents=True)
    write(repo, "docs/decisions/0001-x.md", "# x\n")
    write(
        repo,
        "README.md",
        "every settled question is a record in [docs/decisions/](docs/decisions/)\n",
    )
    commit_all(repo)
    assert _run(repo).returncode == 0, _run(repo).stderr


def test_a_relative_link_resolves_against_the_linking_file(tmp_path):
    """`../README.md` from inside `docs/` is the ordinary spelling. Resolved from
    the repo root instead, it finds nothing and reports every one of them."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "# top\n")
    write(repo, "docs/guide.md", "see [the top](../README.md#top)\n")
    commit_all(repo)
    assert _run(repo).returncode == 0, _run(repo).stderr


# ── what is deliberately out of scope, and says so ───────────────────────────


def test_http_and_mailto_links_are_skipped_and_counted(tmp_path):
    """Nothing here can know whether a remote document still exists, and a gate
    that pretends to cries wolf. But the COUNT is reported, so a file of nothing
    but http links never prints the same sentence as a file with real links."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "[a](https://example.invalid/p) and [b](mailto:x@example.invalid)\n")
    commit_all(repo)
    r = _run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 out-of-scope link(s) skipped" in r.stdout, r.stdout


def test_a_site_absolute_path_is_skipped(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "[a](/somewhere/else)\n")
    commit_all(repo)
    assert _run(repo).returncode == 0, _run(repo).stderr


def test_a_link_pointing_out_of_the_repository_is_red(tmp_path):
    """`../../elsewhere/x.md` has no truth in this tree, so it cannot be
    verified — and unverifiable read as fine is how the next one ships."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "[elsewhere](../other/x.md)\n")
    commit_all(repo)
    r = _run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "outside this repository" in r.stderr, r.stderr


def test_a_link_in_a_code_span_is_an_example_not_a_finding(tmp_path):
    """Documentation about markdown link syntax contains links meant not to
    resolve. Reading them invents findings nobody can fix without deleting the
    example."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(
        repo,
        "README.md",
        "Inline `[x](nope.md)` is an example.\n\n"
        "```\n[x](also-nope.md)\n```\n\n"
        "    [x](indented-nope.md)\n",
    )
    commit_all(repo)
    assert _run(repo).returncode == 0, _run(repo).stderr


def test_a_reference_style_link_is_checked(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "See [the plan][plan].\n\n[plan]: docs/missing.md\n")
    commit_all(repo)
    r = _run(repo)
    assert r.returncode == 1 and "docs/missing.md" in r.stderr, r.stderr


def test_an_image_link_resolves_too(tmp_path):
    """`![x](assets/y.png)` is a path that 404s on a README exactly like a
    document link does."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "![diagram](assets/gone.png)\n")
    commit_all(repo)
    r = _run(repo)
    assert r.returncode == 1 and "assets/gone.png" in r.stderr, r.stderr


def test_a_link_title_does_not_become_part_of_the_target(tmp_path):
    """`[x](docs/a.md "Why")` — the target stops at the space."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "docs/a.md", "# A\n")
    write(repo, "README.md", '[x](docs/a.md "Why this exists")\n')
    commit_all(repo)
    assert _run(repo).returncode == 0, _run(repo).stderr


# ── named non-runs, and the empty-scope contract ─────────────────────────────


def test_a_repo_with_no_markdown_is_a_named_non_run(tmp_path):
    repo = tmp_path / "code"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "main.py", "print(1)\n")
    commit_all(repo)
    r = _run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "nothing to check" in r.stdout, r.stdout


def test_a_markdown_file_with_no_links_is_green_and_not_silent(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "# just prose\n")
    commit_all(repo)
    r = _run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 markdown file(s)" in r.stdout, r.stdout


def test_a_vendored_doc_tree_can_be_excluded(tmp_path):
    """ztools vendors camoufox-rs, whose PROTOCOL.md carries an anchor to a
    heading that is not in that file — upstream's problem, not this repo's, and
    `GOH_EXCLUDE` is already the mechanism for exactly that."""
    repo = tmp_path / "z"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "vendor/camoufox-rs/docs/PROTOCOL.md", "See [CookieOptions](#cookieoptions).\n")
    write(repo, "README.md", "# z\n")
    commit_all(repo)

    assert _run(repo).returncode == 1, "the vendored anchor was not policed"
    assert _run(repo, "--exclude", "vendor/").returncode == 0, _run(
        repo, "--exclude", "vendor/"
    ).stderr


def test_a_bad_exclude_regex_is_exit_2_not_a_silent_exemption(tmp_path):
    """`GOH_EXCLUDE` arrives here straight from .gatesrc. An unparsable pattern
    that merely warned would exempt the whole tree while the run still claimed
    to have examined it."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "[x](nope.md)\n")
    commit_all(repo)
    r = _run(repo, "--exclude", "[unclosed")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "bad --exclude" in r.stderr, r.stderr


def test_a_missing_directory_is_a_usage_error(tmp_path):
    assert _run(tmp_path, "--root", str(tmp_path / "nope")).returncode == 2


def test_json_output(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "[x](nope.md)\n")
    commit_all(repo)
    r = _run(repo, "--json")
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["examined"] == 1 and len(payload["findings"]) == 1, payload


# ── staged scope is INDEX scope (contract #3) ───────────────────────────────


def test_staged_judges_the_commit_not_the_worktree(tmp_path):
    """A broken link fixed in the commit must pass; one introduced only in the
    editor must not block the commit."""
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", "[x](nope.md)\n")
    commit_all(repo)

    write(repo, "README.md", "[x](docs.md)\n")
    write(repo, "docs.md", "# there\n")
    git(repo, "add", "-A")
    assert _run(repo, "--staged").returncode == 0, (
        "a fix staged over a broken link still failed: the worktree was read"
    )

    write(repo, "README.md", "[x](other.md)\n")
    r = _run(repo, "--staged")
    assert r.returncode == 0, f"an unstaged edit blocked a clean commit: {r.stdout}{r.stderr}"


# The retired Python checker's `--probe` and its blinded-source calibrations went with it (Phase
# N3). `test_a_wrong_anchor_is_red` is the same proof against the native: the anchor arm is
# load-bearing, or the audit's wrong anchor would pass.
