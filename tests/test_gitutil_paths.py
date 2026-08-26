"""checks/_gitutil.listed_files — the -z NUL-delimited listing contract.

Regression class (2026-08-25): git quotes paths containing non-ASCII or
control characters unless `-z` is used, so listed_files() handed checkers
QUOTED names like "caf\303\251.md" that content_bytes() could never resolve
— those files were silently skipped by every consumer, in both scopes.
"""

from conftest import EMOJI_SMILE, commit_all, run_check, stage, write


def _stage_nonascii_emoji_file(repo):
    p = repo / "café.md"
    p.write_text(f"bad {EMOJI_SMILE}\n", encoding="utf-8")
    stage(repo, p.name)
    return p


def test_no_emoji_flags_nonascii_filename_staged(repo):
    _stage_nonascii_emoji_file(repo)
    r = run_check(repo, "checks/check_no_emoji.py", "--staged")
    assert r.returncode == 1
    assert "café.md" in r.stdout


def test_no_emoji_flags_nonascii_filename_tracked(repo):
    p = repo / "café.md"
    p.write_text(f"bad {EMOJI_SMILE}\n", encoding="utf-8")
    commit_all(repo)
    r = run_check(repo, "checks/check_no_emoji.py")
    assert r.returncode == 1
    assert "café.md" in r.stdout


def test_file_length_flags_nonascii_filename_staged(repo):
    p = repo / "données.py"
    p.write_text("x = 1\n" * 501, encoding="utf-8")
    stage(repo, p.name)
    r = run_check(repo, "checks/check_file_length.py", "--max", "500",
                  "--staged")
    assert r.returncode == 1
    assert "données.py" in r.stderr


def test_file_length_flags_nonascii_filename_tracked(repo):
    p = repo / "données.py"
    p.write_text("x = 1\n" * 501, encoding="utf-8")
    commit_all(repo)
    r = run_check(repo, "checks/check_file_length.py", "--max", "500")
    assert r.returncode == 1
    assert "données.py" in r.stderr


def test_newline_in_filename_is_one_record_not_two(repo):
    # Without -z git emits this name QUOTED ("we\nird.md"), which no consumer
    # could resolve — the file was invisible to every gate. With -z it arrives
    # raw and must be policed under its true name.
    p = write(repo, "we\nird.md", f"bad {EMOJI_SMILE}\n")
    stage(repo, p.name)
    r = run_check(repo, "checks/check_no_emoji.py", "--staged")
    assert r.returncode == 1
    assert "we\nird.md" in r.stdout
