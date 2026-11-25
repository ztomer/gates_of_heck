"""A crates/ change chooses only the binary tests whose subcommands reach it (GOH.AFFECTEDBINARY2).

GOH.AFFECTEDBINARY chose every test that runs the goh binary for any crates/ change: 283 whole
files, 12m02s at load 120, past the pre-step's 10-minute deadline, so no Rust land had a pre-step
verdict. lib/crate_reach.py reads the crate's module graph and main.rs's dispatch table; a test's
subcommands are the argv words it and the binary-running files it reaches name. Pinned here: a
scanner's change chooses its own tests and not another subcommand's, a shared module (main.rs,
cli.rs, Cargo.toml) still chooses every runner, a constant read is no call, an `impl` of another
file's type is reached through it, a test-only source chooses none, and the dispatch table has an
arm for every variant cli.rs declares (a variant it missed would be reached by nothing).
"""

from __future__ import annotations

import functools
import sys

import _tree_graph
import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

from lib import crate_reach

SCANNER = "crates/goh/src/layerparity.rs"
OTHER = "tests/test_tag_version_native_parity.py"  # `goh tag-version` only: no layer-parity

MAIN = """mod cli;
mod alpha;
mod beta;
mod shared;
mod consts;
mod kind;
fn main() {
    let cli = cli::parse();
    std::process::exit(run_core(cli));
}
fn run_core(command: Commands) -> i32 {
    match command {
        Commands::Alpha => alpha::run(),
        Commands::Beta(a) => helper(a),
        other => 2,
    }
}
fn helper(a: u8) -> i32 { beta::run(a) }
"""
CLI = """pub enum Commands {
    /// alpha's doc
    Alpha,
    #[command(hide = true)]
    Beta(u8),
}
pub fn parse() -> Commands { crate::consts::LIMIT; Commands::Alpha }
"""
CRATE = {
    "crates/goh/src/main.rs": MAIN,
    "crates/goh/src/cli.rs": CLI,
    "crates/goh/src/alpha.rs": "use crate::shared;\npub fn run() -> i32 { shared::go() }\n"
    '#[cfg(test)]\n#[path = "alpha_tests.rs"]\nmod tests;\n',
    "crates/goh/src/alpha_tests.rs": "use super::*;\n",
    "crates/goh/src/beta.rs": "use crate::kind::Kind;\npub fn run(a: u8) -> i32 { Kind.go(a) }\n",
    "crates/goh/src/shared.rs": "pub fn go() -> i32 { 0 } // beta::run is prose\n",
    "crates/goh/src/consts.rs": "pub const LIMIT: u8 = 3;\n"
    "pub fn heavy() { crate::beta::run(1); }\n",
    "crates/goh/src/kind.rs": "pub struct Kind;\n",
    "crates/goh/src/kind_impl.rs": "",
    "Cargo.toml": "",
}
TESTS = {
    "tests/test_alpha.py": "def test_a(goh):\n    run([goh, 'alpha'])\n",
    "tests/test_beta.py": "def test_b(goh):\n    run([goh, 'beta', '--x'])\n",
    "tests/test_dynamic.py": "import os\nB = os.environ['GOH_TEST_BIN']\nrun([B, *ARGS])\n",
    "tests/test_pure.py": "def test_c(tmp_path):\n    assert 'alpha'\n",
}
RUNNERS = {"tests/test_alpha.py", "tests/test_beta.py", "tests/test_dynamic.py"}


def _tree(tmp_path, extra: dict[str, str] | None = None):
    for rel, text in {**CRATE, **TESTS, **(extra or {})}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


@functools.cache
def _real() -> crate_reach.Crate:
    return crate_reach.Crate(REPO_ROOT)


def _runners(chosen: dict[str, str]) -> set[str]:
    return {t for t, why in chosen.items() if "goh binary" in why}


def _binary(root, path: str) -> set[str]:
    return _runners(at.affected(root, [path]))


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("crates/goh/src/shared.rs", {"tests/test_alpha.py", "tests/test_dynamic.py"}),
        ("crates/goh/src/beta.rs", {"tests/test_beta.py", "tests/test_dynamic.py"}),
        ("crates/goh/src/kind.rs", {"tests/test_beta.py", "tests/test_dynamic.py"}),
        ("crates/goh/src/alpha_tests.rs", set()),  # cfg(test): not in the binary
        ("crates/goh/src/main.rs", RUNNERS),
        ("crates/goh/src/cli.rs", RUNNERS),
        ("crates/goh/src/consts.rs", RUNNERS),  # cli reads its constant: every run does
        ("Cargo.toml", RUNNERS),
    ],
)
def test_a_crate_change_chooses_the_runners_of_the_subcommands_it_reaches(
    tmp_path, path, want
) -> None:
    assert _binary(_tree(tmp_path), path) == want


