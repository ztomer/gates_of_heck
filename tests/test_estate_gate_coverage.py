"""The estate audit's verdict is the one the repo's own push gate would reach (queue GOH.EA2).

`scripts/estate_audit.py` said "the next push of <lock> is red" for two locks whose push was not:
a private consumer's nested Cargo workspace gates with cargo-deny, whose `deny.toml` ignores what cargo-audit reported, and
`monitor/fuzz/Cargo.lock` is a workspace no gate step of monitor audits at all. The coverage is read
from the gate (`lib/gate_coverage.py`): a lock no step audits is NOT AUDITED AT PUSH, a lock a
cargo-deny step audits is judged by cargo-deny, and a step goh cannot read makes the lock
undetermined rather than guessed. The advisory database is the fixture of test_estate_audit.py.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _gitutil import foreign_repo_env
from test_estate_audit import GATED, LOCK, PLANTED, SCRIPT, _db, _git, _hook, _repo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import gate_coverage

needs_audit = pytest.mark.skipif(
    shutil.which("cargo-audit") is None, reason="cargo-audit not installed"
)


def _add(repo: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "more")


def _lock(crate: str = "goh-fixture-crate") -> str:
    return LOCK.format(name=crate, zeros="0" * 64)


def _covered(repo: Path) -> gate_coverage.Coverage:
    return gate_coverage.coverage(repo, foreign_repo_env())


def _run(root: Path, state: Path, db: Path, *extra: str) -> dict:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--state", str(state)]
        + ["--db", str(db), "--no-fetch", *extra],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(state.read_text())


# ── the verdicts, end to end ─────────────────────────────────────────────────────────────────


@needs_audit
def test_a_lock_no_gate_step_audits_is_named_not_audited_never_red(tmp_path):
    """A second workspace whose repo audits only its root lock by hand. The push is not red for it
    -- the push never looks -- and the gap is the finding, with what fires there unseen."""
    root = tmp_path / "Projects"
    repo = _repo(root, "mon", "goh-other-crate", gated=False)
    _add(repo, {".gatesrc": "GOH_CI_STEPS='cargo audit'\n", "fuzz/Cargo.lock": _lock()})
    state = tmp_path / "state.json"
    found = _run(root, state, _db(tmp_path, planted=True))
    assert found["advisories"] == {}, found
    assert found["unaudited"] == {"mon/fuzz/Cargo.lock": [PLANTED]}, found
    assert found["lockfiles"] == 1, found
    out = _hook(state)
    assert "NOT AUDITED AT PUSH" in out and "mon/fuzz/Cargo.lock" in out, out
    assert "is red" not in out and "standing" not in out, out


@needs_audit
def test_the_house_gate_audits_a_second_workspace_so_its_push_is_red(tmp_path):
    """monitor/fuzz since GOH.EA2: rust_gate.sh audits every tracked lock, so the same lock is no
    gap -- its next push is red, and the audit says so."""
    root = tmp_path / "Projects"
    repo = _repo(root, "mon", "goh-other-crate")
    _add(repo, {"fuzz/Cargo.lock": _lock()})
    found = _run(root, tmp_path / "state.json", _db(tmp_path, planted=True))
    assert found["unaudited"] == {} and found["undetermined"] == {}, found
    assert "mon/fuzz/Cargo.lock" in json.dumps(found["advisories"]), found


def _fake_deny(tmp: Path, verdict: str) -> Path:
    """A cargo-deny that records how it was called and reports the planted advisory as `verdict`
    (`error` fails the step; `note` is what cargo-deny prints for an advisory deny.toml ignores)."""
    log = tmp / "deny-argv.json"
    fake = tmp / "fake-cargo-deny"
    fake.write_text(
        f"""#!{sys.executable}
import json, os, sys
json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd()}}, open({str(log)!r}, "w"))
diag = {{"type": "diagnostic", "fields": {{"severity": {verdict!r}, "code": "vulnerability",
    "message": "planted", "advisory": {{"id": {PLANTED!r}, "title": "planted"}},
    "graphs": [{{"Krate": {{"name": "goh-fixture-crate", "version": "1.0.0"}}}}]}}}}
