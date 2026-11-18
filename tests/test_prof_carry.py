"""The coverage carry's audit (GOH.COVCARRY2, the owner's term 3; lib/prof_carry.py).

A land's coverage step stands on reuse: verdicts carried from another tree (the nodes an
instrumented run measured running no binary, GOH.COVCARRY), or rustcov's reused measurement (the
tests run plain). Each land CLAIMS its per-crate summary (gates/lcov_merge.py --summary) for its
tree; the idle lander's fresh full instrumented run of that tree compares its own summary with the
claim through `reuse_audit.compare("profcarry", ...)`, and a disagreement turns `profcarry` off:
the coverage run then carries nothing and rustcov measures.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "gates"))
import lcov_merge  # gates/, after its path
import prof_carry  # lib/, after its path
import reuse_audit

FIELDS = {"repo": "/r/.git", "tree": "t" * 40, "tools": "x" * 16}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("GOH_REUSE_NOW", raising=False)
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))  # a fault is never filed
    monkeypatch.setattr(prof_carry, "fields", lambda repo: dict(FIELDS))
    return tmp_path / "store"


def _part(d: Path, name: str, rows: dict[str, list[tuple[int, int]]]) -> None:
    text = ""
    for sf, lines in rows.items():
        text += f"SF:{sf}\n" + "".join(f"DA:{ln},{n}\n" for ln, n in lines) + "end_of_record\n"
    (d / f"part-{name}.info").write_text(text)


def test_the_merge_writes_each_crates_covered_and_coverable_lines(tmp_path: Path) -> None:
    """The summary is the floor's own count, per crate (`crates/<name>/`), from every part."""
    d = tmp_path / "parts"
    d.mkdir()
    _part(d, "workspace", {"/w/crates/goh/src/a.rs": [(1, 1), (2, 0)],
                           "/w/crates/goh-sys/src/b.rs": [(1, 0)]})  # fmt: skip
    _part(d, "external", {"/w/crates/goh/src/a.rs": [(2, 3)], "/w/src/c.rs": [(1, 1)]})
    out = tmp_path / "summary.json"
    rc = lcov_merge.main([str(d), "--floor", "0", "--summary", str(out)])
    assert rc == 0
    assert json.loads(out.read_text()) == {
        "crates": {"goh": [2, 2], "goh-sys": [0, 1], "-": [1, 1]}
    }


