"""An `--exclude` pattern means what it meant when the checker was Python.

The checkers' exclude was `re.search`; the native port compiled it with Rust's `regex`, which has no
look-around, so a pattern a consumer had written for years was refused: ztools' secrets step
excluded `^(?!vendor/)` -- the one way to scope a scan TO vendor/ -- and died with "look-around is
not supported" (2026-10-06). Every exclude now compiles through one helper with the Python
dialect's look-around (`fancy-regex`).
"""

import pytest

from conftest import EMOJI_SMILE, commit_all, run_goh, write

TO_VENDOR = "^(?!vendor/)"


def test_a_look_ahead_scopes_the_emoji_scan_to_vendor(repo) -> None:
    write(repo, "src/a.md", f"mine {EMOJI_SMILE}\n")
    write(repo, "vendor/b.md", f"theirs {EMOJI_SMILE}\n")
    commit_all(repo)
    r = run_goh(repo, "emoji", "--exclude", TO_VENDOR)
    assert r.returncode == 1, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "vendor/b.md" in out and "src/a.md" not in out, out


# Not `secrets`: it takes no path exemption at all (2026-10-08, crates/goh/src/secrets.rs).
@pytest.mark.parametrize(
    "check", [["home-paths"], ["length", "--max", "500"], ["emoji"], ["no-allow"]]
)
def test_every_exclude_takes_the_python_dialect(repo, check) -> None:
    write(repo, "src/ok.txt", "fine\n")
    commit_all(repo)
    r = run_goh(repo, *check, "--exclude", TO_VENDOR)
    assert r.returncode != 2, r.stdout + r.stderr
    assert "look-around" not in r.stderr, r.stderr
