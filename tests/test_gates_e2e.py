"""End-to-end: structural.sh --staged inside a fixture repo.

Pins the whole layer-1 contract: exit codes, first-failure behavior, and that
output uses only Kare glyphs (the suite's own style gate, applied to gates).
"""

import re

from conftest import EMOJI_SMILE, REPO_ROOT, run_gate, stage, write

STRUCTURAL = "gates/structural.sh"

ALLOWED_GLYPHS_RE = re.compile(r"[→·✓✗⚠↔↑↓←⌘⌥⌨]")


def _mk_gatesrc(repo, extra: str = "") -> None:
    (repo / ".gatesrc").write_text(f"GOH_MAX_LINES=5\n{extra}")


def test_staged_clean_repo_passes(repo):
    write(repo, "a.py", "\n" * 3)
    _mk_gatesrc(repo)
    stage(repo, "a.py")
    r = run_gate(repo, STRUCTURAL, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all structural gates passed" in r.stdout


def test_staged_over_length_file_fails(repo):
    write(repo, "long.py", "\n" * 7)
    _mk_gatesrc(repo)
    stage(repo, "long.py")
    r = run_gate(repo, STRUCTURAL, "--staged")
    assert r.returncode == 1
    assert "long.py" in r.stderr


def test_no_cap_set_warns_but_passes(repo):
    write(repo, "a.py", "\n" * 500)
    stage(repo, "a.py")
    r = run_gate(repo, STRUCTURAL, "--staged")  # no .gatesrc at all
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GOH_MAX_LINES" in (r.stdout + r.stderr)  # honest: says WHY it skipped


def test_first_failure_stops_the_gate(repo):
    # Two staged violations; the emoji gate runs first, so the length gate's
    # step line must never appear (exit on FIRST failure).
    write(repo, "bad.md", EMOJI_SMILE + "\n")
    write(repo, "long.py", "\n" * 50)
    _mk_gatesrc(repo)
    stage(repo, "bad.md", "long.py")
    r = run_gate(repo, STRUCTURAL, "--staged")
    assert r.returncode == 1
    assert "file length" not in r.stdout


def test_output_uses_kare_glyphs_only(repo):
    write(repo, "ok.py", "x = 1\n")
    _mk_gatesrc(repo)
    stage(repo, "ok.py")
    r = run_gate(repo, STRUCTURAL, "--staged")
    combined = r.stdout + r.stderr
    stripped = ALLOWED_GLYPHS_RE.sub("", combined)
    for ch in stripped:
        assert ord(ch) < 0x1F000 or ch in "→·✓✗⚠↔↑↓←⌘⌥⌨", (
            f"gate emitted a non-Kare pictograph: U+{ord(ch):04X} {ch!r}"
        )


def test_swift_cold_build_default_is_on():
    # Stated-vs-implemented pin: the header says cold builds are the default.
    # (HEAD shipped :-0 with a header claiming "default in --full".)
    text = (REPO_ROOT / "gates" / "swift_gate.sh").read_text()
    assert '${GOH_SWIFT_COLD:-1}' in text
    assert 'xcode-dd' in text  # cold wipe covers the pinned xcode DD too


def test_explicit_full_flag_reaches_no_checker(repo):
    # Caught by the first real pre-push run: --full was forwarded verbatim to
    # checkers whose argparse rejects it. Only --staged is a checker scope.
    _mk_gatesrc(repo)
    r = run_gate(repo, STRUCTURAL, "--full")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "unrecognized arguments" not in r.stderr


def test_full_scope_runs_disk_check(repo):
    _mk_gatesrc(repo)
    r = run_gate(repo, STRUCTURAL)  # no --staged → full tree + disk hygiene
    assert r.returncode == 0, r.stdout + r.stderr
    assert "disk hygiene" in r.stdout


# ── exemption union semantics ────────────────────────────────────────────────
# GOH_EXCLUDE exempts from BOTH the emoji scan and the length cap;
# GOH_LINE_EXCLUDE is additive to the LENGTH check only. Red proof: under the
# old replacement semantics (LINE_EXCLUDE replaced EXCLUDE for length), the
# vendor file below was flagged by the length gate.


def test_line_exclude_is_additive_not_replacement(repo):
    write(repo, "vendor/big.py", EMOJI_SMILE + "\n" * 30)
    _mk_gatesrc(repo,
                extra="GOH_EXCLUDE='vendor/'\nGOH_LINE_EXCLUDE='legacy\\.py'\n")
    stage(repo, "vendor/big.py")
    r = run_gate(repo, STRUCTURAL, "--staged")
    assert r.returncode == 0, (
        f"vendor/ must be exempt from BOTH checks "
        f"(old replacement semantics leaked it into length): {r.stdout}{r.stderr}"
    )


def test_line_exclude_path_is_length_exempt_but_emoji_scanned(repo):
    # legacy.py is far over the cap AND carries a disallowed glyph: the cap
    # must forgive it (LINE_EXCLUDE), the emoji scan must NOT.
    write(repo, "legacy.py", EMOJI_SMILE + "\n" * 30)
    _mk_gatesrc(repo,
                extra="GOH_EXCLUDE='vendor/'\nGOH_LINE_EXCLUDE='legacy\\.py'\n")
    stage(repo, "legacy.py")
    r = run_gate(repo, STRUCTURAL, "--staged")
    assert r.returncode == 1
    combined = r.stdout + r.stderr
    assert "emoji" in combined.lower()
    assert "legacy.py" in combined


def test_line_exclude_path_without_emoji_passes_cleanly(repo):
    # Control: same exemption, clean content — nothing may flag it.
    write(repo, "legacy.py", "\n" * 30)
    _mk_gatesrc(repo, extra="GOH_LINE_EXCLUDE='legacy\\.py'\n")
    stage(repo, "legacy.py")
    r = run_gate(repo, STRUCTURAL, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
