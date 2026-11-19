"""lib/declared_reads.py: a test that reads the tree at run time is chosen by a change to what it
reads (GOH.SELECTAUDIT), and the ratchet that keeps the reads the selector cannot see declared.

Each real-tree case is a selection miss the selection-miss ledger found (lib/selection_miss.py)
or a read the naming rule cannot see: a change under a fixture a test copies whole chose nothing
on 2026-11-17's selector.
"""

from __future__ import annotations

import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests

from lib import declared_reads as dr, source_closure

PENDING = REPO_ROOT / "gates" / "undeclared_reads.txt"
SCANNERS = REPO_ROOT / "gates" / "undeclared_scanners.txt"


# The day's two selection misses are pinned where they were closed: lib/gate_identity.py ->
# tests/test_rust_gate.py (GOH.BUILDINCLUDES) in test_affected_tests.py's
# test_the_rust_gate_test_is_chosen_for_a_gate_identity_change_on_the_real_tree, a Rust change ->
# the binary's runners (GOH.GRACESHAPES) in test_affected_binary.py.
CASES = [
    # a fixture copied whole: `shutil.copytree(FIXTURE_PROJECT, ...)`
    ("tests/fixtures/swift_baseline_project/.swiftlint.yml", "tests/test_swift_gate_baseline.py"),
    # a fixture tree walked: `FIXTURES.glob(...)`
    ("tests/fixtures/prose_mask/call_code.py", "tests/test_prose_mask_parity.py"),
    # a registry read at run time (GOH.ADAPTLANES' red was in a test chosen for it)
    ("gates/verdict_free_keys.txt", "tests/test_verdict_free_keys.py"),
    # GOH.SELECTLOCKORDER, each found by tracing what the test opened (2026-11-19). A scanner
    # pointed at the root, declaring what it scans: GOH.LANDLOGPRUNE's new lib file with a raw
    # flock chose no lock-order test, and the batch gate went red
    ("lib/state_bounds.py", "tests/test_check_lock_order.py"),
    # a tests/ helper's copy of the gates (tests/test_proven.py's fixture), read by its importers
    ("checks/_canary.py", "tests/test_proven_made.py"),
    ("lib/budget.py", "tests/test_kept_rewrite.py"),
    # a file under a spelled directory, joined with a non-literal part (`FIXTURE / f"{stem}.log"`)
    ("tests/fixtures/selection_misses/attempts.json", "tests/test_selection_miss.py"),
    (
        "tests/fixtures/test_registration/cmake_ok/tests/helpers.h",
        "tests/test_test_registration.py",
    ),
]


def test_a_change_to_what_a_test_reads_chooses_it_whole(monkeypatch):
    """Each change ALONE chooses its reader whole; one graph of the tree serves every case (its
    caches are by file, never by the change)."""
    graph = affected_tests.Closure(REPO_ROOT)
    monkeypatch.setattr(affected_tests, "Closure", lambda root: graph)
    missed = [
        (changed, reader)
        for changed, reader in CASES
        if reader not in affected_tests.affected(REPO_ROOT, [changed])
    ]
    assert not missed


def test_the_reads_a_spelling_gives_away_and_the_declared_ones(tmp_path):
    (tmp_path / "fix" / "proj").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    text = (
        'TREE_READS = ("docs/*.md", "gates/")\n'
        'PROJ = REPO_ROOT / "fix" / "proj"\n'
        "def test_a(tmp_path):\n"
        "    shutil.copytree(PROJ, tmp_path / 'p')\n"
        '    for f in (REPO_ROOT / "src").glob("*.rs"):\n'
        "        f.read_text()\n"
        '    (REPO_ROOT / "missing").iterdir()\n'
    )
    assert dr.declared(text) == ("docs/*.md", "gates/")
    assert dr.inferred(text, tmp_path) == {"fix/proj/", "src/*.rs"}
    assert not dr.dynamic(text)
    assert dr.dynamic('p = REPO_ROOT / "lib" / name\n')
    tests = {"tests/test_r.py": text, "tests/test_other.py": "def test_b():\n    pass\n"}
    for path, want in [("fix/proj/a/b.txt", "fix/proj/"), ("src/x.rs", "src/*.rs"),
                       ("docs/map.md", "docs/*.md"), ("gates/x.sh", "gates/")]:  # fmt: skip
        chosen: dict[str, str] = {}
        dr.choose(tmp_path, tests, [path], chosen)
        assert chosen == {"tests/test_r.py": f"reads {want} ({path})"}, path
    chosen = {}
    dr.choose(tmp_path, tests, ["src/x.py", "fixx/proj/a"], chosen)
    assert chosen == {}


