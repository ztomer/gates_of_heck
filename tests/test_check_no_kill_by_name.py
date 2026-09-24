"""checks/check_no_kill_by_name.py — no process kill by name in tracked code.

The class behind the 2026-09-23 21:58 stop of the necrohand campaign: a test
harness's `pkill -9 -f` cleanup matched another session's Gemini browser and
SIGKILLed it. The gate must go RED on every spelling it claims (pkill, killall,
a pgrep that feeds a kill, and the Python argv and Rust Command forms), pass the
owner-scoped forms, and ignore comments, docstrings, prose and `command -v`. Its
allowlist must be a ratchet: an entry excuses one exact line in one file, needs
a reason, and fails once it goes stale.

The command names are assembled from parts below because this gate scans this
file too: a fixture line naming the command is a kill by name to the gate.
"""

import json
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, stage, write

CHECK = "checks/check_no_kill_by_name.py"
PK, KA, PG = "p" + "kill", "kill" + "all", "p" + "grep"


def run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(REPO_ROOT / CHECK), *args],
                          cwd=repo, capture_output=True, text=True)


def allow(repo: Path, *entries: dict) -> None:
    write(repo, "kill_by_name_allow.json", json.dumps({"entries": list(entries)}))


def test_every_claimed_shape_is_red_and_named(repo):
    shapes = {
        "a.sh": f'{PK} -9 -f "camoufox -no-remote" 2>/dev/null || true',
        "b.sh": f"{KA} Firefox",
        # killall's -s is "show", not a session: no flag makes a killall owner-scoped
        "b2.sh": f"{KA} -s -g Firefox",
        "c.sh": f"kill $({PG} -f worker)",
        "d.sh": f"{PG} -f worker | xargs kill",
        "e.py": f'subprocess.run(["{PK}", "-9", "-f", pattern], check=False)',
        "f.rs": f'let _ = std::process::Command::new("{KA}").arg("Dock").status();',
        "Makefile": f"clean:\n\t{PK} -x MyApp || true",
        "tool": f"#!/bin/sh\n{PK} -x MyApp",
        "ci.yml": f"      - run: {PK} -f server || true",
    }
    for name, text in shapes.items():
        write(repo, name, text + "\n")
    stage(repo, *shapes)
    r = run(repo, "--staged")
    assert r.returncode == 1, r.stdout
    for name in shapes:
        assert f"{name}:" in r.stdout, (name, r.stdout)
    assert f"{len(shapes)} process kill(s) by name" in r.stdout


def test_owner_scoped_forms_pass(repo):
    write(repo, "ok.sh",
          f'{PK} -TERM -P "$pid" 2>/dev/null || true\n'
          f'{PK} -g "$pgid"\n'
          f"{PK} --parent $$ -f worker\n"
          f"kill $({PG} -P $$ worker)\n"
          f"if command -v {PK} >/dev/null 2>&1; then :; fi\n"
          'kill -TERM "$pid"\n')
    write(repo, "ok.py", f'subprocess.run(["{PK}", "-P", str(os.getpid())])\nos.killpg(pgid, 9)\n')
    stage(repo, "ok.sh", "ok.py")
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout
    assert "2 staged code files, no kill by name" in r.stdout


def test_comments_docstrings_and_prose_are_not_kills(repo):
    write(repo, "doc.py",
          f'"""Never {PK} -f by name here."""\n'
          f"x = 1  # the old code ran {PK} -9 -f camoufox\n"
          "def f():\n"
          f'    """{KA} Firefox would kill the owner\'s browser."""\n')
    write(repo, "doc.sh", f"# {PK} -f x was the bug\necho ok  # not {KA}\n")
    write(repo, "doc.rs", f"// {KA} is forbidden\nfn main() {{}} // no {PK}\n")
    write(repo, "NOTES.md", f"Run `{PK} -f camoufox` and you kill everyone's browser.\n")
    stage(repo, "doc.py", "doc.sh", "doc.rs", "NOTES.md")
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout
    assert "3 staged code files" in r.stdout


def test_a_word_inside_a_longer_name_is_not_the_command(repo):
    write(repo, "names.py", f"skill_{PK}_x = 1\nimport {PK}er\nfrom tools import {PK}_helper\n")
    stage(repo, "names.py")
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout


