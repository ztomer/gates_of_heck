"""One changelog fragment per change, folded at release (GOH.CLASS.CHECKOUT part d).

Every goh lane land of 2026-11-14 conflicted on CHANGELOG.md, four times in one night, because each
lane wrote the same `## Unreleased` stanza. With `.gatesrc` GOH_CHANGELOG_FRAGMENTS=on, a change's
entry is `changelog/<ITEM-ID>.md`; the release step folds the fragments into the stanza it cuts
(`changelog_cut.py --fragments`) and deletes them, and the commit gate refuses a line under
Unreleased (`crates/goh/src/changelog_fragments.rs`), so the shared stanza is never written.

Red-proofs (2026-11-15): with `with_fragments` returning its input, the kit said "Unreleased
holds nothing to cut" for the fragment and three tests went red; with the step removed from
`late_steps`, a commit with an Unreleased entry passed `goh structural --staged`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import test_release_kit as rk
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, hermetic_env
from test_release_kit import TAG, VERSION, commit_all, run_release, sh
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import ship_changelog

kit = rk.kit  # the release fixtures, by assignment (an import would be shadowed by each test's arg)
CUT = REPO_ROOT / "tools" / "release-kit" / "changelog_cut.py"
LABEL = "no changelog entry under Unreleased"
LOG = "# CHANGELOG\n\n## Unreleased\n\n## v1.0.0 -- one\n\n* a\n"


def _cut(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CUT), *args], cwd=cwd, capture_output=True,
                          text=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip


def _tree(root: Path, frags: dict[str, str], log: str = LOG) -> Path:
    (root / "changelog").mkdir(parents=True, exist_ok=True)
    (root / "CHANGELOG.md").write_text(log)
    (root / "changelog" / "README.md").write_text("# not a fragment\n\n* never folded\n")
    for name, text in frags.items():
        (root / "changelog" / name).write_text(text)
    return root


def test_the_cut_folds_every_fragment_in_name_order_and_never_the_readme(tmp_path):
    _tree(tmp_path, {"B.md": "* **b lands.** more\n  continued\n", "A.md": "* a2\n"})
    r = _cut(tmp_path, "cut", "--version", "1.1.0", "--title", "two", "--fragments", "changelog")
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "CHANGELOG.md").read_text()
    assert text.startswith(
        "# CHANGELOG\n\n## Unreleased\n\n## v1.1.0 -- two\n\n"
        "* a2\n\n* **b lands.** more\n  continued\n"
    ), text
    assert "never folded" not in text
    listed = _cut(tmp_path, "fragments", "--fragments", "changelog").stdout.split()
    assert listed == ["changelog/A.md", "changelog/B.md"], listed


def test_ready_counts_fragments_as_what_the_release_ships(tmp_path):
    _tree(tmp_path, {"X.md": "* **x lands.** y\n"})
    assert _cut(tmp_path, "ready", "--version", "1.1.0", "--file", "CHANGELOG.md").returncode == 1
    r = _cut(tmp_path, "ready", "--version", "1.1.0", "--fragments", "changelog")
    assert r.returncode == 0, r.stderr


def test_check_refuses_a_fragment_left_at_the_tagged_commit(tmp_path):
    _tree(tmp_path, {"X.md": "* x\n"}, log="# C\n\n## Unreleased\n\n## v1.1.0 -- t\n\n* y\n")
    r = _cut(tmp_path, "check", "--version", "1.1.0", "--fragments", "changelog")
    assert r.returncode == 1 and "1 fragment still in changelog" in r.stderr, r.stderr
    (tmp_path / "changelog" / "X.md").unlink()
    r = _cut(tmp_path, "check", "--version", "1.1.0", "--fragments", "changelog")
    assert r.returncode == 0, r.stderr


def test_a_fragment_with_a_heading_or_no_entry_is_refused(tmp_path):
    _tree(tmp_path, {"H.md": "## v9\n* x\n", "E.md": "prose only\n"})
    r = _cut(tmp_path, "cut", "--version", "1.1.0", "--fragments", "changelog")
    assert r.returncode == 1, r.stderr
    assert "H.md: holds a heading" in r.stderr and "E.md: holds no entry" in r.stderr, r.stderr
    assert (tmp_path / "CHANGELOG.md").read_text() == LOG


def test_ship_asks_the_fold_when_the_repo_keeps_fragments(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(LOG)
    [(_, cmd)] = ship_changelog.reading(tmp_path, "cut", "1.1.0", "s", "on")
    assert "--fragments changelog" in cmd, cmd
    [(_, cmd)] = ship_changelog.reading(tmp_path, "cut", "1.1.0", "s", "")
    assert "--fragments" not in cmd, cmd
    try:
        ship_changelog.reading(tmp_path, "cut", "1.1.0", "s", "yes")
    except ValueError as e:
        assert "GOH_CHANGELOG_FRAGMENTS" in str(e)
    else:
        raise AssertionError("a value neither on nor off was read as a setting")


def test_the_kit_releases_a_fragment_and_its_commit_deletes_it(kit):
    """A consumer opted in: step 0b folds the fragment into the stanza and deletes it."""
    proj = kit["proj"]
    write_gatesrc(proj, "GOH_CHANGELOG_FRAGMENTS=on\n")
    _tree(proj, {"ZZ.ONE.md": "* the new thing\n  continued\n"},
          log="# C\n\n## Unreleased\n\n## v1.1.0\n\n- older\n")  # fmt: skip
    commit_all(proj, f"chore: version {VERSION}")
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr
    tagged = sh(proj, "show", f"{TAG}:CHANGELOG.md")
    assert f"## Unreleased\n\n## {TAG}\n\n* the new thing\n  continued\n" in tagged, tagged
    assert sh(proj, "ls-tree", "--name-only", TAG, "changelog/").split() == ["changelog/README.md"]
    assert sh(proj, "status", "--porcelain").strip() == ""


def _staged(repo: Path, goh: Path, rc: str) -> subprocess.CompletedProcess[str]:
    write_gatesrc(repo, rc)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, stdin=subprocess.DEVNULL)
    return subprocess.run([str(goh), "structural", "--staged"], cwd=repo,
                          env=hermetic_env(GOH_DIR=str(REPO_ROOT)), capture_output=True, text=True,
                          check=False, stdin=subprocess.DEVNULL, timeout=patience(300))  # fmt: skip


def test_the_commit_gate_refuses_an_entry_written_under_unreleased(tmp_path, native_goh):
    fast_init(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        "# C\n\n## Unreleased\n\n* by hand\n\n## v1.0.0\n\n* a\n"
    )
    r = _staged(tmp_path, native_goh, "GOH_CHANGELOG_FRAGMENTS=on\n")
    out = r.stdout + r.stderr
    assert f"structural: {LABEL} failed" in out, out
    assert "CHANGELOG.md:5: * by hand" in out and "changelog/<ITEM-ID>.md" in out, out
    # Off (unset), the same stanza is the repo's own business and the step does not run.
    r = _staged(tmp_path, native_goh, "")
    assert LABEL not in r.stdout + r.stderr, r.stdout + r.stderr
    # On, with the entry moved into a fragment, the step passes: a release cut's tree is this one.
    _tree(tmp_path, {"X.md": "* by hand\n"}, log="# C\n\n## Unreleased\n\n## v1.0.0\n\n* a\n")
    r = _staged(tmp_path, native_goh, "GOH_CHANGELOG_FRAGMENTS=on\n")
    assert f"structural: {LABEL} failed" not in r.stdout + r.stderr, r.stdout + r.stderr
    assert "Unreleased is empty" in r.stdout + r.stderr, r.stdout + r.stderr


def test_goh_itself_keeps_fragments_and_an_empty_unreleased():
    rc = (REPO_ROOT / ".gatesrc").read_text()
    assert "\nGOH_CHANGELOG_FRAGMENTS=on\n" in rc
    text = (REPO_ROOT / "CHANGELOG.md").read_text()
    head = text.index("## Unreleased\n")
    assert text[head:].split("\n## ", 1)[0].strip() == "## Unreleased", "an entry under Unreleased"
