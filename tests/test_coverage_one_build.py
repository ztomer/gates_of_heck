"""coverage_gate.sh --lang rust: one build, N profiles (BACKLOG P1d).

Split from test_coverage_gate.py for the line cap; the fake-cargo pattern is that file's.
"""

from __future__ import annotations

import os
import stat
import subprocess

from conftest import llvm_cov_shim
from test_coverage_gate import COV_GATE

# ── one build, N profiles (BACKLOG P1d) ──────────────────────────────────────
#
# Every per-target export used to clean and RECOMPILE the workspace (16 targets x ~2 s on
# media_server's mediaops-rs); then each target was its own `--no-clean` export with the profile
# reset between them. Now ONE run measures every declared target (A/B on all 29 media_server
# crates: identical merged reports, 226 -> 173 s). The fake models the profile leak that made the
# per-target resets necessary; with one run there is nothing to carry.

LEAKY_CARGO = """\
#!/bin/bash
state="$FAKE_STATE"
echo "$*" >> "$state.calls"
case "$1" in
  metadata)
    cat <<'JSON'
{"packages":[{"name":"lk","targets":[{"name":"lk","kind":["lib"]},
 {"name":"a","kind":["test"]},{"name":"b","kind":["test"]}]}]}
JSON
    exit 0 ;;
  llvm-cov)
    case "$2" in
      --version) exit 0 ;;
      clean)
        case "$*" in
          *--profraw-only*) rm -f "$state.profile" ;;
          *) rm -f "$state.profile" "$state.built" ;;
        esac
        exit 0 ;;
    esac
    case "$*" in *--no-clean*) ;; *) rm -f "$state.built" "$state.profile" ;; esac
    [ -f "$state.built" ] || echo build >> "$state.builds"
    touch "$state.built"
    out=""; prev=""; me="lib"
    for a in "$@"; do
      [ "$prev" = "--output-path" ] && out="$a"
      [ "$prev" = "--test" ] && me="$a"
      prev="$a"
    done
    echo "$me" >> "$state.profile"
    {
      echo "SF:$PWD/src/lib.rs"
      while read -r t; do echo "DA:1,1"; echo "# ran $t"; done < "$state.profile"
      echo "LF:1"; echo "LH:1"; echo "end_of_record"
    } > "$out"
    exit 0 ;;
esac
exit 0
"""


def test_one_instrumented_run_of_exactly_the_declared_targets_and_one_build(tmp_path):
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    shim = bin_ / "cargo"
    shim.write_text(LEAKY_CARGO)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    llvm_cov_shim(bin_)
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "lib.rs").write_text("pub fn f() {}\n")
    state = tmp_path / "state"
    env = dict(os.environ)
    env.update(PATH=f"{bin_}:/usr/bin:/bin", FAKE_STATE=str(state))
    env.pop("GOH_COV_FLOOR_RUST", None)
    r = subprocess.run(
        ["/bin/bash", str(COV_GATE), str(proj), "--lang", "rust", "--floor", "0"],
        cwd=str(proj),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    runs = [
        c.split()
        for c in (tmp_path / "state.calls").read_text().splitlines()
        if c.startswith("llvm-cov") and "--output-path" in c
    ]
    assert len(runs) == 1, runs  # ONE instrumented run, not one per target
    run = runs[0]
    # exactly the declared kinds: a lib and tests here, no bin; never examples or benches
    assert "--lib" in run and "--tests" in run and "--bins" not in run, run
    assert not {"--all-targets", "--examples", "--benches"} & set(run), run
    part = proj / "target" / "llvm-cov" / "lcov-parts" / "part-workspace.info"
    ran = [ln[6:] for ln in part.read_text().splitlines() if ln.startswith("# ran")]
    assert ran == ["lib"], ran  # the fake's one profile: a single run, nothing carried in
    assert (tmp_path / "state.builds").read_text().count("build") == 1


# ── the coverage run IS the test run (BACKLOG P1c) ──────────────────────────
#
# A consumer may drop its plain `cargo test` step only if a failing test under coverage turns the
# gate red AND says which test -- "export failed" names a mechanism, not a defect, and reads like a
# tooling problem someone will retry.

FAILING_TEST_CARGO = """\
#!/bin/bash
case "$1" in
  metadata)
    echo '{"packages":[{"name":"ft","targets":[{"name":"ft","kind":["lib"]}]}]}'
    exit 0 ;;
  llvm-cov)
    case "$2" in --version|clean) exit 0 ;; esac
    cat <<'OUT'
running 2 tests
test tests::fine ... ok
test tests::parses_the_header ... FAILED

failures:

---- tests::parses_the_header stdout ----
thread 'tests::parses_the_header' panicked at src/lib.rs:9:5:
assertion `left == right` failed

failures:
    tests::parses_the_header

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out
error: test failed, to rerun pass `--lib`
OUT
    exit 101 ;;
esac
exit 0
"""


def test_a_failing_test_under_coverage_is_named_as_a_test_failure(tmp_path):
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    shim = bin_ / "cargo"
    shim.write_text(FAILING_TEST_CARGO)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    llvm_cov_shim(bin_)
    proj = tmp_path / "proj"
    proj.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_}:/usr/bin:/bin"
    env.pop("GOH_COV_FLOOR_RUST", None)
    r = subprocess.run(
        ["/bin/bash", str(COV_GATE), str(proj), "--lang", "rust", "--floor", "0"],
        cwd=str(proj),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert r.returncode != 0, r.stdout + r.stderr
    assert "TESTS FAILED" in r.stderr, r.stderr
    assert "tests::parses_the_header" in r.stderr.split("TESTS FAILED", 1)[1].splitlines()[0]
