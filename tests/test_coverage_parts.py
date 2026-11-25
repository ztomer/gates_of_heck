"""The suite's coverage in shards reads as the suite's coverage in one run (GOH.CIWALL).

Hosted CI makes the Rust coverage in parts -- the workspace's targets in the gate job, the external
suite as N shards (GOH_COV_PART) -- and judges them in a last job (GOH_COV_PARTS_IN,
gates/_coverage_parts.sh). On a fixture whose fake suite covers a known set of lines, the shards'
merge must give the unsharded run's verdict and summary at the same floor, green and red alike; a
part never judges the floor itself; and a merge missing one shard is incomplete, never smaller.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

from conftest import hermetic_env, llvm_cov_shim
from cov_fakes import SHOW_ENV_FLOW
from test_coverage_gate import COV_GATE
from timing_bounds import patience

# The workspace part covers its one line; the external part's report counts the lines the fake
# suite says it ran (`hits`, one line number per hit).
CARGO = (
    """\
#!/bin/bash
case "$1" in
  metadata)
    echo '{"packages":[{"name":"ft","manifest_path":"'"$PWD"'/Cargo.toml",
      "targets":[{"name":"ft","kind":["lib"]}]}]}'
    exit 0 ;;
  locate-project) echo "$PWD/Cargo.toml"; exit 0 ;;
  build)
    exe="$CARGO_LLVM_COV_TARGET_DIR/debug/ft"
    mkdir -p "${exe%/*}"; : >"$exe"; chmod +x "$exe"
    printf '{"reason":"compiler-artifact","filenames":[],"executable":"%s"}\\n' "$exe"
    exit 0 ;;
esac
"""
    + SHOW_ENV_FLOW
    + """\
case "$1 $2" in
  "llvm-cov report")
    out=""; prev=""
    for a in "$@"; do [ "$prev" = "--output-path" ] && out="$a"; prev="$a"; done
    if [ "${CARGO_TARGET_DIR##*/}" = llvm-cov-external ]; then
      {
        echo "SF:src/ext.rs"
        for i in 1 2 3 4 5 6 7 8; do
          echo "DA:$i,$(grep -cx "$i" "$CARGO_TARGET_DIR/hits" 2>/dev/null || true)"
        done
        echo end_of_record
      } >"$out"
    else
      printf 'SF:%s/src/lib.rs\\nDA:1,1\\nend_of_record\\n' "$PWD" >"$out"
    fi
    exit 0 ;;
esac
exit 0
"""
)

# Six tests: test i runs line 1 and line i; lines 7 and 8 no test reaches. A shard K/N runs the
# tests i with (i-1) % N == K-1, as tools/pytest.sh would run only its shard's.
SUITE = (
    'd="${GOH_COVERAGE_BIN_DIR%/*}"; mkdir -p "$d"; h="$d/hits"; k=1; n=1; '
    'if [ -n "${GOH_SUITE_SHARD:-}" ]; then k=${GOH_SUITE_SHARD%/*}; n=${GOH_SUITE_SHARD#*/}; fi; '
    'for i in 1 2 3 4 5 6; do [ $(( (i - 1) % n + 1 )) = "$k" ] || continue; '
    'echo 1 >>"$h"; echo "$i" >>"$h"; done'
)
# 7 of the 9 coverable lines: 77.78%.
GREEN, RED = "77", "78"


def _proj(tmp_path: Path) -> tuple[Path, dict]:
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    (bin_ / "cargo").write_text(CARGO)
    (bin_ / "cargo").chmod(0o755 | stat.S_IEXEC)
    llvm_cov_shim(bin_)
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "lib.rs").write_text("pub fn f() {}\n")
    (proj / "src" / "ext.rs").write_text("\n" * 8)
    return proj, hermetic_env(PATH=f"{bin_}:/usr/bin:/bin")


def _gate(proj: Path, env: dict, floor: str, **extra: str) -> subprocess.CompletedProcess:
    hits = proj / "target" / "llvm-cov-external" / "hits"
    if hits.exists():
        hits.unlink()  # each run is a fresh runner: no profile survives it
    return subprocess.run(
        ["/bin/bash", str(COV_GATE), str(proj), "--lang", "rust", "--floor", floor,
         "--external", SUITE],
        cwd=str(proj), capture_output=True, text=True, env={**env, **extra},
        timeout=patience(120), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def _parts(proj: Path) -> Path:
    return proj / "target" / "llvm-cov" / "lcov-parts"


def _made(proj: Path, env: dict, n: int) -> Path:
    """Every part of an n-shard run, collected as gate.yml's artifacts are: one directory."""
    merge = proj / "target" / "cov-merge"
    shutil.rmtree(merge, ignore_errors=True)
    merge.mkdir(parents=True)
    for part in ["workspace", *(f"{k}/{n}" for k in range(1, n + 1))]:
        # a part judges no floor: 100 is far above what any one part covers
        r = _gate(proj, env, "100", GOH_COV_PART=part)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "the floor is the merge's" in out, out
        for f in _parts(proj).glob("part-*.info*"):
            if f.name.endswith((".info", ".info.ok")):
                shutil.copy(f, merge / f.name)
    return merge


