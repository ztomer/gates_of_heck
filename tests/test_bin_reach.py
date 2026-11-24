"""A Rust change chooses the binary tests its stored profiles reach (GOH.BINREACH, lib/bin_reach).

A one-file crates/ change chose every test that runs the binary for a subcommand it can reach:
1,452-4,180 tests at the enqueue. Each binary test's stored profile names the functions it entered,
so the enqueue now chooses only the tests whose profile reaches the change. Pinned here, on a
fixture crate and a fixture profile store: a profile that misses the change leaves its test out; a
changed CONSTANT -- no executed region of its own -- still chooses the test whose profile reaches
only the file that `use`s it; a test with NO stored profile is always chosen; a test measured
starting no goh is not; the gate's mode narrows nothing; a v0 symbol is read for the module its
code sits in; and the 1-in-10 audit runs what was left out, counts its reds as disagreements, and
`goh land-profile` prints the count.
"""

from __future__ import annotations

import json
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

from lib import bin_reach, bin_reach_audit, durations, land_profile, v0_home

MAIN = """mod cli;
mod limits;
mod user;
mod other;
fn main() {
    let command = cli::parse();
    std::process::exit(run_core(command));
}
fn run_core(command: Commands) -> i32 {
    match command {
        Commands::Go => user::run(),
        Commands::Away => other::run(),
    }
}
"""
CRATE = {
    "crates/goh/src/main.rs": MAIN,
    "crates/goh/src/cli.rs": "pub enum Commands {\n    Go,\n    Away,\n}\n"
    "pub fn parse() -> Commands { Commands::Go }\n",
    "crates/goh/src/limits.rs": "pub const LIMIT: u32 = 3;\n",
    "crates/goh/src/user.rs": "use crate::limits::LIMIT;\npub fn run() -> i32 { LIMIT as i32 }\n",
    "crates/goh/src/other.rs": "pub fn run() -> i32 { 0 }\n",
    "Cargo.toml": "",
}
GO = "tests/test_go.py"
TESTS = {
    GO: "def test_reads_the_limit(goh):\n    run([goh, 'go'])\n\n\n"
    "def test_only_the_entry(goh):\n    run([goh, 'go'])\n\n\n"
    "def test_never_profiled(goh):\n    run([goh, 'go'])\n\n\n"
    "def test_starts_no_goh(goh):\n    pass\n",
}
# each node's profile, as the module paths its entered functions live in (bin_reach.index's form)
PROFILES = {
    f"{GO}::test_reads_the_limit": ["goh", "goh::user", "std::rt"],
    f"{GO}::test_only_the_entry": ["goh", "goh::cli"],
    f"{GO}::test_starts_no_goh": None,  # measured: ran no goh (the carry record's `measured`)
}
NODES = [*PROFILES, f"{GO}::test_never_profiled"]


def _tree(tmp_path, monkeypatch, measured_free: bool = True):
    root = tmp_path / "repo"
    for rel, text in {**CRATE, **TESTS}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    store = tmp_path / "reuse"
    key = store / "profstore" / "k1"
    key.mkdir(parents=True)
    reach = {}
    for node, homes in PROFILES.items():
        if homes is not None:
            (key / f"{bin_reach.node_hash(node)}.profdata").write_bytes(b"")
            reach[bin_reach.node_hash(node)] = homes
    (store / "profreach").mkdir()
    (store / "profreach" / "k1.json").write_text(json.dumps({"n": len(reach), "reach": reach}))
    table = tmp_path / "goh.db"
    durations.record({"dur": dict.fromkeys(NODES, 1.0)}, db=table)
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(table))
    monkeypatch.delenv("GOH_BINREACH", raising=False)
    free = frozenset({f"{GO}::test_starts_no_goh"} if measured_free else ())
    monkeypatch.setattr(bin_reach, "free", lambda repo, newest=0: free)
    return root


def _chosen(root, changed: list[str], enqueue: bool = True) -> set[str]:
    return {t for t, why in at.affected(root, changed, enqueue=enqueue).items() if t.startswith(GO)}