def _summary(tmp_path: Path, crates: dict[str, list[int]], name: str = "s.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"crates": crates}))
    return p


def test_a_fresh_run_that_agrees_with_the_claim_keeps_the_carry_on(tmp_path: Path) -> None:
    prof_carry.claim(REPO_ROOT, _summary(tmp_path, {"goh": [9000, 9400]}), "land gate")
    fresh = _summary(tmp_path, {"goh": [9000, 9400]}, "fresh.json")  # the measured band: 0
    said = prof_carry.compare(REPO_ROOT, fresh)
    assert "agree" in said, said
    st = reuse_audit.state("profcarry")
    assert st["state"] == "on" and st["window"]["agreed"] == 1, st
    assert prof_carry.ask(REPO_ROOT) in ("reuse", "shadow")


def test_a_fresh_run_that_disagrees_turns_the_carry_and_rustcov_reuse_off(tmp_path: Path) -> None:
    """The calibration: a claim 40 lines above what the fresh run measured is a carried profile
    that was not this tree's -- the path goes off, and every reuse it gates asks and is told so."""
    prof_carry.claim(REPO_ROOT, _summary(tmp_path, {"goh": [9040, 9400]}), "land gate")
    said = prof_carry.compare(REPO_ROOT, _summary(tmp_path, {"goh": [9000, 9400]}, "f.json"))
    assert "DISAGREE" in said, said
    assert reuse_audit.state("profcarry")["state"] == "off"
    assert prof_carry.ask(REPO_ROOT) == "off"
    assert not prof_carry.on()


# Two full instrumented runs of one set of Rust inputs (GOH.COVCARRY3, 2026-11-18): the coverage
# step's claim on 5bce690b's tree in a land (unremapped build) and a full run of 6d066d8c's in a
# lane worktree (remapped, path-independent), at load ~48 -- the same covered lines in every crate.
MEASURED = {"goh": [26844, 27517], "goh-golden": [392, 397], "goh-sys": [158, 163],
            "goh-testkit": [119, 121], "goh-tui": [80, 106]}  # fmt: skip


def test_the_noise_band_is_the_measured_one() -> None:
    """The band was a guess (3 lines, or 1 in 2000: 13 of goh's 27517); measured, it is 0."""
    assert prof_carry.agree(MEASURED, {k: list(v) for k, v in MEASURED.items()})
    for moved in (26845, 26843):
        assert not prof_carry.agree(MEASURED, {**MEASURED, "goh": [moved, 27517]}), moved


def test_the_noise_band_never_forgives_another_binary_or_another_crate() -> None:
    assert prof_carry.agree({"goh": [9000, 9400]}, {"goh": [9000, 9400]})
    assert not prof_carry.agree({"goh": [9000, 9400]}, {"goh": [9000, 9401]})  # coverable moved
    assert not prof_carry.agree({"goh": [9000, 9400]}, {"goh": [9000, 9400], "x": [1, 1]})
    assert not prof_carry.agree({"goh": [9000, 9400]}, {"goh": [8990, 9400]})


def test_no_claim_on_the_tree_compares_nothing(tmp_path: Path) -> None:
    said = prof_carry.compare(REPO_ROOT, _summary(tmp_path, {"goh": [1, 2]}))
    assert "no claim" in said, said
    assert reuse_audit.state("profcarry")["window"]["compared"] == 0


def test_the_cli_claims_and_compares(tmp_path: Path, monkeypatch) -> None:
    """The shell sites' form (gates/_coverage_rust.sh, gates/_rust_proven.sh): exit 0 either way."""
    s = _summary(tmp_path, {"goh": [5, 9]})
    lib = REPO_ROOT / "lib" / "prof_carry.py"
    repo = tmp_path / "not-a-repo"
    repo.mkdir()
    for verb in (["claim", "--summary", str(s)], ["compare", "--summary", str(s)], ["ask"]):
        argv = [sys.executable, str(lib), *verb, "--repo", str(repo)]
        r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           check=False)  # fmt: skip
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip(), verb


def test_off_the_coverage_run_reuses_nothing(tmp_path: Path, store: Path, monkeypatch) -> None:
    """The kill switch, in the suite's own plugin: under coverage a node measured running no binary
    is carried while `profcarry` is on, and nothing is once a fresh run disagreed."""
    import reuse
    from test_verdict_carry import SESSION_CONFTEST, _gate

    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    (d / "test_t.py").write_text("def test_ok():\n    pass\n")
    node = {"test_t.py::test_ok": False}
    reuse.record_suite(REPO_ROOT, "T0", "TOOLS", node, set(), True, "idle", instrumented=True,
                       measured=node)  # fmt: skip
    cov = {"GOH_COVERAGE_BIN_DIR": str(tmp_path), "LLVM_PROFILE_FILE": str(tmp_path / "g.profraw")}
    on = _gate(d, store, **cov)
    assert "1 deselected, carried from" in on.stdout, on.stdout
    prof_carry.claim(REPO_ROOT, _summary(tmp_path, {"goh": [90, 100]}), "land gate")
    prof_carry.compare(REPO_ROOT, _summary(tmp_path, {"goh": [50, 100]}, "f.json"))
    off = _gate(d, store, **cov)
    assert "the audit turned `profcarry` off" in off.stdout, off.stdout
    assert "PASSED test_t.py::test_ok" in off.stdout, off.stdout
