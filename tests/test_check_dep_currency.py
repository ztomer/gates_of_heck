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
* the SAME 0.x rule in the currency arm. It read "major" as the first field, so
  `signal-hook 0.3 -> 0.4` -- a breaking move, held on purpose in monitor --
  printed as `[drift]`, and a ratchet over majors had nothing to hold
  (2026-10-10). Arm 1 had learned the rule; arm 2 was its unfixed sibling.
* the ratchet is a RATCHET: an entry whose crate is no longer behind a major
  fails until it is deleted, so the list can only shrink; and every entry says
  why it may stand.
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
    r = subprocess.run(
        args, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False
    )
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
        stdin=subprocess.DEVNULL,
        check=False,
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


@pytest.mark.parametrize(
    ("triaged", "want"), [("", 1), ("ureq  # held: a parent still needs 2\n", 0)]
)
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
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == want, r.stdout + r.stderr
    if want:
        assert "ureq" in r.stdout and "ratchet" in r.stdout, r.stdout


def _currency(
    tmp_path: Path,
    deps: dict[str, str],
    latest: dict[str, str],
    ratchet: str | None = None,
    offline: bool = False,
):
    """`goh deps --json` over one crate whose lock holds exactly `deps`, crates.io answering
    `latest` through the cache seam: (exit code, the parsed report)."""
    manifest = '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\n' + "".join(
        f'{n} = "{v}"\n' for n, v in deps.items()
    )
    lock = LOCK_HEADER + "".join(
        f'[[package]]\nname = "{n}"\nversion = "{v}"\n\n' for n, v in deps.items()
    )
    root = repo(tmp_path / "r", manifest, lock)
    args = [str(native_goh_path()), "deps", "--root", str(root), "--json"]
    if ratchet is not None:
        (tmp_path / "ratchet.txt").write_text(ratchet)
        args += ["--ratchet", str(tmp_path / "ratchet.txt")]
    if offline:
        args.append("--offline")
    r = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=60,
        env=_crates_io(tmp_path, **latest),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode in (0, 1), r.stdout + r.stderr
    return r.returncode, json.loads(r.stdout)


@pytest.mark.parametrize(
    ("held", "latest", "bucket"),
    [
        ("0.3.18", "0.4.5", "major_behind"),  # monitor's signal-hook: a breaking move
        ("0.0.3", "0.0.4", "major_behind"),  # ^0.0.3 admits only 0.0.3
        ("0.3.18", "0.3.20", "minor_behind"),
        ("1.2.0", "1.3.0", "minor_behind"),
        ("1.2.0", "2.0.0", "major_behind"),
        ("0.9.1", "1.0.0", "major_behind"),
    ],
)
def test_behind_a_major_means_behind_cargos_compatibility_boundary(tmp_path, held, latest, bucket):
    """Cargo's breaking boundary is the leftmost NON-ZERO field: 0.3 -> 0.4 is a major move, as
    `^0.3` refusing 0.4 says. The currency arm compared the first field only."""
    _, doc = _currency(tmp_path, {"c": held}, {"c": latest})
    names = {k: [f["name"] for f in doc[k]] for k in ("major_behind", "minor_behind")}
    assert names[bucket] == ["c"], doc
    other = "minor_behind" if bucket == "major_behind" else "major_behind"
    assert names[other] == [], doc


def _fatal(doc: dict) -> list[tuple[str, str]]:
    return [(f["severity"], f["name"]) for f in doc["fatal"]]


def test_a_triaged_major_with_its_reason_passes(tmp_path):
    code, doc = _currency(
        tmp_path,
        {"sh": "0.3.18"},
        {"sh": "0.4.5"},
        ratchet="# held majors\nsh  # crossterm 0.29 still depends on 0.3\n",
    )
    assert (code, _fatal(doc)) == (0, []), doc


def test_a_ratchet_entry_with_no_reason_fails(tmp_path):
    """The list says what is held; the reason says why it may stand. An entry without one is the
    allowlist nobody can re-judge."""
    code, doc = _currency(tmp_path, {"sh": "0.3.18"}, {"sh": "0.4.5"}, ratchet="sh\n")
    assert code == 1, doc
    assert ("ratchet-unreasoned", "sh") in _fatal(doc), doc


@pytest.mark.parametrize(
    ("deps", "latest"),
    [
        ({"sh": "0.4.5"}, {"sh": "0.4.5"}),  # the held crate moved: the entry is done
        ({"sh": "0.3.18"}, {"sh": "0.3.20"}),  # only drift left: nothing to hold
        ({"other": "1.0.0"}, {"other": "1.0.0"}),  # not a dependency at all
    ],
)
def test_a_stale_ratchet_entry_fails_until_it_is_deleted(tmp_path, deps, latest):
    """A ratchet that keeps an entry after its crate catches up is a standing licence for that
    crate to fall behind again unseen: the set could grow back, which is the one thing it is for."""
    code, doc = _currency(
        tmp_path, deps, latest, ratchet="sh  # crossterm 0.29 still depends on 0.3\n"
    )
    assert code == 1, doc
    assert ("ratchet-stale", "sh") in _fatal(doc), doc


def test_offline_does_not_judge_an_entry_stale_and_says_so(tmp_path):
    """Staleness needs crates.io's answer; without it the entry is not judged, and the report says
    so rather than calling it clean."""
    code, doc = _currency(
        tmp_path,
        {"sh": "0.3.18"},
        {"sh": "0.4.5"},
        ratchet="sh  # crossterm 0.29 still depends on 0.3\n",
        offline=True,
    )
    assert (code, _fatal(doc)) == (0, []), doc
    assert any("ratchet" in n for n in doc["notes"]), doc