def test_the_merged_shards_give_the_unsharded_runs_verdict(tmp_path):
    proj, env = _proj(tmp_path)
    whole = {}
    for floor in (GREEN, RED):
        r = _gate(proj, env, floor)
        whole[floor] = r.returncode
        if floor == GREEN:
            assert r.returncode == 0, r.stdout + r.stderr
            summary = json.loads((_parts(proj) / "summary.json").read_text())
    assert whole == {GREEN: 0, RED: 1}, whole  # the fixture's calibration: the floor bites
    merge = _made(proj, env, 2)
    assert sorted(p.name for p in merge.glob("*.info")) == [
        "part-external-1-of-2.info", "part-external-2-of-2.info", "part-workspace.info",
    ]  # fmt: skip
    merged = {}
    for floor in (GREEN, RED):
        r = _gate(proj, env, floor, GOH_COV_PARTS_IN="target/cov-merge")
        merged[floor] = r.returncode
        assert "merging 3 coverage parts" in r.stdout + r.stderr, r.stdout + r.stderr
        if floor == GREEN:
            assert json.loads((merge / "summary.json").read_text()) == summary
    assert merged == whole


def test_a_merge_missing_one_shard_is_incomplete_not_smaller(tmp_path):
    """The parts are canned, as three shards would write them (the test above makes real ones)."""
    proj, env = _proj(tmp_path)
    merge = proj / "target" / "cov-merge"
    merge.mkdir(parents=True)
    for name in ("workspace", "external-1-of-3", "external-3-of-3"):
        (merge / f"part-{name}.info").write_text("SF:src/ext.rs\nDA:1,1\nend_of_record\n")
        (merge / f"part-{name}.info.ok").touch()
    r = _gate(proj, env, "0", GOH_COV_PARTS_IN="target/cov-merge")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "coverage exports incomplete" in out and "external shard 2 of 3" in out, out


def test_a_part_is_one_of_two_spellings(tmp_path):
    """The function reads the spelling, all five in one shell; the gate stopping on its 2 is the
    last run's."""
    proj, env = _proj(tmp_path)
    bad = ("0/2", "3/2", "a/2", "2", "1/2/3")
    script = (
        f". '{COV_GATE.parent}/_coverage_parts.sh'; err() {{ echo \"$*\" >&2; }}; EXTERNAL=true\n"
        'for p; do GOH_COV_PART="$p" cov_part_mode 2>/dev/null; echo "$p $?"; done\n'
        'GOH_COV_PART=2 cov_part_mode; GOH_COV_PART=2/3 cov_part_mode && echo "$COV_EXT_PART"\n'
    )
    r = subprocess.run(
        ["/bin/bash", "-c", script, "spellings", *bad],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.stdout.splitlines() == [*(f"{b} 2" for b in bad), "part-external-2-of-3"], r.stderr
    assert "give 'workspace' or K/N" in r.stderr, r.stderr
    r = _gate(proj, env, "0", GOH_COV_PART="1/2", GOH_COV_PARTS_IN="target")
    assert r.returncode == 2 and "one run each" in r.stderr, r.stderr