def test_a_gate_script_that_names_no_binary_key_still_names_its_subcommand(tmp_path) -> None:
    """tests/test_gates_e2e.py runs gates/structural.sh, which runs the binary through a resolver
    the base sources; its `structural` word counts though the script names no binary key."""
    root = _tree(
        tmp_path,
        {
            "tests/test_script.py": "S = ROOT / 'gates' / 's.sh'\n",
            "gates/s.sh": 'source "$GOH_DIR/gates/_c.sh"\nexec "$bin" beta\n',
            "gates/_c.sh": '"$GOH_RESOLVED_BIN" alpha\n',
        },
    )
    assert "tests/test_script.py" in _binary(root, "crates/goh/src/beta.rs")


def test_a_constant_read_is_no_call_into_its_module(tmp_path) -> None:
    """cli.rs reads consts::LIMIT (clap's defaults do this); consts.rs's own call into beta is not
    run by it, so beta is no module every run reaches."""
    crate = crate_reach.Crate(_tree(tmp_path))
    assert "crates/goh/src/consts.rs" in crate.shared
    assert "crates/goh/src/beta.rs" not in crate.shared
    assert crate.subcommands("crates/goh/src/beta.rs") == {"beta"}


def test_an_impl_of_another_files_type_is_reached_through_the_type(tmp_path) -> None:
    impl = "use crate::kind::Kind;\nimpl Kind { pub fn go(&self, a: u8) -> i32 { 0 } }\n"
    main = MAIN.replace("mod kind;", "mod kind;\nmod kind_impl;")
    root = _tree(tmp_path, {"crates/goh/src/kind_impl.rs": impl, "crates/goh/src/main.rs": main})
    assert _binary(root, "crates/goh/src/kind_impl.rs") == {
        "tests/test_beta.py",
        "tests/test_dynamic.py",
    }


def test_a_blanket_impl_of_another_files_trait_is_reached_through_the_trait(tmp_path) -> None:
    blanket = "use crate::kind::Ext;\nimpl<T: std::io::Read> Ext for T { fn x(&self) {} }\n"
    main = MAIN.replace("mod kind;", "mod kind;\nmod kind_impl;")
    kind = "pub struct Kind;\npub trait Ext { fn x(&self); }\n"
    root = _tree(
        tmp_path,
        {
            "crates/goh/src/kind_impl.rs": blanket,
            "crates/goh/src/main.rs": main,
            "crates/goh/src/kind.rs": kind,
        },
    )
    assert crate_reach.Crate(root).subcommands("crates/goh/src/kind_impl.rs") == {"beta"}


def test_an_impl_naming_nothing_it_can_resolve_is_reached_by_every_run(tmp_path) -> None:
    unresolved = "impl<T: std::io::Read> Ext for T { fn x(&self) {} }\n"
    main = MAIN.replace("mod kind;", "mod kind;\nmod kind_impl;")
    root = _tree(
        tmp_path, {"crates/goh/src/kind_impl.rs": unresolved, "crates/goh/src/main.rs": main}
    )
    assert crate_reach.Crate(root).subcommands("crates/goh/src/kind_impl.rs") is None
    assert _binary(root, "crates/goh/src/kind_impl.rs") == RUNNERS


def test_the_dispatch_table_has_an_arm_for_every_variant_on_the_real_tree() -> None:
    crate = _real()
    arms, _ = crate.dispatch
    variants = {crate_reach.kebab(v) for v in crate.variants}
    assert len(variants) > 60, sorted(variants)
    assert variants == set(arms), (sorted(variants - set(arms)), sorted(set(arms) - variants))
    assert not [r for r in crate.code if r not in crate.module], "a source no `mod` declares"


def test_the_scanner_change_chooses_its_runners_and_not_every_binary_test() -> None:
    """The lane's measure: GOH.AFFECTEDBINARY ran 283 whole files for a layerparity.rs change."""
    chosen = _runners(_tree_graph.affected([SCANNER]))
    assert "tests/test_tui_marks.py" in chosen  # tuimarks calls layerparity::push_text
    assert "tests/test_goh_structural.py" in chosen  # layer-parity is a structural step
    assert OTHER not in chosen
    # A share of every runner, not a count: a new test of a gate that runs the binary is one more
    # runner of both (199 on main 2026-11-17, then 200 of 295 with tests/test_proven_tool_line.py
    # red at the old `< 200`: GOH.PREPARITY's land). GOH.AFFECTEDBINARY chose every one, 283.
    every = _tree_graph.affected(["crates/goh/src/main.rs"])
    runners = _runners(every)
    assert len(chosen) < 0.75 * len(runners), (len(chosen), len(runners))
    assert chosen < set(every) and OTHER in every, sorted(chosen - set(every))


@pytest.mark.parametrize(
    "path", ["crates/goh/src/cli.rs", "crates/goh/Cargo.toml", "Cargo.lock", "crates/goh/build.rs"]
)
def test_a_shared_input_still_chooses_every_binary_test_on_the_real_tree(path) -> None:
    assert _real().subcommands(path) is None


def test_a_file_the_binary_includes_reaches_its_includer() -> None:
    crate = _real()
    assert crate.subcommands("lib/busyloop_shapes.json") >= {"busy-loop"}
    assert crate.subcommands("lib/no_such_file.json") == set()
