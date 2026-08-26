"""check_no_emoji.py — policy and scope tests.

Red-proof note: the --staged index-vs-worktree cases here encode the DESIRED
behavior delivered by Phase 4 (checks/_gitutil.py). They were written in
Phase 0, proven red against HEAD (worktree reads), and turned green by the
class fix.
"""

import subprocess

from conftest import (
    CHECK_MARK_BUTTON,
    commit_all,
    DOUBLE_ARROW,
    EMOJI_SMILE,
    KEYCAP_COMBINE,
    REPO_ROOT,
    VS16,
    git,
    run_check,
    stage,
    write,
)

SCRIPT = "checks/check_no_emoji.py"


def test_clean_repo_passes(repo):
    (repo / "a.py").write_text("x = 1  # → ok ✓\n", encoding="utf-8")
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 0


def test_all_allowed_glyphs_pass(repo):
    (repo / "a.md").write_text("→ ✓ ✗ ⚠ ↔ ↑ ↓ ← ⌘ ⌥ ⌨\n", encoding="utf-8")
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 0


def test_disallowed_pictograph_fails(repo):
    (repo / "a.md").write_text(f"hello {EMOJI_SMILE} world\n", encoding="utf-8")
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 1
    assert "a.md:1" in r.stdout


def test_dingbat_and_ranges_fail(repo):
    for glyph in (CHECK_MARK_BUTTON, DOUBLE_ARROW, KEYCAP_COMBINE):
        (repo / "a.md").write_text(f"x {glyph}\n", encoding="utf-8")
        commit_all(repo)
        assert run_check(repo, SCRIPT).returncode == 1, hex(ord(glyph))


def test_variation_selector_alone_fails(repo):
    # VS16 is rejected even when attached to nothing else: it exists to make
    # text render as emoji, so it is a failure state on its own.
    (repo / "a.md").write_text(f"warn{VS16} sign\n", encoding="utf-8")
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 1


def test_allowed_warn_sign_with_vs16_reports_the_vs16(repo):
    # U+26A0 is allowed; the VS16 that turns it into an emoji is not.
    (repo / "a.md").write_text(f"\u26a0{VS16} hazard\n", encoding="utf-8")
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 1


def test_binary_file_skipped(repo):
    (repo / "blob.bin").write_bytes(b"\x00\xff" + EMOJI_SMILE.encode() + b"\x00")
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 0


def test_exclude_flag_skips_matching_paths(repo):
    (repo / "vendor" / "x.md").parent.mkdir(exist_ok=True)
    (repo / "vendor" / "x.md").write_text(EMOJI_SMILE + "\n", encoding="utf-8")
    commit_all(repo)
    assert run_check(repo, SCRIPT, "--exclude", "^vendor/").returncode == 0
    assert run_check(repo, SCRIPT).returncode == 1


# ---- staged scope -----------------------------------------------------------


def _stage_then_mutate_worktree(repo, rel: str, staged_text: str, worktree_text: str):
    p = write(repo, rel, staged_text)
    stage(repo, p.name)
    p.write_text(worktree_text, encoding="utf-8")


def test_staged_scope_clean_index_passes_even_if_worktree_dirty(repo):
    # The gate must measure THE INDEX, not the worktree: what is being
    # committed is clean, later scratch edits must not block the commit.
    _stage_then_mutate_worktree(
        repo, "a.md", "clean → staged\n", f"dirty {EMOJI_SMILE}\n"
    )
    r = run_check(repo, SCRIPT, "--staged")
    assert r.returncode == 0, r.stdout


def test_staged_scope_dirty_index_fails_even_if_worktree_fixed(repo):
    # And the inverse: emoji in the INDEX fail even if the worktree was since
    # cleaned — otherwise `git add` then fix in worktree would slip through.
    _stage_then_mutate_worktree(
        repo, "a.md", f"bad {EMOJI_SMILE}\n", "fixed, no glyph\n"
    )
    r = run_check(repo, SCRIPT, "--staged")
    assert r.returncode == 1
    assert "a.md" in r.stdout


def test_tracked_scope_uses_worktree_state(repo):
    # Full-tree mode polices what IS, not what was committed: after committing
    # a bad file, cleaning the worktree makes tracked mode pass without a new
    # commit... but ls-files still lists the path, so content must come from disk.
    p = write(repo, "a.md", EMOJI_SMILE + "\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "bad")
    assert run_check(repo, SCRIPT).returncode == 1
    p.write_text("clean now\n", encoding="utf-8")
    assert run_check(repo, SCRIPT).returncode == 0


def test_failure_message_permits_exactly_the_allow_list(repo):
    # Message-policy parity: the permit list a user is shown must be the
    # policy's full allow-list, generated FROM it so they cannot drift.
    (repo / "a.md").write_text(EMOJI_SMILE + "\n", encoding="utf-8")
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 1
    for glyph in "→ ✓ ✗ ⚠ ↔ ↑ ↓ ← ⌘ ⌥ ⌨".split():
        assert glyph in r.stdout, f"failure message omits allowed glyph {glyph!r}"


def test_staged_new_file_reported_relative_to_root(repo):
    nested = repo / "src"
    nested.mkdir()
    (nested / "n.md").write_text(EMOJI_SMILE + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "src/n.md"], check=True)
    r = run_check(repo, SCRIPT, "--staged")
    assert r.returncode == 1
    assert "src/n.md" in r.stdout


