"""check_lints_optin.py — a workspace's lint policy is a declaration, and a
crate that never opts in is subject to none of it while looking clean.

The defect these pin was measured on `monitor` (2026-09-07): the workspace had
declared `pedantic` and `nursery` from the start, two of three crates opted in,
and the third -- the biggest, the one uploaded to every monitored host -- had no
`[lints]` table at all and had accumulated 254 findings that no gate reported.
"""

from conftest import commit_all, run_check, write

SCRIPT = "checks/check_lints_optin.py"

WS = '[workspace]\nmembers = ["a", "b"]\n\n[workspace.lints.clippy]\npedantic = "warn"\n'
OPTED_IN = '[package]\nname = "{name}"\n\n[lints]\nworkspace = true\n'
NO_LINTS = '[package]\nname = "{name}"\n'
OWN_LINTS = '[package]\nname = "{name}"\n\n[lints.clippy]\nall = "warn"\n'


def _ws(repo, a_body, b_body):
    write(repo, "Cargo.toml", WS)
    write(repo, "a/Cargo.toml", a_body.format(name="a"))
    write(repo, "b/Cargo.toml", b_body.format(name="b"))
    commit_all(repo)


def test_a_member_with_no_lints_table_fails(repo):
    _ws(repo, OPTED_IN, NO_LINTS)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 1, r.stdout
    assert "b/Cargo.toml" in r.stdout + r.stderr
    assert "inherits NONE" in r.stdout + r.stderr


def test_every_member_opted_in_passes(repo):
    _ws(repo, OPTED_IN, OPTED_IN)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 0, r.stdout
    assert "2 workspace member" in r.stdout + r.stderr


def test_a_member_with_its_own_lints_still_fails(repo):
    """Its own table is not the workspace's, and the distinction is the point:
    the crate has A policy, just not the one the workspace declared."""
    _ws(repo, OPTED_IN, OWN_LINTS)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 1, r.stdout
    assert "does not inherit the workspace policy" in r.stdout + r.stderr


def test_a_workspace_with_no_policy_says_so_rather_than_passing_quietly(repo):
    """An empty subject population must be named, not reported as success --
    otherwise deleting `[workspace.lints]` retires this gate in silence."""
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["a"]\n')
    write(repo, "a/Cargo.toml", NO_LINTS.format(name="a"))
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 0, r.stdout
    assert "nothing to inherit" in r.stdout + r.stderr


def test_excluded_members_are_not_policed(repo):
    write(repo, "Cargo.toml",
          '[workspace]\nmembers = ["a", "b"]\nexclude = ["b"]\n\n'
          '[workspace.lints.clippy]\npedantic = "warn"\n')
    write(repo, "a/Cargo.toml", OPTED_IN.format(name="a"))
    write(repo, "b/Cargo.toml", NO_LINTS.format(name="b"))
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 0, r.stdout


def test_self_test_passes(repo):
    r = run_check(repo, SCRIPT, "--self-test")
    assert r.returncode == 0, r.stdout
