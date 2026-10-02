"""check_dep_currency.py — a dependency pinned below the graph is a bug.

The offline arm is the one that can fail a build, so it is the one tested
hardest here: it reads two files and decides, so every way it can be wrong is
decidable without a network.

What the tests pin, in the order the failures actually happened:

* the DIRECTION of the finding — our pin behind the graph is a finding, and a
  PARENT behind our pin is not. The first version got this backwards and would
  have manufactured a finding in every repo whose dependency is newer than one
  of its parents.
* workspace INHERITANCE resolves against the root's table. A member manifest
  has no `[workspace]` section, so reading it there yields nothing and quietly
  exempts every inherited dependency.
* a dict-valued requirement reached FIRST used to crash the whole run. Two
  repos had never taken that path, so nothing was red until a third did.
* 0.x caret semantics, where "same major" is the wrong rule and
  `fancy-regex 0.14 -> 0.19` is the case that proves it.
* a bare three-part requirement is a CARET requirement, not an exact one. Two
  of the probe's own expectations were wrong about this before the code was.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "checks" / "check_dep_currency.py"

spec = importlib.util.spec_from_file_location("check_dep_currency", CHECK)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["check_dep_currency"] = mod
spec.loader.exec_module(mod)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def repo(tmp_path: Path, manifest: str, lock: str) -> Path:
    write(tmp_path / "Cargo.toml", manifest)
    write(tmp_path / "Cargo.lock", lock)
    return tmp_path


LOCK_HEADER = "version = 3\n\n"


def test_a_pin_below_the_graph_is_a_finding(tmp_path):
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n',
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n\n'
        '[[package]]\nname = "ureq"\nversion = "3.4.2"\n',
    )
    rep = mod.run(root, offline=True, ratchet=None)
    findings = [f for f in rep.findings if f.severity == "pinned-below-graph"]
    assert len(findings) == 1
    assert findings[0].name == "ureq"
    # The message must name BOTH versions, or the reader has to go and look.
    assert "2.12.1" in findings[0].detail and "3.4.2" in findings[0].detail


def test_a_pin_at_or_above_the_graph_is_clean(tmp_path):
    """The direction case. A PARENT on an older version is not our doing."""
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "3"\n',
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n\n'
        '[[package]]\nname = "ureq"\nversion = "3.4.2"\n',
    )
    rep = mod.run(root, offline=True, ratchet=None)
    assert not [f for f in rep.findings if f.severity == "pinned-below-graph"]


def test_one_version_in_the_graph_is_never_a_finding(tmp_path):
    """Consistency between manifest and lockfile is not currency."""
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n',
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n',
    )
    rep = mod.run(root, offline=True, ratchet=None)
    assert not rep.findings


def test_an_inherited_requirement_is_resolved_against_the_workspace_root(tmp_path):
    write(
        tmp_path / "Cargo.toml",
        "[workspace]\nmembers = ['m']\n\n[workspace.dependencies]\ntoml = \"0.8\"\n",
    )
    write(
        tmp_path / "m" / "Cargo.toml",
        '[package]\nname = "m"\nversion = "0.1.0"\n\n[dependencies]\ntoml = { workspace = true }\n',
    )
    write(
        tmp_path / "Cargo.lock",
        LOCK_HEADER + '[[package]]\nname = "toml"\nversion = "0.8.23"\n\n'
        '[[package]]\nname = "toml"\nversion = "1.1.6"\n',
    )
    rep = mod.run(tmp_path, offline=True, ratchet=None)
    assert any(f.name == "toml" for f in rep.findings if f.severity == "pinned-below-graph")


def test_a_dict_valued_requirement_first_does_not_crash(tmp_path):
    """The path two repos never took, and the third did."""
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n'
        '[dependencies]\ntoml = { version = "1", features = ["preserve_order"] }\n'
        'ureq = { version = "2", default-features = false, features = ["tls"] }\n',
        LOCK_HEADER + '[[package]]\nname = "toml"\nversion = "1.1.6"\n',
    )
    rep = mod.run(root, offline=True, ratchet=None)
    assert rep.examined == 2


def test_path_and_git_dependencies_are_skipped(tmp_path):
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\n'
        'local = { path = "../local" }\nremote = { git = "https://example.test/r" }\n'
        'ureq = "2"\n',
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n',
    )
    rep = mod.run(root, offline=True, ratchet=None)
    assert rep.examined == 1


def test_target_specific_dependencies_are_examined(tmp_path):
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n'
        "[target.'cfg(unix)'.dependencies]\nureq = \"2\"\n",
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n\n'
        '[[package]]\nname = "ureq"\nversion = "3.4.2"\n',
    )
    rep = mod.run(root, offline=True, ratchet=None)
    assert any(f.severity == "pinned-below-graph" for f in rep.findings)


@pytest.mark.parametrize(
    "req,version,expected",
    [
        ("1", "1.9.9", True),
        ("1", "2.0.0", False),
        ("0.19", "0.19.2", True),
        ("0.19", "0.20.0", False),
        ("0", "0.0.5", True),
        ("^0.14", "0.19.2", False),
        ("^1.0.5", "2.0.0", False),
        # A bare three-part req is a CARET req, not an exact one. Two of the
        # probe's own expectations were wrong about this before the code was.
        ("1.0.5", "1.0.6", True),
        ("1.0.5", "1.0.4", False),
        (">=1.2, <2", "1.7.0", True),
        (">=1.2, <2", "2.0.0", False),
        ("~1.2.3", "1.2.9", True),
        ("~1.2.3", "1.3.0", False),
        ("*", "9.9.9", True),
    ],
)
def test_requirement_semantics(req, version, expected):
    assert mod.req_allows(req, version) is expected


def test_an_unreadable_requirement_abstains_rather_than_guessing():
    """A guess here invents findings nobody can act on."""
    assert mod.req_allows("not a version", "1.0.0") is None
    assert mod.req_allows("^1.2.3.4.5", "1.0.0") is None


def test_build_metadata_is_ignored():
    assert mod.req_allows("1.1.6", "1.1.6+spec-1.1.0") is True


def test_a_prerelease_sorts_below_its_release():
    assert mod.parse_version("1.0.0-rc.1") < mod.parse_version("1.0.0")


def test_offline_states_that_it_did_not_check(tmp_path):
    """The honest-degradation rule: absence of evidence is not a clean bill."""
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n',
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n',
    )
    rep = mod.run(root, offline=True, ratchet=None)
    assert not rep.checked_currency
    assert any("offline" in n for n in rep.notes)


def test_a_malformed_manifest_is_skipped_not_fatal(tmp_path):
    write(tmp_path / "Cargo.toml", "this is not toml [[[")
    write(tmp_path / "Cargo.lock", LOCK_HEADER)
    rep = mod.run(tmp_path, offline=True, ratchet=None)
    assert rep.examined == 0


def test_an_empty_tree_declares_itself_not_applicable(tmp_path, capsys):
    """The estate refuses a gate that passes over nothing (`check_empty_scope`).

    Exit 0 WITH the marker is the only shape that satisfies both estate rules
    at once: the sweep reads the marker as an abstention, and `rust_gate.sh`
    stays green on a crate that really does declare no dependencies. Passing
    silently breaks the first; failing breaks the second.
    """
    assert mod.main(["--root", str(tmp_path), "--offline"]) == 0
    assert "not applicable" in capsys.readouterr().out


def test_a_manifest_with_no_dependencies_also_declares_non_applicable(tmp_path, capsys):
    write(tmp_path / "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    assert mod.main(["--root", str(tmp_path), "--offline"]) == 0
    assert "not applicable" in capsys.readouterr().out


def test_a_crate_with_no_dependencies_passes(tmp_path):
    """A real tree with nothing to say is a PASS, not a non-run.

    The estate's own `test_rust_gate.py` builds exactly this crate -- a
    `[package]` with no `[dependencies]` at all -- and requires the gate to
    pass on it, so failing it would be the crying-wolf failure mode.
    """
    write(tmp_path / "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n')
    write(tmp_path / "src" / "lib.rs", "")
    assert mod.main(["--root", str(tmp_path), "--offline"]) == 0


def test_manifests_are_counted_even_with_no_dependencies(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    rep = mod.run(tmp_path, offline=True, ratchet=None)
    assert rep.examined == 0
    assert rep.manifests_seen == 1


def test_the_probe_passes():
    assert mod._probe() == 0
