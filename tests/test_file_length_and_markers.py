"""check_file_length.py + check_no_conflict_markers.py — contract tests."""

from conftest import commit_all, run_check, stage, write

LONG = "checks/check_file_length.py"
MARKERS = "checks/check_no_conflict_markers.py"


# ---- file length ------------------------------------------------------------


def test_under_cap_passes(repo):
    write(repo, "a.py", "\n" * 10)
    commit_all(repo)
    assert run_check(repo, LONG, "--max", "50").returncode == 0


def test_over_cap_fails_with_count(repo):
    write(repo, "a.py", "\n" * 11)
    commit_all(repo)
    r = run_check(repo, LONG, "--max", "10")
    assert r.returncode == 1
    assert "a.py" in r.stderr
    assert "11" in r.stderr


def test_only_source_suffixes_checked(repo):
    write(repo, "notes.md", "\n" * 500)  # docs legitimately long
    write(repo, "lock.txt", "\n" * 500)
    commit_all(repo)
    assert run_check(repo, LONG, "--max", "100").returncode == 0


def test_exclude_regex(repo):
    write(repo, "gen/thing.py", "\n" * 500)
    write(repo, "src/app.py", "\n" * 200)
    commit_all(repo)
    r = run_check(repo, LONG, "--max", "100", "--exclude", r"\.generated\.|gen/")
    assert r.returncode == 1
    assert "app.py" in r.stderr and "gen/thing" not in r.stderr


def test_staged_scope(repo):
    p = write(repo, "b.py", "\n" * 12)
    stage(repo, "b.py")
    # index has the big file; shrinking the worktree copy must not matter once
    # the class fix lands (index-truth); before that this documents behavior.
    r = run_check(repo, LONG, "--max", "10", "--staged")
    assert r.returncode == 1


# ---- conflict markers -------------------------------------------------------


def test_conflict_pair_fails(repo):
    text = "<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n"
    write(repo, "c.txt", text)
    commit_all(repo)
    r = run_check(repo, MARKERS)
    assert r.returncode == 1
    assert "c.txt:1" in r.stderr and "c.txt:5" in r.stderr


def test_diff3_base_marker_fails(repo):
    write(repo, "c.txt", "||||||| base\n")
    commit_all(repo)
    assert run_check(repo, MARKERS).returncode == 1


def test_lone_equals_line_is_not_a_marker(repo):
    # ===== underlining is ordinary Markdown/rst; only <,>,| markers count.
    write(repo, "doc.md", "Title\n======\n\nSection\n-------\n")
    commit_all(repo)
    assert run_check(repo, MARKERS).returncode == 0


def test_redirection_doc_does_not_trip(repo):
    # `>>>>>>>` NOT followed by space/EOL is shell redirection talk, not a marker.
    write(repo, "d.sh", "cat <<< \"$x\" >> out.log\n")
    commit_all(repo)
    assert run_check(repo, MARKERS).returncode == 0


def test_binary_skipped(repo):
    (repo / "e.bin").write_bytes(b"\x00" * 9000 + b"<<<<<<< x\n")
    commit_all(repo)
    assert run_check(repo, MARKERS).returncode == 0


def test_staged_scope_uses_index_content(repo):
    # Desired behavior (Phase 4): stage a clean blob, dirty the worktree —
    # the commit must pass.
    p = write(repo, "f.txt", "clean staged line\n")
    stage(repo, "f.txt")
    p.write_text("<<<<<<< ours\n", encoding="utf-8")
    r = run_check(repo, MARKERS, "--staged")
    assert r.returncode == 0, r.stderr