def test_a_changed_constant_chooses_the_test_reaching_only_the_file_that_uses_it(
    tmp_path, monkeypatch
) -> None:
    """limits.rs holds a constant: no profile ever enters it. user.rs `use`s it, so the test whose
    profile reaches user.rs is chosen; the one entering only main.rs and cli.rs is left out."""
    root = _tree(tmp_path, monkeypatch)
    got = _chosen(root, ["crates/goh/src/limits.rs"])
    assert f"{GO}::test_reads_the_limit" in got, sorted(got)
    assert f"{GO}::test_only_the_entry" not in got, sorted(got)
    assert f"{GO}::test_only_the_entry" in at.SKIPPED, at.SKIPPED


def test_a_test_with_no_stored_profile_is_always_chosen(tmp_path, monkeypatch) -> None:
    root = _tree(tmp_path, monkeypatch)
    for changed in (["crates/goh/src/limits.rs"], ["crates/goh/src/user.rs"]):
        assert f"{GO}::test_never_profiled" in _chosen(root, changed), changed


def test_a_test_measured_starting_no_goh_is_left_out_unless_unmeasured(tmp_path, monkeypatch):
    root = _tree(tmp_path, monkeypatch)
    assert f"{GO}::test_starts_no_goh" not in _chosen(root, ["crates/goh/src/user.rs"])
    root = _tree(tmp_path / "unmeasured", monkeypatch, measured_free=False)
    assert f"{GO}::test_starts_no_goh" in _chosen(root, ["crates/goh/src/user.rs"])


def test_the_gate_mode_and_the_kill_switch_narrow_nothing(tmp_path, monkeypatch) -> None:
    root = _tree(tmp_path, monkeypatch)
    assert _chosen(root, ["crates/goh/src/limits.rs"], enqueue=False) == {GO}
    monkeypatch.setenv("GOH_BINREACH", "off")
    assert _chosen(root, ["crates/goh/src/limits.rs"]) == {GO}


def test_a_change_the_graph_cannot_place_narrows_nothing(tmp_path, monkeypatch) -> None:
    root = _tree(tmp_path, monkeypatch)
    user = root / "crates/goh/src/user.rs"
    user.write_text(user.read_text() + "macro_rules! m { () => {} }\n")  # expanded where used
    assert _chosen(root, ["crates/goh/src/user.rs"]) == {GO}
    assert _chosen(root, ["Cargo.toml"]) == {GO}


def test_a_module_every_run_passes_through_narrows_nothing(tmp_path, monkeypatch) -> None:
    """main() calls cli.rs before it dispatches: clap's derived parser, generated there, has no
    executed region in any profile (derive output is not instrumented), so a profile that never
    entered cli.rs's own functions still ran its code."""
    root = _tree(tmp_path, monkeypatch)
    assert _chosen(root, ["crates/goh/src/cli.rs"]) == {GO}


@pytest.mark.parametrize(
    ("symbol", "home"),
    [
        ("_RNvNtCs6u25gycwC8Y_3goh10structural3run", "goh::structural"),
        # an inherent impl's method lives where the impl is (backrefs B3_, B5_ inside)
        (
            "_RINvMNtNtCs6u25gycwC8Y_3goh10structural4onlyNtB3_4Only3runNCNvB5_3runs_0EB7_",
            "goh::structural::only",
        ),
        # a closure inside a closure: "00" is two empty identifiers, not one number
        ("_RNCNCNvNtCs6u25gycwC8Y_3goh11step_report9run_child00B7_", "goh::step_report"),
        ("_RNvNtCs1_7goh_sys4kill5group", "goh_sys::kill"),
        ("main", None),
        ("_RNvNtCs6u25gycwC8Y_3goh", None),
    ],
)
def test_a_v0_symbol_is_read_for_the_module_its_code_sits_in(symbol, home) -> None:
    assert v0_home.home(symbol) == home


