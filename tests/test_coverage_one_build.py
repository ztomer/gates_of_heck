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
# media_server's mediaops-rs). The build is now cleaned once and each export passes
# `--no-clean`; what must not carry between targets is the PROFILE. The fake below models the
# real leak measured on mediaops-rs: profile data accumulates across exports until
# `clean --profraw-only` runs, and each part reports every target whose run it accumulated.

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


def test_each_part_measures_only_its_own_target_and_the_build_happens_once(tmp_path):
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
    parts = proj / "target" / "llvm-cov" / "lcov-parts"
    for name, me in (
        ("part-lk-lib.info", "lib"),
        ("part-lk-test-a.info", "a"),
        ("part-lk-test-b.info", "b"),
    ):
        ran = [ln[6:] for ln in (parts / name).read_text().splitlines() if ln.startswith("# ran")]
        assert ran == [me], (name, ran)
    assert (tmp_path / "state.builds").read_text().count("build") == 1
