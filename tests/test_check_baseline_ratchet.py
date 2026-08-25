"""check_baseline_ratchet.py — contract tests."""

import json

from conftest import run_check, write

RATCHET = "checks/check_baseline_ratchet.py"


def run(repo, *args):
    return run_check(repo, RATCHET, *args)


# ---- line format ------------------------------------------------------------


def test_growth_fails_tab_format(repo):
    write(repo, "base.txt", "10\talpha\n20\tbeta\n")
    write(repo, "now.txt", "11\talpha\n20\tbeta\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt")
    assert r.returncode == 1
    assert "alpha" in r.stderr and "10 -> 11" in r.stderr


def test_shrink_passes_and_is_reported(repo):
    write(repo, "base.txt", "10\talpha\n")
    write(repo, "now.txt", "9\talpha\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt")
    assert r.returncode == 0, r.stderr
    assert "9" in r.stdout


def test_equal_passes(repo):
    write(repo, "base.txt", "10\talpha\n")
    write(repo, "now.txt", "10\talpha\n")
    assert run(repo, "--baseline", "base.txt", "--current",
               "now.txt").returncode == 0


def test_space_separated_lines(repo):
    write(repo, "base.txt", "# ceilings\n12 alpha\n")
    write(repo, "now.txt", "13 alpha\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt")
    assert r.returncode == 1
    assert "alpha" in r.stderr


# ---- JSON format --------------------------------------------------------------


def test_json_baseline_and_current(repo):
    write(repo, "base.json", json.dumps({"a": 5, "b": 7}) + "\n")
    write(repo, "now.json", json.dumps({"a": 6, "b": 7}) + "\n")
    r = run(repo, "--baseline", "base.json", "--current", "now.json")
    assert r.returncode == 1
    assert "a" in r.stderr and "+1" in r.stderr


def test_json_shrink_passes(repo):
    write(repo, "base.json", '{"a": 5}\n')
    write(repo, "now.json", '{"a": 4}\n')
    assert run(repo, "--baseline", "base.json", "--current",
               "now.json").returncode == 0


# ---- new keys -----------------------------------------------------------------


def test_new_key_fails_as_growth(repo):
    write(repo, "base.txt", "10\talpha\n")
    write(repo, "now.txt", "10\talpha\n3\tgamma\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt")
    assert r.returncode == 1
    assert "gamma" in r.stderr and "NEW" in r.stderr


def test_allow_new_keys_passes_new_key(repo):
    write(repo, "base.txt", "10\talpha\n")
    write(repo, "now.txt", "10\talpha\n3\tgamma\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt",
            "--allow-new-keys")
    assert r.returncode == 0, r.stderr


def test_allow_new_keys_still_fails_ceilings(repo):
    write(repo, "base.txt", "10\talpha\n")
    write(repo, "now.txt", "99\talpha\n3\tgamma\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt",
            "--allow-new-keys")
    assert r.returncode == 1
    assert "alpha" in r.stderr


# ---- --record -----------------------------------------------------------------


def test_record_rewrites_baseline_from_current(repo):
    base = write(repo, "base.txt", "10\talpha\n20\tbeta\n")
    write(repo, "now.txt", "8\talpha\n2\tnewkey\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt", "--record")
    assert r.returncode == 0, r.stderr
    assert "-> 8" in r.stdout and "NEW at 2" in r.stdout
    assert base.read_text() == "8\talpha\n2\tnewkey\n"


def test_without_record_the_baseline_is_untouched(repo):
    base = write(repo, "base.txt", "10\talpha\n")
    write(repo, "now.txt", "4\talpha\n")
    run(repo, "--baseline", "base.txt", "--current", "now.txt")
    assert base.read_text() == "10\talpha\n"


# ---- current-from-command -----------------------------------------------------


