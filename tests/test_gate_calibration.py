"""The calibration registry is read, and every claim it makes is checked against the estate.

SUPERSOTA R7: a registry nothing reads is a rumour. `checks/gate_calibration.json` held 23 entries
claiming gates had proven they can fail, and nothing in this repo read it -- `check_probes_pass.py`
swept what a gate DECLARES and could not see a claim about a gate it had stopped discovering. So
these tests pin three directions: the real registry holds against the real estate, a registry whose
claim is false goes red THROUGH THE GATE (not just through the module), and the reader's own
self-proof goes red when a rule is dropped from it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT as ROOT

sys.path.insert(0, str(ROOT / "checks"))
import _calibration as reader
import check_probes_pass as gate

CHECK = ROOT / "checks" / "check_probes_pass.py"


def run_gate(root, *args):
    return subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def estate(tmp_path, gates=None, registry=None):
    """A throwaway repo holding `checks/check_<name>.py` gates and, if asked, a registry."""
    root = tmp_path / "estate"
    (root / "checks").mkdir(parents=True, exist_ok=True)
    for name in gates or {}:
        (root / "checks" / f"check_{name}.py").write_text(gates[name], encoding="utf-8")
    if registry is not None:
        (root / "checks" / "gate_calibration.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )
    return root


PROBING = 'import sys\nif "--probe" in sys.argv:\n    print("clean")\nsys.exit(0)\n'
QUIET = '"""No self-proof here."""\nprint("clean")\n'


# One worker, so `ran_here` is computed once rather than once per worker that draws a test.
pytestmark = pytest.mark.xdist_group("gate_calibration")


def green_probes(root):
    """{repo-relative: flag} for the self-proofs this sweep runs to green, by the gate's own
    primitives -- so the test's idea of 'ran' cannot drift from the gate's."""
    ran = {}
    for path, flag in gate.discover(str(root))[0]:
        passed, _ = gate.run_one(path, flag, cwd=str(root))
        if passed:
            ran[os.path.relpath(path, str(root))] = flag
    return ran


# ── the real registry, against the real estate ────────────────────────────────


@pytest.fixture(scope="module")
def ran_here() -> dict:
    """The sweep over THIS repo, once: running every self-proof is ~8 s, and two tests read it."""
    return green_probes(ROOT)


def test_the_house_registry_holds_against_this_repo(ran_here):
    """The claim under test is the real file, not a fixture of it."""
    bad = reader.verify(str(ROOT), reader.load(str(ROOT)), set(ran_here))
    assert bad == [], bad


def test_every_checker_under_checks_is_in_the_house_registry():
    """6.21: a checker shipped with no registry entry is counted unproven by every cross-repo
    reader (game_asset_factory's ceiling went red on two) while nothing here noticed."""
    assert reader.unregistered(str(ROOT), reader.load(str(ROOT))) == []


def test_removing_one_real_entry_is_red():
    """Calibrated against the real file: drop one entry, and the gate names that checker."""
    data = reader.load(str(ROOT))
    del data["proven_by"]["git_env"]
    bad = reader.unregistered(str(ROOT), data)
    assert len(bad) == 1 and "check_git_env.py" in bad[0], bad
    assert any("check_git_env.py" in line for line in reader.verify(str(ROOT), data, set()))


def test_a_checker_missing_from_the_registry_is_red_through_the_gate(tmp_path):
    root = estate(
        tmp_path,
        {"prover": PROBING, "orphan": QUIET},
        {"proven_by": {"prover": "checks/check_prover.py --probe: red on a violation"}},
    )
    got = run_gate(root)
    assert got.returncode == 1, got.stdout + got.stderr
    assert "check_orphan.py" in got.stderr and "no entry" in got.stderr, got.stderr


def test_the_gate_agrees_with_the_reader_on_this_repo():
    assert run_gate(ROOT).returncode == 0


def test_every_registered_key_names_a_gate_this_repo_has():
    data = reader.load(str(ROOT))
    for key in list(data["proven_by"]) + list(data["known_unproven"]):
        native = reader.native_gate(str(ROOT), key)
        assert native or any((ROOT / rel).is_file() for rel in reader.gate_paths(str(ROOT), key)), (
            key
        )


def test_every_local_citation_names_a_proof_the_sweep_actually_ran(ran_here):
    """The load-bearing rule: a claim about a gate the sweep cannot see is a finding."""
    data = reader.load(str(ROOT))
    ran = ran_here
    local = {key: why for key, why in data["proven_by"].items() if why.startswith("checks/")}
    for key, why in local.items():
        cited = reader.CITED_FILE.match(why).group(1)
        assert cited in ran, f"{key}: cites {cited}, which the sweep did not run"
    # Since Phase N3 the local proofs are native test suites, run by the push gate's pytest step.
    suites = {key: why for key, why in data["proven_by"].items() if why.startswith("tests/")}
    assert suites, "the registry cites nothing local, so this test would prove nothing"
    assert reader.runs_pytest(str(ROOT)), "the cited suites are not run by this repo's push gate"
    for key, why in suites.items():
        cited = reader.CITED_FILE.match(why).group(1)
        assert (ROOT / cited).is_file(), f"{key}: cites {cited}, which does not exist"


def test_every_external_citation_names_a_prover_that_claims_the_gate():
    data = reader.load(str(ROOT))
    outside = 0
    for key, why in data["proven_by"].items():
        match = reader.CITED_FILE.match(why)
        if not match:
            continue
        path = reader.resolves_to(str(ROOT), match.group(1))
        if path is None or not os.path.relpath(path, str(ROOT)).startswith(".."):
            continue
        outside += 1
        claimed = reader.claims(path)
        assert claimed is None or key in claimed, key
    assert outside, "no entry cites another estate, so this test would prove nothing"


def test_a_prover_that_answers_nothing_has_not_said_rather_than_claimed_nothing(tmp_path):
    """A pytest module run as a script ignores `--proves` and prints nothing; that is silence, not
    a claim to prove no gate, so a citation to it is never contradicted on the empty answer."""
    silent = tmp_path / "test_silent.py"
    silent.write_text("def test_x():\n    pass\n")
    assert reader.claims(str(silent)) is None
    speaks = tmp_path / "prover.py"
    speaks.write_text("import sys\nif '--proves' in sys.argv:\n    print('md_links')\n")
    assert reader.claims(str(speaks)) == {"md_links"}


def test_a_prover_that_does_not_dispatch_on_proves_is_never_run(tmp_path):
    """G1b(a), 2026-10-24. routines' check_tabs_contract (before its b774d20) had no `--proves`:
    asked for its claims, it ignored the flag and ran its FULL path -- a cargo build and the real
    binary -- and its rc-0 stdout became the claim set. A prover that does not dispatch on the flag
    in its CODE is not asked at all, and the refusal says why."""
    marker = tmp_path / "full_path_ran"
    ignores = tmp_path / "check_ignores.py"
    ignores.write_text(
        '"""Run me with --proves to list my gates."""  # the flag in prose is not a dispatch\n'
        f"open({str(marker)!r}, 'w').write('built')\n"
        "print('check_ignores')\n"
    )
    claimed, reason = reader.claims_answer(str(ignores))
    assert claimed is None
    assert not marker.exists(), (
        "the prover's full path ran: --proves was asked of a prover that ignores it"
    )
    assert reason and "does not dispatch on --proves" in reason, reason
    assert reader.claims(str(ignores)) is None


def test_an_answer_that_is_not_a_list_of_keys_is_not_a_claim(tmp_path):
    """A prover that dispatches but answers in prose (a build log, a summary) has not stated a
    claim set; its lines are refused rather than read as registry keys."""
    chatty = tmp_path / "check_chatty.py"
    chatty.write_text(
        "import sys\n"
        "if '--proves' in sys.argv:\n"
        "    print('Compiling routines v0.4.0')\n"
        "    print('ok: 3 checks passed')\n"
    )
    claimed, reason = reader.claims_answer(str(chatty))
    assert claimed is None
    assert reason and "not a registry key" in reason, reason


def test_the_external_note_does_not_claim_what_a_silent_prover_never_said(tmp_path, monkeypatch):
    """The note said every external prover "lists each of them among what it proves" without
    asking any of them: a sentence the gate never checked. A prover that did not answer is named
    as one that did not, with its reason."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    silent = outside / "check_gate_canary.py"
    silent.write_text("print('a full run')\n")
    fast_init(outside)
    subprocess.run(["git", "-C", str(outside), "add", "."], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(
        [
            "git",
            "-C",
            str(outside),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(reader, "resolves_to", lambda _root, _cited: str(silent))
    monkeypatch.setattr(reader, "owner_of", lambda _path: "elsewhere/check_gate_canary.py")
    data = {"proven_by": {"md_links": "check_gate_canary.py -- a case over it"}}
    note = " ".join(reader.external_note(str(root), data))
    assert "lists each of them" not in note, note
    assert "did not answer --proves" in note and "md_links" in note, note


# ── a false claim goes red THROUGH THE GATE, not only through the module ──────


def test_a_claim_naming_a_gate_this_repo_does_not_have_is_red(tmp_path):
    root = estate(
        tmp_path,
        {"prover": PROBING},
        {"proven_by": {"phantom": "checks/check_phantom.py --probe: red on a violation"}},
    )
    got = run_gate(root)
    assert got.returncode == 1
    assert "names no gate" in got.stderr


def test_a_claim_naming_a_file_that_does_not_exist_is_red(tmp_path):
    root = estate(
        tmp_path,
        {"prover": PROBING},
        {"proven_by": {"prover": "checks/check_absent.py --probe: red"}},
    )
    got = run_gate(root)
    assert got.returncode == 1
    assert "does not exist" in got.stderr


def test_a_claim_to_a_proof_that_does_not_run_is_red(tmp_path):
    """The R7 shape: the checker declares no self-proof, so the sweep cannot see it, and the
    registry goes on calling it proven."""
    root = estate(
        tmp_path,
        {"quiet": QUIET},
        {"proven_by": {"quiet": "checks/check_quiet.py --probe: red on a violation"}},
    )
    got = run_gate(root)
    assert got.returncode == 1
    assert "did not run to green" in got.stderr


def test_a_sound_registry_is_green_through_the_gate(tmp_path):
    root = estate(
        tmp_path,
        {"prover": PROBING},
        {"proven_by": {"prover": "checks/check_prover.py --probe: red on a violation"}},
    )
    assert run_gate(root).returncode == 0


def test_a_repo_with_no_registry_is_reported_not_failed(tmp_path):
    """Every consumer repo runs this gate; only this one has a registry."""
    got = run_gate(estate(tmp_path, {"prover": PROBING}))
    assert got.returncode == 0
    assert "calibration registry" not in got.stdout


# ── the self-proof is calibrated: it goes RED when a rule is dropped ──────────


# Each rule as it stands in `verify`, as the `if` line that carries it. Replacing the condition
# with `False` removes exactly one rule, and the probe must notice that its own case for it has
# stopped firing. Driven through a COPY of the real module, so the case is about the file on disk.
RULES = (
    (
        "no-such-gate",
        "if not any(os.path.isfile(os.path.join(root, rel)) for rel in gate_paths(root, key)):",
    ),
    ("proof-did-not-run", "if flag and rel not in ran:"),
    ("in-no-commit", "if in_head is False:"),
    ("prover-claims", "if claimed is not None and key not in claimed:"),
    ("contradiction", "if key in entries:"),
    ("unregistered", 'if not ({stem, stem.removeprefix("check_")} & keys):'),
)


@pytest.mark.parametrize(("label", "rule"), RULES, ids=[r[0] for r in RULES])
def test_the_calibration_probe_goes_red_when_a_rule_is_dropped(tmp_path, label, rule):
    """A probe that cannot fail is worse than no probe: the claim it certifies is that gates in
    this estate have been SEEN to refuse something."""
    source = (ROOT / "checks" / "_calibration.py").read_text(encoding="utf-8")
    assert rule in source, f"the rule this case drops is not in the reader: {rule!r}"
    work = tmp_path / "checks"
    work.mkdir(parents=True)
    (work / "_calibration.py").write_text(
        source.replace(rule, f"if False and {rule[len('if ') :]}"), encoding="utf-8"
    )
    for name in ("_calibration_probe.py", "_gitutil.py", "_retired.py"):
        (work / name).write_text((ROOT / "checks" / name).read_text(encoding="utf-8"), "utf-8")
    got = subprocess.run(
        [sys.executable, str(work / "_calibration_probe.py")],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        stdin=subprocess.DEVNULL,
    )
    assert got.returncode == 1, f"the probe stayed green without the {label} rule:\n{got.stdout}"
    assert "case(s) wrong" in got.stderr, got.stderr


_COV_ROUTE = (
    "GOH_CI_STEPS='./gates/rust_gate.sh:make lint'\nGOH_COV_FLOOR_RUST=95\n"
    "GOH_COV_RUST_EXTERNAL='GOH_TEST_BIN=\"$GOH_COVERAGE_BIN_DIR/goh\" ./tools/pytest.sh'\n"
)


@pytest.mark.parametrize(
    ("steps", "red"),
    [
        ("GOH_CI_STEPS='make test'\n", True),
        ("GOH_CI_STEPS='./tools/pytest.sh'\n", False),
        # The suite run once, as the Rust coverage group's external command (G2e) -- this repo's.
        (_COV_ROUTE, False),
        # ...and not when no floor arms that group, or when the gate that runs it is not a step.
        (_COV_ROUTE.replace("GOH_COV_FLOOR_RUST=95\n", ""), True),
        (_COV_ROUTE.replace("./gates/rust_gate.sh:", ""), True),
    ],
)
def test_a_cited_test_suite_counts_only_when_the_push_gate_runs_pytest(tmp_path, steps, red):
    """A test file is a proof only if something RUNS it on every push; one that merely exists is a
    rumour with a filename. The push gate's step list is the one place that says so."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "gates").mkdir()
    (root / "gates" / "goh.sh").write_text("#!/bin/sh\n")
    (root / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    (root / ".gatesrc").write_text(steps)
    subprocess.run(["git", "init", "-q", str(root)], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    data = {"proven_by": {"no_emoji": "tests/test_x.py (native): red on a planted glyph"}}
    bad = reader.verify(str(root), data, set())
    assert bool(bad) is red, bad
    if red:
        assert "does not run" in bad[0], bad