def test_the_profile_reader_keeps_only_entered_functions() -> None:
    shown = (
        "Counters:\n"
        "  _RNvNtCs1_3goh5alpha3run:\n    Hash: 0x1\n    Function count: 4\n"
        "  _RNvNtCs1_3goh4beta3run:\n    Hash: 0x2\n    Function count: 0\n"
        "  garbage:\n    Hash: 0x3\n    Function count: 1\n"
    )
    assert v0_home.homes(shown) == [v0_home.UNKNOWN, "goh::alpha"]


def _junit(path, red: list[str]) -> None:
    cases = "".join(
        f'<testcase classname="tests.test_go" name="{n}"><failure message="x"/></testcase>'
        for n in red
    )
    path.write_text(f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>')


def test_the_audit_runs_what_the_reach_left_out_and_counts_its_reds(
    tmp_path, monkeypatch, capsys
) -> None:
    root = _tree(tmp_path, monkeypatch)
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl.py"))  # nothing really filed
    monkeypatch.setattr(bin_reach_audit, "tree_of", lambda root: "t" * 40)
    left_out = [f"{GO}::test_only_the_entry"]
    ran: list[list[str]] = []

    def runner(nodes, junit):
        ran.append(nodes)
        _junit(junit, ["test_only_the_entry"])
        return 1

    monkeypatch.setenv("GOH_BINREACH_AUDIT", "never")
    assert bin_reach_audit.audit(root, ["x.rs"], left_out, runner) == (0, "")
    monkeypatch.setenv("GOH_BINREACH_AUDIT", "always")
    assert bin_reach_audit.audit(root, ["x.rs"], [], runner) == (0, "")
    assert not ran
    rc, said = bin_reach_audit.audit(root, ["x.rs"], left_out, runner)
    assert rc == 1 and ran == [left_out], said
    assert "1 disagreements" in said and f"{GO}::test_only_the_entry" in said, said
    rows = bin_reach_audit.audits()
    assert [(r["skipped"], r["disagreements"]) for r in rows] == [(1, 1)], rows
    assert bin_reach_audit.audit(root, ["x.rs"], left_out, lambda n, j: _junit(j, []) or 0)[0] == 0
    line = bin_reach_audit.audit_line()
    assert "2 audits" in line and "1 disagreements" in line, line
    monkeypatch.setenv("GOH_LANDQ_DIR", str(tmp_path / "landq"))
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "timing"))
    assert land_profile.main([]) == 0
    assert "binary reach audit (GOH.BINREACH): 2 audits" in capsys.readouterr().out


def test_one_tree_in_ten_is_audited_and_every_re_land_of_it_alike(monkeypatch) -> None:
    monkeypatch.delenv("GOH_BINREACH_AUDIT", raising=False)
    trees = [f"{n:040x}" for n in range(400)]
    due = [t for t in trees if bin_reach_audit.audit_due(t)]
    assert 20 <= len(due) <= 60, len(due)
    assert all(bin_reach_audit.audit_due(t) for t in due)


def test_free_reads_the_newest_carry_records_through_the_reuse_api(tmp_path, monkeypatch):
    """The carry records are goh-cache.db rows (GOH.VERDICTDB): a reader globbing their old files
    saw none, and every measured-free test was chosen again."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.delenv("GOH_CACHE_DB", raising=False)
    monkeypatch.setattr(bin_reach.reuse, "repo_id", lambda repo: "/r/.git")
    for n, (node, ran) in enumerate([("a", False), ("b", False), ("b", True), ("c", False)]):
        monkeypatch.setenv("GOH_REUSE_NOW", str(2_000_000_000 + n))
        bin_reach.reuse.record(bin_reach.reuse.CARRY, {"repo": "/r/.git", "tree": str(n) * 40,
                               "tools": "t"}, "green", extra={"measured": {node: ran}})  # fmt: skip
    assert bin_reach.free(tmp_path) == {"a", "c"}  # b ran a goh once: never free
    assert bin_reach.free(tmp_path, newest=1) == {"c"}  # the newest record alone