def test_current_from_command_growth_fails(repo):
    write(repo, "base.txt", "10\talpha\n")
    r = run(repo, "--baseline", "base.txt", "--current-from-command",
            "printf '12\\talpha\\n'")
    assert r.returncode == 1
    assert "12 ->" in r.stderr or "alpha" in r.stderr


def test_current_from_command_failure_is_precondition(repo):
    write(repo, "base.txt", "10\talpha\n")
    r = run(repo, "--baseline", "base.txt", "--current-from-command",
            "exit 3")
    assert r.returncode == 2
    assert "exited 3" in r.stderr


# ---- preconditions (exit 2) ---------------------------------------------------


def test_missing_baseline_is_precondition(repo):
    write(repo, "now.txt", "1\tk\n")
    r = run(repo, "--baseline", "absent.txt", "--current", "now.txt")
    assert r.returncode == 2 and "does not exist" in r.stderr


def test_missing_current_file_is_precondition(repo):
    write(repo, "base.txt", "1\tk\n")
    r = run(repo, "--baseline", "base.txt", "--current", "absent.txt")
    assert r.returncode == 2 and "no such file" in r.stderr


def test_both_current_sources_rejected(repo):
    write(repo, "base.txt", "1\tk\n")
    write(repo, "now.txt", "1\tk\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt",
            "--current-from-command", "true")
    assert r.returncode == 2


def test_neither_current_source_rejected(repo):
    write(repo, "base.txt", "1\tk\n")
    assert run(repo, "--baseline", "base.txt").returncode == 2


def test_unparseable_line_is_precondition(repo):
    write(repo, "base.txt", "banana\n")
    write(repo, "now.txt", "1\tk\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt")
    assert r.returncode == 2 and "expected" in r.stderr


def test_non_numeric_json_value_is_precondition(repo):
    write(repo, "base.json", '{"a": "many"}\n')
    write(repo, "now.json", '{"a": 1}\n')
    r = run(repo, "--baseline", "base.json", "--current", "now.json")
    assert r.returncode == 2


# ---- non-finite values (2026-08-25) ------------------------------------------
#
# Policy choice, pinned here: a non-finite value is a PRECONDITION failure
# (exit 2) wherever it appears — baseline or current, JSON or line format.
# It is never compared. Rationale: inf-as-current vs a finite ceiling would
# "fail as a violation" by accident of comparison, but nan-vs-anything is
# always False (a NaN current would silently PASS), and a ratchet gate must
# not derive verdicts from values that cannot be reasoned about. Clean data
# or no verdict.

NONFINITE_JSON = '{"k": Infinity}\n'
NONFINITE_LINE = "inf\tk\n"


def test_nonfinite_baseline_json_is_precondition(repo):
    write(repo, "base.json", NONFINITE_JSON)
    write(repo, "now.json", '{"k": 1}\n')
    r = run(repo, "--baseline", "base.json", "--current", "now.json")
    assert r.returncode == 2
    assert "non-finite" in r.stderr


def test_nonfinite_current_json_is_precondition(repo):
    write(repo, "base.json", '{"k": 1}\n')
    write(repo, "now.json", NONFINITE_JSON)
    r = run(repo, "--baseline", "base.json", "--current", "now.json")
    assert r.returncode == 2
    assert "non-finite" in r.stderr


def test_nonfinite_baseline_line_is_precondition(repo):
    write(repo, "base.txt", NONFINITE_LINE)
    write(repo, "now.txt", "1\tk\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt")
    assert r.returncode == 2
    assert "non-finite" in r.stderr


def test_nonfinite_current_line_is_precondition(repo):
    # Even against a finite ceiling: non-finite current is rejected, not
    # reported as an ordinary ceiling violation.
    write(repo, "base.txt", "10\tk\n")
    write(repo, "now.txt", "-inf\tk\n")
    r = run(repo, "--baseline", "base.txt", "--current", "now.txt")
    assert r.returncode == 2
    assert "non-finite" in r.stderr
