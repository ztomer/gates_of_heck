"""`goh md-links` judges the relative paths HTML puts in markdown, not only markdown's own links.

A README that shows a picture for light and dark mode writes `<picture><source srcset=...><img
src=...></picture>`: markdown has no syntax for it. md-links read only `[text](target)` and
`[ref]: target`, so those paths were judged nowhere, and a broken one passed (found 2026-11-18 from
ZSUS_14_Portrait_Mount_v20, whose README shows 14 images this way and whose md-links subject was
the one LICENSE link). GOH.MDHTMLREFS: `src`, `srcset` (every candidate) and `href` of any tag are
judged and counted like a markdown link.
"""

import pytest
from _fast_git import fast_init
from conftest import commit_all, write
from tier_kit import both_tiers, run_tiered  # noqa: F401  # both_tiers: a fixture

pytestmark = pytest.mark.usefixtures("both_tiers")

PICTURE = (
    '<picture><source media="(prefers-color-scheme: dark)" srcset="docs/a_dark.png">'
    '<img src="docs/a_light.png" alt="a" width="360"></picture>\n'
)


def _repo(tmp_path, readme, *files):
    repo = tmp_path / "app"
    repo.mkdir(parents=True)
    fast_init(repo, "main")
    write(repo, "README.md", readme)
    for rel in files:
        write(repo, rel, "x\n")
    commit_all(repo)
    return repo


def _run(repo, *args):
    return run_tiered(repo, "checks/check_md_links.py", "md-links", *args)


def test_a_missing_img_src_is_red(tmp_path):
    r = _run(_repo(tmp_path, '# t\n\n<img src="missing.png" alt="x">\n'))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "README.md:3: missing.png" in r.stderr, r.stderr


def test_a_picture_is_judged_source_and_img_alike(tmp_path):
    """The shape that was blind: both halves of a light/dark picture, each one red alone."""
    r = _run(_repo(tmp_path, PICTURE, "docs/a_light.png"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "docs/a_dark.png" in r.stderr, r.stderr
    assert "docs/a_light.png" not in r.stderr, r.stderr
    r = _run(_repo(tmp_path / "2", PICTURE, "docs/a_dark.png"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "docs/a_light.png" in r.stderr, r.stderr


def test_a_picture_whose_files_exist_is_green(tmp_path):
    r = _run(_repo(tmp_path, PICTURE, "docs/a_light.png", "docs/a_dark.png"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_srcset_candidate_is_judged_without_its_descriptor(tmp_path):
    readme = '<img srcset="a.png 1x, b.png 2x, c.png 640w" src="a.png">\n'
    r = _run(_repo(tmp_path, readme, "a.png", "b.png"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "c.png" in r.stderr and "640w" not in r.stderr, r.stderr
    assert "b.png" not in r.stderr, r.stderr


def test_an_html_href_and_its_anchor_are_judged(tmp_path):
    readme = '<a href="docs/guide.md#setup">guide</a> and <a href="#nowhere">here</a>\n'
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "README.md", readme)
    write(repo, "docs/guide.md", "## Setup\n")
    commit_all(repo)
    r = _run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "#nowhere" in r.stderr and "#setup" not in r.stderr, r.stderr


def test_a_tag_spanning_lines_is_read_at_the_attribute_line(tmp_path):
    readme = '# t\n\n<img\n  alt="x"\n  src="missing.png">\n'
    r = _run(_repo(tmp_path, readme))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "README.md:5: missing.png" in r.stderr, r.stderr


def test_an_indented_img_inside_an_html_block_is_judged(tmp_path):
    """`<p align="center">` then an indented `<img>` is the common centred-logo README; in an HTML
    block an indented line is not code, so it is not blanked as an indented code block."""
    readme = '<p align="center">\n    <img src="logo.png">\n</p>\n'
    r = _run(_repo(tmp_path, readme))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "logo.png" in r.stderr, r.stderr


def test_remote_data_and_absolute_sources_are_skipped_and_counted(tmp_path):
    readme = (
        '<img src="https://example.invalid/a.png"> <img src="data:image/png;base64,AAAA">\n'
        '<a href="mailto:x@example.invalid">m</a> <img src="/site/abs.png">\n'
    )
    r = _run(_repo(tmp_path, readme))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "4 out-of-scope links skipped" in r.stdout, r.stdout


def test_html_in_code_is_an_example_not_a_finding(tmp_path):
    readme = 'Write `<img src="nope.png">` like this:\n\n```html\n<img src="also-nope.png">\n```\n'
    r = _run(_repo(tmp_path, readme))
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_data_attribute_is_not_a_source(tmp_path):
    """`data-src` is a script's value, not a path the renderer loads."""
    r = _run(_repo(tmp_path, '<img data-src="lazy.png" src="a.png">\n', "a.png"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_html_in_a_comment_is_not_rendered_and_not_read(tmp_path):
    r = _run(_repo(tmp_path, '<!-- old:\n<img src="gone.png">\n-->\n<img src="a.png">\n', "a.png"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_empty_value_and_a_trailing_srcset_comma_name_no_path(tmp_path):
    """`src=""` names nothing, and `srcset="a.png 1x, "` has one candidate, not two."""
    r = _run(_repo(tmp_path, '<img src="" srcset="a.png 1x, " alt=x>\n', "a.png"))
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run(_repo(tmp_path / "2", "<img srcset='gone.png 1x, ' src=a.png>\n", "a.png"))
    assert r.returncode == 1 and "gone.png" in r.stderr, r.stdout + r.stderr