print(json.dumps(diag), file=sys.stderr)
print(json.dumps({{"type": "summary", "fields": {{}}}}), file=sys.stderr)
sys.exit(1 if {verdict!r} == "error" else 0)
"""
    )
    fake.chmod(0o755)
    return fake


def _deny_repo(root: Path) -> Path:
    """A private consumer's shape: no rust_gate.sh, the push runs `make ci`, whose recipe runs cargo-deny."""
    repo = _repo(root, "consumer", "goh-fixture-crate", gated=False)
    _add(
        repo,
        {
            ".gatesrc": "# gated, with its own gate\n",
            "tools/gate.sh": "#!/bin/sh\nmake ci\n",
            "Makefile": "ci:\n\t$(CLOCK) run deny -- cargo deny check advisories bans sources\n",
        },
    )
    return repo


@needs_audit
@pytest.mark.parametrize(("verdict", "red"), [("note", False), ("error", True)])
def test_a_cargo_deny_gate_is_judged_by_cargo_deny(tmp_path, verdict, red):
    """cargo-audit fires on the lock either way; the push is red only when cargo-deny says so."""
    root = tmp_path / "Projects"
    repo = _deny_repo(root)
    fake = _fake_deny(tmp_path, verdict)
    found = _run(
        root, tmp_path / "state.json", _db(tmp_path, planted=True), "--cargo-deny", str(fake)
    )
    assert found["unaudited"] == {} and found["lockfiles"] == 1, found
    assert (PLANTED in found["advisories"]) is red, found
    called = json.loads((tmp_path / "deny-argv.json").read_text())
    assert Path(called["cwd"]).resolve() == repo.resolve(), called
    assert called["argv"][-2:] == ["check", "advisories"] and "--offline" in called["argv"], called


@needs_audit
def test_a_step_goh_cannot_read_makes_the_lock_undetermined(tmp_path):
    """koffee_big's shape: `( cd "$rust_core" && cargo audit )`. Neither red nor unaudited."""
    root = tmp_path / "Projects"
    repo = _repo(root, "kb", "goh-other-crate", gated=False)
    _add(
        repo,
        {
            ".gatesrc": "# gated\n",
            "tools/gate.sh": '#!/bin/sh\n"$(dirname "$0")/run.sh"\n',
            "tools/run.sh": 'core="$root/core"\n( cd "$core" \\\n  && cargo audit )\n',
            "core/Cargo.lock": _lock(),
        },
    )
    found = _run(root, tmp_path / "state.json", _db(tmp_path, planted=True))
    assert found["advisories"] == {} and found["unaudited"] == {}, found
    assert sorted(found["undetermined"]) == ["kb/Cargo.lock", "kb/core/Cargo.lock"], found
    assert found["undetermined"]["kb/core/Cargo.lock"]["firing"] == [PLANTED], found


# ── the reading of a gate ────────────────────────────────────────────────────────────────────


def _gate_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = _repo(tmp_path, "r", "goh-other-crate", gated=False)
    _add(repo, files)
    return repo


def test_rust_gate_sh_audits_every_tracked_lock_from_its_config(tmp_path):
    """rust_gate.sh audits every tracked lock of its repo (lib/rust_locks.py), each from the nearest
    `.cargo/audit.toml` at or above it: the reader asks the same rule, and copies none of it."""
    repo = _gate_repo(
        tmp_path,
        {
            ".gatesrc": "GOH_CI_STEPS='\"$GOH_DIR/gates/rust_gate.sh\" . rust/core'\n",
            "rust/Cargo.lock": _lock(),
            "rust/.cargo/audit.toml": "[advisories]\nignore = []\n",
            "fuzz/Cargo.lock": _lock(),
        },
    )
    cov = _covered(repo)
    assert cov.covered() == {"Cargo.lock", "rust/Cargo.lock", "fuzz/Cargo.lock"}, cov
    by = {a.lock: a.cwd for a in cov.audits}
    assert by == {"Cargo.lock": ".", "rust/Cargo.lock": "rust", "fuzz/Cargo.lock": "fuzz"}, cov
    assert cov.unresolved == [], cov