def test_an_entry_excuses_exactly_its_line_and_no_other(repo):
    write(repo, "run.sh", f"{PK} -x MyApp\n{PK} -f helper\n")
    allow(repo, {"path": "run.sh", "line": f"{PK} -x MyApp", "reason": "dev relaunch",
                 "status": "legitimate"})
    commit_all(repo)
    r = run(repo)
    assert r.returncode == 1, r.stdout
    assert "run.sh:2:" in r.stdout and "run.sh:1:" not in r.stdout


def test_an_entry_does_not_cover_the_same_line_in_another_file(repo):
    write(repo, "run.sh", f"{PK} -x MyApp\n")
    write(repo, "other.sh", f"{PK} -x MyApp\n")
    allow(repo, {"path": "run.sh", "line": f"{PK} -x MyApp", "reason": "dev relaunch"})
    commit_all(repo)
    r = run(repo)
    assert r.returncode == 1 and "other.sh:1:" in r.stdout, r.stdout


def test_a_stale_entry_fails_and_a_duplicate_is_stale(repo):
    write(repo, "run.sh", f"{PK} -x MyApp\n")
    entry = {"path": "run.sh", "line": f"{PK} -x MyApp", "reason": "dev relaunch"}
    allow(repo, entry, entry)
    commit_all(repo)
    r = run(repo)
    assert r.returncode == 1 and "1 stale entr" in r.stdout, r.stdout
    allow(repo, entry)
    write(repo, "run.sh", "kill -TERM \"$pid\"\n")
    commit_all(repo)
    r = run(repo)
    assert r.returncode == 1 and "1 stale entr" in r.stdout, r.stdout


def test_an_allowlisted_tree_passes_and_counts_its_debt(repo):
    write(repo, "run.sh", f"{PK} -x MyApp\n{KA} Dock\n")
    allow(repo,
          {"path": "run.sh", "line": f"{PK} -x MyApp", "reason": "seeded", "status": "unreviewed"},
          {"path": "run.sh", "line": f"{KA} Dock", "reason": "the Dock is one per user",
           "status": "legitimate"})
    commit_all(repo)
    r = run(repo)
    assert r.returncode == 0, r.stdout
    assert "2 allowlisted" in r.stdout
    assert "1 of them 'unreviewed'" in r.stdout


def test_an_entry_without_a_reason_or_with_a_bad_status_is_refused(repo):
    write(repo, "run.sh", f"{PK} -x MyApp\n")
    allow(repo, {"path": "run.sh", "line": f"{PK} -x MyApp", "reason": "  "})
    commit_all(repo)
    r = run(repo)
    assert r.returncode == 1 and "needs a non-empty path, line and reason" in r.stdout, r.stdout
    allow(repo, {"path": "run.sh", "line": f"{PK} -x MyApp", "reason": "x", "status": "fine"})
    commit_all(repo)
    r = run(repo)
    assert r.returncode == 1 and "status must be one of" in r.stdout, r.stdout


def test_malformed_allowlist_is_refused_not_ignored(repo):
    write(repo, "kill_by_name_allow.json", "{not json")
    commit_all(repo)
    r = run(repo)
    assert r.returncode == 1 and "not valid JSON" in r.stdout, r.stdout


def test_staged_scope_judges_only_staged_entries(repo):
    write(repo, "run.sh", f"{PK} -x MyApp\n")
    allow(repo, {"path": "run.sh", "line": f"{PK} -x MyApp", "reason": "dev relaunch"})
    commit_all(repo)
    write(repo, "clean.sh", "echo hi\n")
    stage(repo, "clean.sh")
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout


def test_exclude_skips_a_vendored_tree(repo):
    write(repo, "vendor/x.sh", f"{KA} thing\n")
    write(repo, "own.sh", "echo ours\n")
    commit_all(repo)
    assert run(repo).returncode == 1
    r = run(repo, "--exclude", "^vendor/")
    assert r.returncode == 0, r.stdout


def test_an_empty_scope_refuses_to_report_clean_but_an_empty_commit_passes(repo):
    r = run(repo)
    assert r.returncode == 1 and "refusing to report clean over zero code files" in r.stdout, r.stdout
    write(repo, "NOTES.md", "prose only\n")
    stage(repo, "NOTES.md")
    r = run(repo, "--staged")
    assert r.returncode == 0 and "nothing staged" in r.stdout, r.stdout


def test_unparseable_python_falls_back_to_the_line_scan(repo):
    write(repo, "broken.py", f"def (:\n    subprocess.run(['{PK}', '-f', 'x'])\n")
    stage(repo, "broken.py")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "broken.py:2:" in r.stdout, r.stdout
