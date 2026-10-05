"""check_probes_pass.py -- the gate that runs every other gate's self-proof.

The hole it closes: `proven` was decided by a REGEX over a gate's source, so a probe that had
rotted into an exception still counted as proof its gate could fail. These tests drive the two
directions that matter -- a broken proof must go red, and prose mentioning `--probe` must not
count as one.
"""

import os
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


# A BASH GATE DISPATCHES ON A POSITIONAL PARAMETER, and every form below scored as carrying no
# self-proof until 2026-10-02. The cost was a real miscount: ZeroThunder's check_gate_calibration
# reported `P4 fresh_install` uncalibrated for a session, and the remedy a reader reaches for --
# delete the working probe, or record a person -- destroys the evidence and fixes nothing.
@pytest.mark.parametrize(
    "flag,source",
    [
        ("--break-probe", 'if [[ "${1:-}" == "--break-probe" ]]; then\n  exit 3\nfi'),
        ("--selftest", '[ "$1" = "--selftest" ] && exit 3'),
        ("--probe", 'case "$1" in\n  --probe) exit 3 ;;\nesac'),
        (
            "--break-probe",
            '#!/usr/bin/env bash\nif [ "${1:-}" == "--break-probe" ]; then exit 3; fi',
        ),
    ],
)
def test_a_shell_self_proof_is_recognised(flag, source):
    """A shell dispatch is as real as a python one -- it is the same instrument, not a lesser one."""
    assert gate.probe_flag(source) == flag


@pytest.mark.parametrize(
    "source",
    [
        "# a gate that ships no --break-probe is unproven",
        'echo "--probe" ; :',
        "# --selftest disables one thing it detects; see the docstring",
    ],
)
def test_shell_prose_naming_a_flag_is_still_not_a_self_proof(source):
    """The loosening that reads shell must not become a loosening that reads DOCUMENTATION.

    Anchoring the shell form to a COMPARISON is what keeps this true, and it is the exact place a
    careless matcher lets a gate certify itself by documenting the convention.
    """
    assert gate.probe_flag(source) is None


def test_a_shell_gate_is_discovered_and_run(tmp_path):
    """A `check_*.sh` is a gate. Before this, discovery matched `check_*.py` only, so every shell
    gate in the estate was invisible to this gate -- the fix to `probe_flag` would otherwise have
    been INERT CONFIG, which is the same sin as a gate that reads like a check and never runs."""
    root = _tree(
        tmp_path,
        {
            "check_shell_gate.sh": (
                "#!/usr/bin/env bash\n"
                'if [[ "${1:-}" == "--break-probe" ]]; then echo red >&2; exit 1; fi\n'
                "exit 0\n"
            ),
        },
    )
    passed, total = gate.discover(root)
    assert total == 1, "a shell gate must be counted as a gate at all"
    # The path is absolute by the time discovery returns it; the flag is what is under test.
    assert [flag for _path, flag in passed] == ["--break-probe"]
    assert os.path.basename(passed[0][0]) == "check_shell_gate.sh"


def test_a_broken_shell_probe_fails_the_gate(tmp_path):
    """And the discovery is not decoration: a shell probe that lies must go red, run under bash."""
    root = _tree(
        tmp_path,
        {
            "check_liar.sh": (
                "#!/usr/bin/env bash\n"
                'if [[ "${1:-}" == "--break-probe" ]]; then echo "claims red, exits 0"; exit 0; fi\n'
                "exit 0\n"
            ),
        },
    )
    probes, _total = gate.discover(root)
    assert probes, "the liar must be discovered"
    passed, detail = gate.run_one(probes[0][0], probes[0][1], cwd=root)
    assert passed, f"a bash probe that exits 0 passes; the FAILURE case is a non-zero exit"

    (tmp_path / "tools" / "check_liar.sh").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "--break-probe" ]]; then echo broken >&2; exit 7; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    passed, detail = gate.run_one(probes[0][0], probes[0][1], cwd=root)
    assert not passed and "exit 7" in detail


def test_a_python_shebang_on_a_shell_extension_still_runs_as_python(tmp_path):
    """A `check_*.sh` carrying a python shebang is a real thing in this estate, and running it
    under bash produces a syntax error that reads like a broken probe."""
    root = _tree(
        tmp_path,
        {
            "check_odd.sh": (
                "#!/usr/bin/env python3\nimport sys\nif '--probe' in sys.argv:\n    raise SystemExit(1)\n"
            ),
        },
    )
    probes, _total = gate.discover(root)
    assert probes
    passed, detail = gate.run_one(probes[0][0], probes[0][1], cwd=root)
    assert not passed and "exit 1" in detail, detail


def test_discovery_counts_gates_and_finds_only_real_proofs(tmp_path):
    root = _tree(
        tmp_path,
        {
            "check_good.py": PASSING,
            "check_bare.py": PROSE_ONLY,
            "notagate.py": PASSING,
        },
    )
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
    root = _tree(
        tmp_path,
        {
            "check_hang.py": """
        import sys, time
        if "--probe" in sys.argv:
            time.sleep(30)
        sys.exit(0)
    """
        },
    )
    okay, detail = gate.run_one(
        str(root / "tools" / "check_hang.py"), "--probe", str(root), timeout=1
    )
    assert not okay and "timed out" in detail


