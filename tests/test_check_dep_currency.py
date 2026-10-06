"""`goh deps` — a dependency pinned below the graph is a bug.

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

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import native_goh_path


def run(root: Path, offline: bool = True, ratchet: str | None = None) -> SimpleNamespace:
    """One `goh deps --json` pass, as the report these tests read: `findings` (severity, name,
    detail, where), `examined`, `notes`, `checked_currency`, and the not-applicable sentence."""
    args = [str(native_goh_path()), "deps", "--root", str(root), "--json"]
    if offline:
        args.append("--offline")
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    doc = json.loads(r.stdout)
    rows = doc.get("fatal", []) + doc.get("major_behind", []) + doc.get("minor_behind", [])
    return SimpleNamespace(
        findings=[SimpleNamespace(**f) for f in rows],
        examined=doc["examined"],
        notes=doc.get("notes", []),
        checked_currency=doc.get("currency_checked", False),
        not_applicable=doc.get("not_applicable", ""),
    )


def main(root: Path) -> subprocess.CompletedProcess:
    """`goh deps --root R --offline`, text."""
    return subprocess.run(
        [str(native_goh_path()), "deps", "--root", str(root), "--offline"],
        capture_output=True,
        text=True,
        timeout=60,
    )


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
    rep = run(root)
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
    rep = run(root)
    assert not [f for f in rep.findings if f.severity == "pinned-below-graph"]


def test_one_version_in_the_graph_is_never_a_finding(tmp_path):
    """Consistency between manifest and lockfile is not currency."""
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n',
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n',
    )
    rep = run(root)
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
    rep = run(tmp_path)
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
    rep = run(root)
    assert rep.examined == 2


def test_path_and_git_dependencies_are_skipped(tmp_path):
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\n'
        'local = { path = "../local" }\nremote = { git = "https://example.test/r" }\n'
        'ureq = "2"\n',
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n',
    )
    rep = run(root)
    assert rep.examined == 1


def test_target_specific_dependencies_are_examined(tmp_path):
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n'
        "[target.'cfg(unix)'.dependencies]\nureq = \"2\"\n",
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n\n'
        '[[package]]\nname = "ureq"\nversion = "3.4.2"\n',
    )
    rep = run(root)
    assert any(f.severity == "pinned-below-graph" for f in rep.findings)


# The requirement comparator's table (Cargo's own, plus the rows read wrong before 2026-10-05),
# unreadable requirements, build metadata and pre-release order are Rust tests now, beside the
# comparator: `deps::semver::tests`.


def test_offline_states_that_it_did_not_check(tmp_path):
    """The honest-degradation rule: absence of evidence is not a clean bill."""
    root = repo(
        tmp_path,
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n',
        LOCK_HEADER + '[[package]]\nname = "ureq"\nversion = "2.12.1"\n',
    )
    rep = run(root)
    assert not rep.checked_currency
    assert any("offline" in n for n in rep.notes)


def test_a_malformed_manifest_is_skipped_not_fatal(tmp_path):
    write(tmp_path / "Cargo.toml", "this is not toml [[[")
    write(tmp_path / "Cargo.lock", LOCK_HEADER)
    rep = run(tmp_path)
    assert rep.examined == 0


def test_an_empty_tree_declares_itself_not_applicable(tmp_path):
    """The estate refuses a gate that passes over nothing (`check_empty_scope`).

    Exit 0 WITH the marker is the only shape that satisfies both estate rules
    at once: the sweep reads the marker as an abstention, and `rust_gate.sh`
    stays green on a crate that really does declare no dependencies. Passing
    silently breaks the first; failing breaks the second.
    """
    r = main(tmp_path)
    assert r.returncode == 0 and "not applicable" in r.stdout, r.stdout + r.stderr


def test_a_manifest_with_no_dependencies_also_declares_non_applicable(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    r = main(tmp_path)
    assert r.returncode == 0 and "not applicable" in r.stdout, r.stdout + r.stderr


def test_a_crate_with_no_dependencies_passes(tmp_path):
    """A real tree with nothing to say is a PASS, not a non-run.

    The estate's own `test_rust_gate.py` builds exactly this crate -- a
    `[package]` with no `[dependencies]` at all -- and requires the gate to
    pass on it, so failing it would be the crying-wolf failure mode.
    """
    write(tmp_path / "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n')
    write(tmp_path / "src" / "lib.rs", "")
    r = main(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_manifests_are_counted_even_with_no_dependencies(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    rep = run(tmp_path)
    assert rep.examined == 0
    assert "1 manifest(s), none declaring a dependency" in rep.not_applicable, rep


def _crates_io(tmp_path: Path, **latest: str) -> dict:
    """crates.io answers through the cache seam, so the currency arm runs with no network."""
    import json as _json
    import os
    import time

    cache = tmp_path / "crates-io"
    cache.mkdir(exist_ok=True)
    for name, version in latest.items():
        (cache / f"{name}.json").write_text(
            _json.dumps({"fetched": time.time(), "version": version})
        )
    return dict(os.environ, GOH_CRATES_IO_CACHE=str(cache))


@pytest.mark.parametrize(("triaged", "want"), [("", 1), ("ureq\n", 0)])
def test_the_ratchet_fails_an_untriaged_major_and_passes_a_triaged_one(tmp_path, triaged, want):
    """`--ratchet FILE` is "fail on any major NOT listed in FILE". It read `f["name"]` off a
    dataclass -- a TypeError the moment a major was behind -- and the findings it meant to add
    were not fatal anyway (found porting it, Phase N1)."""
    root = repo(
        tmp_path / "r",
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n',
        'version = 3\n\n[[package]]\nname = "ureq"\nversion = "2.12.1"\n',
    )
    ratchet = tmp_path / "ratchet.txt"
    ratchet.write_text(triaged)
    r = subprocess.run(
        [str(native_goh_path()), "deps", "--root", str(root), "--ratchet", str(ratchet)],
        capture_output=True,
        text=True,
        env=_crates_io(tmp_path, ureq="3.4.2"),
    )
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == want, r.stdout + r.stderr
    if want:
        assert "ureq" in r.stdout and "ratchet" in r.stdout, r.stdout