def test_a_scanner_pointed_at_the_root_lends_its_reads_and_a_helper_its_own(tmp_path):
    """Through a chain of tests/ files: a helper's reads are the test's; a scanner's TREE_READS
    are the test's only when the file reaching it points it at the root (argv, positional
    argument, cwd=), never at a fixture, and never through a location (`GOH_DIR=str(ROOT)`)."""
    for rel, text in {
        "checks/scan.py": 'TREE_READS = ("lib/",)\n',
        "tests/_copy.py": 'TREE_READS = ("gates/",)\n',
        "tests/test_root.py": "import scan\ndef test_a():\n    scan.check(REPO_ROOT)\n",
        "tests/test_argv.py": 'CHECK = "checks/scan.py"\nARGV = [CHECK, str(REPO_ROOT)]\n',
        "tests/test_fixture.py": "import scan\ndef test_b(tmp_path):\n    scan.check(tmp_path)\n",
        "tests/test_env.py": "import scan\nENV = hermetic_env(GOH_DIR=str(REPO_ROOT))\n",
        "tests/test_helper.py": "import _copy\ndef test_c():\n    pass\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    graph = affected_tests.Closure(tmp_path)
    tests = {r: graph.texts[r] for r in graph.texts if r.startswith("tests/test_")}
    chosen: dict[str, str] = {}
    dr.choose(tmp_path, tests, ["lib/x.py", "gates/y.sh"], chosen, graph)
    assert chosen == {
        "tests/test_root.py": "reads lib/ (lib/x.py) through checks/scan.py",
        "tests/test_argv.py": "reads lib/ (lib/x.py) through checks/scan.py",
        "tests/test_helper.py": "reads gates/ (gates/y.sh) through tests/_copy.py",
    }


def _tests() -> dict[str, str]:
    return {
        f"tests/{p.name}": p.read_text(encoding="utf-8")
        for p in sorted((REPO_ROOT / "tests").glob("test_*.py"))
    }


def test_every_test_joining_a_path_it_does_not_spell_declares_its_reads():
    """A test joining REPO_ROOT with a variable (`REPO_ROOT / rel`) reads what no spelling gives
    away: it declares TREE_READS, or is on gates/undeclared_reads.txt -- the list of the 70 found on
    2026-11-17, which only shrinks (a listed file that declares, or joins no more, is stale)."""
    pending = set(PENDING.read_text().split())
    found = {rel for rel, text in _tests().items() if dr.dynamic(text) and not dr.declared(text)}
    assert not found - pending, f"declare TREE_READS = (...) in: {sorted(found - pending)}"
    assert not pending - found, f"stale on {PENDING.name}, delete: {sorted(pending - found)}"


def test_every_scanner_a_test_points_at_the_root_declares_what_it_scans():
    """A checks/ scanner listing the tracked tree, pointed at the root by a test that is not a
    standing tree reader, declares TREE_READS -- what it scans is the test's reads -- or the pair
    is on gates/undeclared_scanners.txt, today's traced exceptions, which only shrinks."""
    graph = affected_tests.Closure(REPO_ROOT)
    tests = _tests()
    standing = affected_tests.tree_readers(REPO_ROOT, tests)
    found = {
        f"{rel} {node}"
        for rel in tests
        if rel not in standing
        for node in dr.driven(graph, rel)
        if node.startswith("checks/")
        and dr.lists_tree(graph, node)
        and not dr.declared(graph.texts[node])
    }
    listed = {ln for ln in SCANNERS.read_text().splitlines() if ln and not ln.startswith("#")}
    assert not found - listed, (
        f"declare TREE_READS = (...) in the scanner: {sorted(found - listed)}"
    )
    assert not listed - found, f"stale on {SCANNERS.name}, delete: {sorted(listed - found)}"


def test_every_declared_read_matches_a_file_of_the_tree():
    tracked = source_closure.tree_files(REPO_ROOT)
    scanners = {f"checks/{p.name}": p.read_text() for p in (REPO_ROOT / "checks").glob("*.py")}
    for rel, text in {**_tests(), **scanners}.items():
        for pattern in dr.declared(text):
            assert any(dr.matches(pattern, t) for t in tracked), f"{rel}: {pattern} reads nothing"