def test_the_gates_own_self_proof_passes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "checks" / "check_probes_pass.py"), "--probe"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ── where the R3 estate sweep runs, and where it does not (2026-10-05) ───────
#
# The sweep answers one question: do the checkers in THIS `checks/` still go red on a plant inside a
# real consumer corpus. It used to run on every invocation whatever `--root` named, so a run against
# a fixture estate holding one trivial gate cost 53.7 s of which 53.66 s was that sweep -- and five
# tests asserting a FIXTURE's registry was sound would have gone red if any of eight unrelated
# consumer repos changed. These pin the scoping and, just as importantly, that the sweep still runs
# where its subject is: a cost fix that quietly switched the gate off would be the worse failure.


def test_the_sweep_runs_for_the_repo_that_owns_the_checkers(monkeypatch, capsys):
    """The half that keeps the cost fix from becoming a switched-off gate: in THIS repo the sweep
    is still reached. `subprocess.run` is stubbed so the assertion is about the WIRING, and the
    sweep's substance is proved where it belongs, by `tests/test_estate_corpus.py` entry by entry."""
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = list(command)
        return subprocess.CompletedProcess(command, 0, "8/8 checker(s) went red on a plant", "")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.corpus_sweep(str(ROOT)) is True
    assert seen["command"][-1].endswith("check_estate_corpus.py"), seen["command"]
    assert "went red on a plant" in capsys.readouterr().out


def test_a_foreign_root_skips_the_sweep_and_says_so(tmp_path, monkeypatch, capsys):
    """R4's rule, in the shape this fix could have broken: a step that does not run must be VISIBLE.
    A green line over a sweep that never ran is indistinguishable from a sweep that passed."""

    def explode(*args, **kwargs):
        raise AssertionError("the estate sweep ran against a fixture root")

    monkeypatch.setattr(gate.subprocess, "run", explode)
    assert gate.corpus_sweep(str(tmp_path)) is True
    said = capsys.readouterr().out
    assert "NOT RUN" in said, said
    assert str(tmp_path) in said, said
    assert "--estate" in said, said


def test_estate_forces_the_sweep_anywhere(tmp_path, monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["ran"] = True
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.corpus_sweep(str(tmp_path), force=True) is True
    assert seen.get("ran") is True, "--estate did not force the sweep"


def test_the_scope_test_names_the_repo_and_the_alternative(tmp_path):
    assert gate.estate_in_scope(str(ROOT)) is True
    assert gate.estate_in_scope(str(tmp_path)) is False
    assert gate.estate_in_scope(None) is False
    # A path that reaches this repo the long way is the SAME repo, not a foreign one.
    assert gate.estate_in_scope(str(ROOT / "checks" / "..")) is True


def _git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "t"], check=True, capture_output=True
    )
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


def test_a_tools_dir_holding_only_other_scripts_is_nothing_to_prove(tmp_path):
    """The installer writes `tools/gate.sh` into every repo. A `tools/` whose
    tracked files are all something else is a repo with no gates of its
    own, never a blind discovery -- refusing it blocked every such push."""
    root = _git_repo(tmp_path)
    (root / "tools").mkdir()
    (root / "tools" / "gate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    assert gate.main(["--root", str(root)]) == 0
    # ...while an ignored check_*.py beside it is the harness's shape, and refuses.
    (root / "tools" / "check_copy.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text("tools/check_copy.py\n", encoding="utf-8")
    assert gate.main(["--root", str(root)]) == 1


def test_a_self_proof_outside_the_check_convention_is_named_not_run(tmp_path, capsys):
    """ZoneWM, 2026-10-05: `idle_probe.py`, `space_hotkey_probe.py`, `visualizer_colour_probe.py` and
    `input_lock.py` declare `--probe` and nothing ran them, silently. Discovery stays by NAME -- a
    `check_*` file is a repo declaring a headless gate, and `input_lock.py --probe` locks the
    owner's real input -- so widening it by flag would hand the keyboard to a pre-push hook. What
    must not happen is the silence: the gate names the self-proofs it does not run."""
    root = _tree(tmp_path, {"check_good.py": PASSING, "idle_probe.py": BROKEN})
    assert gate.main(["--root", str(root), "--no-corpus"]) == 0, "a non-gate's probe was run"
    out = capsys.readouterr()
    text = out.out + out.err
    assert "tools/idle_probe.py" in text and "NOT run" in text, text


def test_self_proofs_run_concurrently_and_report_in_order(tmp_path, capsys):
    """Every consumer's pre-push runs this, and it ran the probes one after another: 8.1 s summed
    here for a 2.0 s longest probe (2026-10-05). Each probe below can only finish if the OTHER is
    running at the same time -- a serial runner times both out -- and a failure is still reported
    against the probe that failed, in discovery order."""
    meet = tmp_path / "meet"
    meet.mkdir()

    def rendezvous(me, other):
        return f"""
            import os, sys, time
            if "--probe" in sys.argv:
                open(os.path.join({str(meet)!r}, {me!r}), "w").close()
                deadline = time.monotonic() + 8
                while not os.path.exists(os.path.join({str(meet)!r}, {other!r})):
                    if time.monotonic() > deadline:
                        sys.exit("the other probe never ran alongside this one")
                    time.sleep(0.05)
                sys.exit(0)
            sys.exit(0)
        """

    root = _tree(
        tmp_path,
        {
            "check_a.py": rendezvous("a", "b"),
            "check_b.py": rendezvous("b", "a"),
            "check_c.py": BROKEN,
        },
    )
    assert gate.main(["--root", str(root), "--no-corpus"]) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "check_c.py --probe is BROKEN" in text, text
    assert "check_a.py --probe is BROKEN" not in text and "check_b.py" not in text, text
