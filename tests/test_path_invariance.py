"""gates/path_invariance.py: a gate's verdict must not depend on how the tree's path is spelled
(BACKLOG 4.7).

ZeroThunder's SwiftLint 0.65.1 keyed its baseline on a path minus `getcwd`. `getcwd` is always the
canonical spelling (`/private/var/...`), while the walk took the spelling the run was handed
(`/var/...`, a symlink; `/System/Volumes/Data/...`, a firmlink), so from one spelling all 1034
recorded violations read as new and from another none did. The fixture gate here does the same
subtraction with `$PWD`, which is what a shell's `cd` sets: it passes under the canonical spelling
and fails under an alias. The check clones the committed tree to scratch and runs the gate under
every spelling of it the host offers; two verdicts are a finding.
"""

import subprocess
import sys

from conftest import REPO_ROOT, commit_all, write

CHECK = REPO_ROOT / "gates" / "path_invariance.py"

# Lists its files as `$PWD/<name>`, then takes `getcwd()` off the front to look them up in a baseline
# of relative names -- the subtraction SwiftLint did. Under an alias spelling `$PWD` is not under
# `getcwd()`, every name stays absolute, and every baselined finding reads as new.
CWD_KEYED = """\
import os, sys
cwd = os.getcwd()
here = os.environ.get("PWD", cwd)
found = [os.path.join(here, f) for f in sorted(os.listdir(here)) if f.endswith(".txt")]
rel = [f[len(cwd) + 1 :] if f.startswith(cwd + "/") else f for f in found]
known = set(open("baseline.list").read().split())
new = [r for r in rel if r not in known]
print("new:", *new)
sys.exit(1 if new else 0)
"""


def run(repo, *command):
    return subprocess.run(
        [sys.executable, str(CHECK), "--", *command],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_a_gate_keyed_on_the_spelling_is_named_with_each_verdict(repo):
    write(repo, "gate.py", CWD_KEYED)
    write(repo, "a.txt", "finding\n")
    write(repo, "baseline.list", "a.txt\n")
    commit_all(repo, "a cwd-keyed gate")
    r = run(repo, sys.executable, "gate.py")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "exit 0" in out and "exit 1" in out, out
    assert "/private/" in out and "/var/" in out, out


def test_a_gate_blind_to_the_spelling_gives_one_verdict(repo):
    write(repo, "gate.py", "import os, sys\nsys.exit(0 if os.path.isfile('a.txt') else 1)\n")
    write(repo, "a.txt", "x\n")
    commit_all(repo, "a path-blind gate")
    r = run(repo, sys.executable, "gate.py")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "spellings" in out, out


def test_a_gate_that_fails_everywhere_is_still_one_verdict(repo):
    write(repo, "gate.py", "import sys\nsys.exit(3)\n")
    commit_all(repo, "a failing gate")
    r = run(repo, sys.executable, "gate.py")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "exit 3" in out, out


def test_the_committed_tree_is_judged_not_the_working_tree(repo):
    write(repo, "gate.py", "import os, sys\nsys.exit(1 if os.path.exists('dirty.txt') else 0)\n")
    commit_all(repo, "gate")
    write(repo, "dirty.txt", "uncommitted\n")
    r = run(repo, sys.executable, "gate.py")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "exit 0" in out, out


def test_root_is_the_spelled_path_and_pwd_matches_it(repo):
    write(
        repo, "gate.py", "import os, sys\nsys.exit(0 if sys.argv[1] == os.environ['PWD'] else 1)\n"
    )
    commit_all(repo, "gate")
    r = run(repo, sys.executable, "gate.py", "{root}")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "exit 0" in out and "exit 1" not in out, out


def test_no_command_is_a_usage_error(repo):
    r = run(repo)
    assert r.returncode == 2, r.stdout + r.stderr


def test_help_prints_the_usage_and_exits_zero(repo):
    for flag in ("--help", "-h"):
        r = subprocess.run(
            [sys.executable, str(CHECK), flag],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "usage: path_invariance.py -- <gate command>" in r.stdout
        assert "Traceback" not in r.stderr


def test_a_command_that_cannot_run_is_named_not_a_traceback(repo):
    r = run(repo, "no-such-gate-command-anywhere")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "cannot run `no-such-gate-command-anywhere`" in r.stderr
    assert "Traceback" not in r.stderr
