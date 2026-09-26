"""check_probes_pass.py -- the gate that runs every other gate's self-proof.

The hole it closes: `proven` was decided by a REGEX over a gate's source, so a probe that had
rotted into an exception still counted as proof its gate could fail. These tests drive the two
directions that matter -- a broken proof must go red, and prose mentioning `--probe` must not
count as one.
"""

import subprocess
import sys
import textwrap

import pytest

from conftest import REPO_ROOT as ROOT  # noqa: F401

sys.path.insert(0, str(ROOT / "checks"))
import check_probes_pass as gate  # noqa: E402

PASSING = """
    import sys
    if "--probe" in sys.argv:
        sys.exit(0)
    sys.exit(0)
"""

BROKEN = """
    import sys
    if "--probe" in sys.argv:
        raise SystemExit("the fixture this probe drove was renamed")
    sys.exit(0)
"""

PROSE_ONLY = '''
    """No self-proof yet -- a --probe is on the backlog."""
    import sys
    sys.exit(0)
'''


def _tree(tmp_path, gates):
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    for name, body in gates.items():
        (tools / name).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "flag,source",
    [
        ("--probe", 'if "--probe" in sys.argv:'),
        ("--selftest", 'parser.add_argument("--selftest", action="store_true")'),
        ("--break-probe", 'if "--break-probe" in sys.argv:'),
    ],
)
def test_every_spelling_of_a_self_proof_is_recognised(flag, source):
    assert gate.probe_flag(source) == flag


def test_prose_mentioning_probe_is_not_a_self_proof():
    assert gate.probe_flag('"""A --probe is on the backlog."""') is None


def test_discovery_counts_gates_and_finds_only_real_proofs(tmp_path):
    root = _tree(tmp_path, {
        "check_good.py": PASSING, "check_bare.py": PROSE_ONLY, "notagate.py": PASSING,
    })
    probes, total = gate.discover(str(root))
    assert total == 2, "check_*.py only -- notagate.py is not a gate"
    assert [p.endswith("check_good.py") for p, _ in probes] == [True]


def test_a_broken_self_proof_fails_the_gate(tmp_path):
    root = _tree(tmp_path, {"check_good.py": PASSING, "check_broken.py": BROKEN})
    assert gate.main(["--root", str(root)]) == 1


def test_a_tree_whose_proofs_all_pass_is_green(tmp_path):
    root = _tree(tmp_path, {"check_good.py": PASSING})
    assert gate.main(["--root", str(root)]) == 0


def test_a_repo_with_no_gates_is_reported_not_failed(tmp_path):
    assert gate.main(["--root", str(tmp_path)]) == 0


def test_gates_without_any_proof_warn_rather_than_fail(tmp_path):
    """Deliberate: this gate checks that existing proofs still WORK. Demanding a proof for every
    gate is the calibration ratchet's job, and it shrinks over time rather than blocking today."""
    root = _tree(tmp_path, {"check_bare.py": PROSE_ONLY})
    assert gate.main(["--root", str(root)]) == 0


def test_a_probe_that_hangs_is_a_failure_not_a_wait(tmp_path):
    root = _tree(tmp_path, {"check_hang.py": """
        import sys, time
        if "--probe" in sys.argv:
            time.sleep(30)
        sys.exit(0)
    """})
    okay, detail = gate.run_one(str(root / "tools" / "check_hang.py"), "--probe", str(root),
                                timeout=1)
    assert not okay and "timed out" in detail


def test_the_gates_own_self_proof_passes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "checks" / "check_probes_pass.py"), "--probe"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True,
                   capture_output=True)
    return path


def test_ignored_copies_are_not_discovered(tmp_path):
    """The empty-tree harness copies the gate directory to disk ignored for
    baselines. listdir would do real work on those copies and report an
    empty tree as proven; git scope must not see them."""
    root = _git_repo(tmp_path)
    checks = root / "checks"
    checks.mkdir()
    (checks / "check_copy.py").write_text(textwrap.dedent(PASSING).lstrip(), encoding="utf-8")
    (root / ".gitignore").write_text("checks/\n", encoding="utf-8")
    probes, total = gate.discover(str(root))
    assert (probes, total) == ([], 0)


def test_gate_dirs_present_but_nothing_scannable_refuses(tmp_path):
    """checks/ exists yet yields zero scannable gates: the discovery
    convention has moved, or the files moved. Passing would report the
    move as compliance. A repo with no gate dirs at all still passes."""
    root = _git_repo(tmp_path)
    (root / "checks").mkdir()
    (root / "README.md").write_text("# no gates\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    assert gate.main(["--root", str(root)]) == 1
