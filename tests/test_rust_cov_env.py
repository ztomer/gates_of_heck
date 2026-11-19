"""The Rust coverage measurement keys the GOH_* it READS, never the whole configuration
(GOH.ONEVERDICTTREE, lib/cov_env.py).

Every release push re-measured what its land had just measured on the same inputs (9 of the last 9
said "under another GOH_* configuration", 380-980 s each): the land's environment carries its own
bookkeeping (GOH_PROFILE_PROVEN, a lane's inherited variables) and the release push's does not.
Both directions, on the real gates/_rust_proven.sh: a variable nothing keyed spells reuses; one a
coverage script, the crate or the suite's declaration names moves the key, and the miss names it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import cov_env
import gate_identity
import pytest
import rust_cov_scripts
from conftest import REPO_ROOT
from test_rust_cov_reuse import _decision, _run, _run_out, _write, repo  # noqa: F401  (the fixture)

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")

DECL = 'CALLER_INPUTS = ("GOH_PY", "GOH_SUITE_KNOB")\n'


@pytest.fixture
def declared(repo: Path) -> Path:  # noqa: F811  (the imported fixture, extended)
    """The goh shape: a suite that drops every GOH_* it does not declare (tests/suite_home.py)."""
    _write(repo, "tests/suite_home.py", DECL)
    return repo


def test_the_land_gates_bookkeeping_does_not_void_the_release_pushs_reuse(declared: Path) -> None:
    """tools/gate_profile.sh runs the land's gate under GOH_PROVEN=1, the push its default: the
    proven cache's own settings decide whether a key exists (0: none), never what is measured.
    The first land under the new key recorded `goh-env GOH_PROVEN=1` (709d43aa)."""
    land = {"GOH_PROFILE_PROVEN": "1", "GOH_LAND_PRE_FROM": "/q/pre/1.keep", "GOH_MAX_LINES": "400",
            "GOH_PROVEN": "1", "GOH_PROVEN_TTL_S": "86400"}  # fmt: skip
    assert _run(declared, **land) == "MEASURED"
    _, out = _run_out(declared)
    assert "PLAIN" in out, _decision(out)  # the release push: none of them
    assert _run(declared, GOH_LAND_BRANCH="lane/x") == "PLAIN"


@pytest.mark.parametrize(
    ("name", "how"),
    [
        ("GOH_TAIL", "a coverage script reads it"),
        ("GOH_SUITE_KNOB", "the suite declares it"),
        ("GOH_CRATE_KNOB", "the crate's source names it"),
        ("GOH_CRATE_FAMILY_X", "the crate's source reads its prefix"),
    ],
)
def test_a_variable_the_measurement_reads_moves_the_key(
    declared: Path, name: str, how: str
) -> None:
    _write(declared, "crates/a/src/lib.rs", 'pub fn f() -> u64 {\n    1\n}\n// "GOH_CRATE_KNOB"'
           ' std::env::vars().filter(|(k, _)| k.starts_with("GOH_CRATE_FAMILY_"))\n')  # fmt: skip
    assert _run(declared, **{name: "1"}) == "MEASURED"
    assert _run(declared, **{name: "1"}) == "PLAIN"
    _, out = _run_out(declared, **{name: "2"})
    assert f"was on other inputs: {name} moved" in _decision(out), how


def test_a_new_read_enters_the_key_from_the_scripts_bytes(
    declared: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No list to keep: the line that reads a variable is what keys it."""
    from source_closure import copy_closure

    root = tmp_path / "goh"
    copy_closure(root, *gate_identity.rust_gate_scripts(REPO_ROOT), "gates/verdict_free_keys.txt",
                 root=REPO_ROOT)  # fmt: skip
    monkeypatch.setenv("GOH_BRAND_NEW_KNOB", "1")  # in-process: the copied root's scripts
    assert not any("GOH_BRAND_NEW_KNOB" in ln for ln in gate_identity.rust(declared, root, "true"))
    p = root / "gates/_coverage_rust.sh"
    p.write_text(p.read_text() + '\n: "${GOH_BRAND_NEW_KNOB:-}"\n')
    assert "goh-env GOH_BRAND_NEW_KNOB=1" in gate_identity.rust(declared, root, "true")


def test_without_a_suite_declaration_every_goh_variable_is_keyed(repo: Path) -> None:  # noqa: F811
    """A suite that drops nothing can read anything the caller set: the old, whole key."""
    assert _run(repo, GOH_PROFILE_PROVEN="1") == "MEASURED"
    _, out = _run_out(repo)
    assert "was on other inputs: GOH_PROFILE_PROVEN moved" in _decision(out)


def test_what_is_keyed_elsewhere_or_never_a_verdict_is_left_out() -> None:
    env = {"GOH_DIR": "/a", "GOH_COV_FLOOR_RUST": "90", "GOH_TIMINGS": "/t", "GOH_X": "1",
           "GOH_FAM_A": "1", "PATH": "/bin", "GOH_PROVEN": "1", "GOH_PROVEN_NOW": "9"}  # fmt: skip
    free = cov_env.free_keys(REPO_ROOT)
    names = {"GOH_X", "GOH_DIR", "GOH_TIMINGS", "GOH_FAM_", "GOH_PROVEN"}
    assert cov_env.keyed(names, env, free) == ["GOH_FAM_A", "GOH_X"]
    assert cov_env.keyed(None, env, free) == ["GOH_FAM_A", "GOH_X"]
    assert cov_env.spelled([b'"GOH_" GOH_A $GOH_B_']) == {"GOH_A", "GOH_B_"}


def test_goh_declares_its_suite_inputs() -> None:
    """goh's own suite is the declared shape, so its measurement keys only what it reads."""
    import suite_home

    assert cov_env.declared(REPO_ROOT) == suite_home.CALLER_INPUTS


def test_no_wholesale_goh_read_in_gohs_measurement_but_the_pinned_ones() -> None:
    """A bare `GOH_` is no name, so the scan keys nothing for it: each file of goh's measurement
    that spells one is pinned with what it does there, and a new one (or a stale entry) is red."""
    ask = '. ./.gatesrc >/dev/null 2>&1; printf %s "$GOH_COV_RUST_EXTERNAL"'
    ext = subprocess.run(["bash", "-c", ask], cwd=REPO_ROOT, capture_output=True, text=True,
                         check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    inputs = gate_identity.build_inputs(REPO_ROOT)
    scripts = rust_cov_scripts.env_readers(REPO_ROOT, gate_identity.rust_gate_scripts(REPO_ROOT))
    parts = [(REPO_ROOT, inputs), (REPO_ROOT, scripts),
             (REPO_ROOT, gate_identity.runner_files(REPO_ROOT, ext))]  # fmt: skip
    files = {rel: data for root, ps in parts for rel, _, data in gate_identity._entries(root, ps)}
    bare = {rel for rel, data in files.items() if b"GOH_" in cov_env._NAME.findall(data)}
    assert bare == set(cov_env.WHOLESALE)
    built = {m for data in files.values() for m in cov_env.BUILT.findall(data)}
    assert built == {b"GOH_COV_FLOOR_$"}  # keyed whole by the `cov` lines