# ---- 2026-08-25 policy ruling: singleton/small-range additions ----------------

# Emoji-presentation codepoints outside the big blocks, each flagged on its own.
SINGLETON_CODEPOINTS = (0x2934, 0x2935, 0x3030, 0x3297, 0x3299, 0x2139, 0x24C2)


def test_singleton_ranges_are_flagged(repo):
    for cp in SINGLETON_CODEPOINTS:
        (repo / "a.md").write_text(f"x {chr(cp)}\n", encoding="utf-8")
        commit_all(repo)
        r = run_check(repo, SCRIPT)
        assert r.returncode == 1, f"U+{cp:04X} was not flagged"


# Typographic signs with legal meaning are POLICY-PERMITTED bare (bucket 2d),
# but their VS16 forms request emoji presentation and stay rejected.
SIGN_CODEPOINTS = (0x00A9, 0x00AE, 0x2122)  # copyright / registered / trademark


def test_typographic_signs_pass_bare_but_vs16_forms_fail(repo):
    for cp in SIGN_CODEPOINTS:
        g = chr(cp)
        (repo / "a.md").write_text(f"legal {g} notice\n", encoding="utf-8")
        commit_all(repo)
        assert run_check(repo, SCRIPT).returncode == 0, (
            f"bare U+{cp:04X} must pass"
        )
        (repo / "a.md").write_text(f"legal {g}{VS16}\n", encoding="utf-8")
        commit_all(repo)
        assert run_check(repo, SCRIPT).returncode == 1, (
            f"VS16 form of U+{cp:04X} must fail"
        )


def test_failure_message_permit_list_includes_typographic_signs(repo):
    # Message-policy parity: the signs are policy, so the permit list a user
    # is shown must name them.
    (repo / "a.md").write_text(EMOJI_SMILE + "\n", encoding="utf-8")
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 1
    for cp in SIGN_CODEPOINTS:
        assert chr(cp) in r.stdout, f"U+{cp:04X} missing from permit list"


# ---- 2026-08-26 policy ruling: geometric shapes + full astral cap -------------

# Geometric Shapes block (0x25A0-0x25FF): decorative bullets/play-triangles/
# squares — decoration, not vocabulary. Zero occurrences across the wired
# repos on adoption, so nothing flips today.
GEOMETRIC = (0x25B6, 0x25FC, 0x25FD)  # play triangle, black/white-medium squares


def test_geometric_shape_glyphs_are_flagged(repo):
    for cp in GEOMETRIC:
        (repo / "a.md").write_text(f"x {chr(cp)}\n", encoding="utf-8")
        commit_all(repo)
        r = run_check(repo, SCRIPT)
        assert r.returncode == 1, f"U+{cp:04X} was not flagged"
        assert f"U+{cp:04X}" in r.stdout


def test_astral_cap_extends_to_plane_end(repo):
    # The emoji cap now reaches 0x1FFFF: Symbols for Legacy Computing
    # (sextants, 0x1FB00+) and future Unicode emoji additions are flagged on
    # arrival instead of after someone ships them.
    for cp in (0x1FB00, 0x1FAF7):  # sextant-8, hand-with-index-finger-and-thumb-crossed
        (repo / "a.md").write_text(f"x {chr(cp)}\n", encoding="utf-8")
        commit_all(repo)
        r = run_check(repo, SCRIPT)
        assert r.returncode == 1, f"U+{cp:04X} was not flagged"


def test_self_host_committed_tree_stays_clean():
    # The gate's own repo is its first consumer: widening RANGES must never
    # flag this tree (the suite builds all disallowed glyphs with chr()
    # precisely so this scan stays green).
    r = subprocess.run(
        ["python3", str(REPO_ROOT / SCRIPT)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_runs_from_subdirectory_of_target_repo(repo):
    # Gates may be invoked from anywhere inside the target repo (Phase 4 made
    # this contract explicit); the checker resolves paths against the root.
    sub = repo / "src"
    sub.mkdir()
    (sub / "deep.md").write_text("ok\n", encoding="utf-8")
    commit_all(repo)
    r = subprocess.run(
        ["python3", str(REPO_ROOT / SCRIPT)],
        cwd=sub,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
