"""check_exclusion_has_ceiling.py — an exemption from the CAP is not an
exemption from every bound.

The defect this pins was measured on `monitor` (2026-09-07):
`crates/multitop/tests/event_loop_e2e.rs` was 619 lines, named in
GOH_LINE_EXCLUDE (so the 500-line cap did not apply) and absent from
`tools/loc_baseline.txt` (so no ratchet ceiling applied either). Two gates,
each correct about its own list, and between them a file bounded by nothing.
The repo's baseline header asserted the opposite in prose, which is why nobody
looked.
"""

from conftest import commit_all, run_check, write

SCRIPT = "checks/check_exclusion_has_ceiling.py"
BASE = "base.txt"


def _repo(repo, *, lines: int, in_baseline: bool, path: str = "src/big.rs"):
    write(repo, path, "\n".join(f"// {i}" for i in range(lines)) + "\n")
    body = "# lines path\n0 __sentinel__\n"
    if in_baseline:
        body += f"{lines} {path}\n"
    write(repo, BASE, body)
    commit_all(repo)
    return path


def test_over_cap_and_exempt_with_no_ceiling_fails(repo):
    """The monitor hole, reproduced."""
    path = _repo(repo, lines=619, in_baseline=False)
    r = run_check(repo, SCRIPT, "--max", "500", "--baseline", BASE,
                  "--line-exclude", r"src/big\.rs")
    assert r.returncode == 1
    assert "NO ceiling" in r.stdout + r.stderr
    assert path in r.stdout + r.stderr


def test_over_cap_and_exempt_with_a_ceiling_passes(repo):
    _repo(repo, lines=619, in_baseline=True)
    r = run_check(repo, SCRIPT, "--max", "500", "--baseline", BASE,
                  "--line-exclude", r"src/big\.rs")
    assert r.returncode == 0


def test_exempt_but_under_the_cap_needs_no_ceiling(repo):
    """The cap would bind it if the exemption were removed, so it is bounded."""
    _repo(repo, lines=100, in_baseline=False)
    r = run_check(repo, SCRIPT, "--max", "500", "--baseline", BASE,
                  "--line-exclude", r"src/big\.rs")
    assert r.returncode == 0


def test_exactly_at_the_cap_needs_no_ceiling(repo):
    """The boundary must match check_file_length's, or the two gates disagree
    about the same file and the disagreement reads as a defect in the file.

    Both now count through _gitutil.line_count: a trailing newline TERMINATES
    the last line rather than beginning another. A plain `count("\\n") + 1`
    reads this 500-line file as 501 and demands a ceiling the cap does not."""
    _repo(repo, lines=500, in_baseline=False)
    r = run_check(repo, SCRIPT, "--max", "500", "--baseline", BASE,
                  "--line-exclude", r"src/big\.rs")
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_exclusion_matching_nothing_fails(repo):
    """A stale exemption naming a path that is gone is indistinguishable from
    one that is doing its job, so it is refused rather than passed."""
    _repo(repo, lines=100, in_baseline=False)
    r = run_check(repo, SCRIPT, "--max", "500", "--baseline", BASE,
                  "--line-exclude", r"src/gone\.rs")
    assert r.returncode == 1
    assert "0 tracked files" in r.stdout + r.stderr


def test_no_exclusions_at_all_passes(repo):
    _repo(repo, lines=100, in_baseline=False)
    r = run_check(repo, SCRIPT, "--max", "500", "--baseline", BASE)
    assert r.returncode == 0


def test_a_missing_baseline_is_a_precondition_failure_not_a_pass(repo):
    _repo(repo, lines=619, in_baseline=False)
    r = run_check(repo, SCRIPT, "--max", "500", "--baseline", "nope.txt",
                  "--line-exclude", r"src/big\.rs")
    assert r.returncode == 2