def test_a_cargo_dir_named_through_a_variable_still_audits_the_repo(tmp_path):
    """media_server's --staged shape, `rust_gate.sh . "$crate"`: the variable names which crate is
    LINTED, not which locks are audited -- every tracked one is, whatever the crate."""
    repo = _gate_repo(
        tmp_path,
        {"tools/gate.sh": '"$GOH/gates/rust_gate.sh" . "$crate"\n', "sub/Cargo.lock": _lock()},
    )
    cov = _covered(repo)
    assert cov.covered() == {"Cargo.lock", "sub/Cargo.lock"} and cov.unresolved == [], cov


def test_a_step_outside_the_repo_group_and_a_listing_audit_nothing(tmp_path):
    repo = _gate_repo(
        tmp_path,
        {
            "tools/gate.sh": "# rust_gate.sh . would audit; a comment does not\n"
            "GOH_RUST_GROUPS=crate,coverage rust_gate.sh .\n"
            "rust_gate.sh --each-crate --list .\n"
            "cargo deny check bans licenses\n"
            'echo "cargo audit is a string here"\n',
        },
    )
    assert _covered(repo).covered() == set()


def test_the_repo_group_audits(tmp_path):
    repo = _gate_repo(tmp_path, {"tools/gate.sh": "GOH_RUST_GROUPS=repo rust_gate.sh .\n"})
    assert _covered(repo).covered() == {"Cargo.lock"}


def test_cargo_audit_and_cargo_deny_name_their_lock(tmp_path):
    repo = _gate_repo(
        tmp_path,
        {
            "tools/gate.sh": "bash tools/steps.sh\ncd sub && cargo audit --deny unsound\n",
            "tools/steps.sh": "cargo audit --file a/Cargo.lock\n"
            "cargo deny --manifest-path b/Cargo.toml check\n",
            "a/Cargo.lock": _lock(),
            "b/Cargo.lock": _lock(),
            "sub/Cargo.lock": _lock(),
        },
    )
    cov = _covered(repo)
    assert cov.covered() == {"a/Cargo.lock", "b/Cargo.lock", "sub/Cargo.lock"}, cov
    by = {a.lock: a for a in cov.audits}
    assert by["b/Cargo.lock"].tool == "cargo-deny" and by["sub/Cargo.lock"].deny == ("unsound",)
    assert by["sub/Cargo.lock"].cwd == "sub", cov


def test_each_crate_audits_every_lock_testkits_included(tmp_path):
    manifest = '[package]\nname = "{n}"\nversion = "0.1.0"\nedition = "2021"\n'
    repo = _gate_repo(
        tmp_path,
        {
            "tools/gate.sh": '"$GOH/gates/rust_gate.sh" --each-crate .\n',
            ".cargo/audit.toml": "[advisories]\nignore = []\n",
            "crates/a/Cargo.toml": manifest.format(n="a"),
            "crates/a/Cargo.lock": _lock(),
            "crates/a/src/lib.rs": "",
            # a testkit is its crate's business to --each-crate, but its lock is the repo's to
            # audit: exactly media_server's testkits, which only a hand-written step audited.
            "crates/a/testkit/Cargo.toml": manifest.format(n="t"),
            "crates/a/testkit/Cargo.lock": _lock(),
        },
    )
    cov = _covered(repo)
    assert cov.covered() == {"Cargo.lock", "crates/a/Cargo.lock", "crates/a/testkit/Cargo.lock"}
    assert {a.cwd for a in cov.audits} == {"."}, cov  # the root policy governs them all


def test_audit_toml_output_deny_is_honoured(tmp_path):
    repo = _gate_repo(
        tmp_path,
        {
            "tools/gate.sh": "cargo audit\n",
            ".cargo/audit.toml": '[output]\ndeny = ["unmaintained"]\nquiet = true\n',
        },
    )
    (a,) = _covered(repo).audits
    kinds = gate_coverage.denied_kinds(repo, a)
    assert "unmaintained" in kinds and "unsound" not in kinds, kinds
    assert GATED  # the shared fixture's gate is the one these tests depart from
