"""The falsifier of D-0011: a Rust coverage function hash does not see a const it reads.

GOH.COVSELECT asked whether an instrumented land could re-run only the tests whose per-test profile
reaches a changed function, judging the floor on a merged profile whose other tests' counts are
REUSED, keyed on the binary's unchanged functions (their coverage hash). That keying lies: rustc's
coverage hash covers a function's own body, so a change to a const it reads leaves the hash equal,
llvm-cov accepts the old profile for it, and the floor credits a branch the new binary never takes.
This builds one tiny program twice with rustc (no cargo) and pins both halves: the hash does not
move for a const change and does move for a body change (the instrument can see), and the reused
profile credits the branch the fresh run leaves uncovered. When rustc starts hashing what a body
reads, the first assertion fails and the ruling is re-opened.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _llvm_bin() -> Path | None:
    if shutil.which("rustc") is None:
        return None
    r = subprocess.run(["rustc", "--print", "target-libdir"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    bins = Path(r.stdout.strip()).parent / "bin"
    return bins if (bins / "llvm-profdata").exists() and (bins / "llvm-cov").exists() else None


LLVM = _llvm_bin()
pytestmark = pytest.mark.skipif(LLVM is None, reason="rustc's llvm-tools component not installed")

SRC = """const LIMIT: u32 = {limit};
fn a(x: u32) -> u32 {{ if x > LIMIT {{ 1 }} else {{ 2 }} }}
fn b(x: u32) -> u32 {{ x + {step} }}
fn main() {{ println!("{{}} {{}}", a(3), b(3)); }}
"""


def _build(root: Path, name: str, limit: int, step: int) -> tuple[Path, Path]:
    """(binary, the profile of one run of it). One source PATH for every version: a span's file is
    in the hash, as a land's worktree path would be."""
    d = root / name
    d.mkdir()
    src = root / "main.rs"
    src.write_text(SRC.format(limit=limit, step=step))
    exe = d / "prog"
    subprocess.run(["rustc", "--crate-name", "prog", "-C", "instrument-coverage", "-o", str(exe),
                    "main.rs"], cwd=root, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip
    raw = d / "run.profraw"
    subprocess.run([str(exe)], env={"LLVM_PROFILE_FILE": str(raw)}, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip
    prof = d / "run.profdata"
    subprocess.run([str(LLVM / "llvm-profdata"), "merge", "-o", str(prof), str(raw)], check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip
    return exe, prof


def _hashes(prof: Path) -> dict[str, str]:
    out = subprocess.run([str(LLVM / "llvm-profdata"), "show", "--all-functions", str(prof)],
                         capture_output=True, text=True, check=True,
                         stdin=subprocess.DEVNULL).stdout  # fmt: skip
    got, name = {}, ""
    for line in out.splitlines():
        if line.startswith("  ") and line.rstrip().endswith(":") and "Hash" not in line:
            name = line.strip().rstrip(":")
        elif "Hash:" in line and name:
            got[name.split("4prog")[-1]] = line.split("Hash:")[1].strip()
    return got


def _regions_of_a(exe: Path, prof: Path) -> list[tuple[int, int]]:
    """(column, count) of each region of fn a, the binary's mapping read with `prof`'s counts."""
    out = subprocess.run([str(LLVM / "llvm-cov"), "export", str(exe), f"-instr-profile={prof}"],
                         capture_output=True, text=True, check=True,
                         stdin=subprocess.DEVNULL).stdout  # fmt: skip
    fns = json.loads(out)["data"][0]["functions"]
    (fa,) = [f for f in fns if f["name"].endswith("1a")]
    return [(r[1], r[4]) for r in fa["regions"]]


def test_a_reused_profile_credits_a_branch_a_const_change_left(tmp_path: Path) -> None:
    old_exe, old = _build(tmp_path, "old", limit=5, step=1)
    new_exe, new = _build(tmp_path, "const", limit=1, step=1)
    _, body = _build(tmp_path, "body", limit=5, step=2)
    h_old, h_new, h_body = _hashes(old), _hashes(new), _hashes(body)
    assert h_old["1a"] == h_new["1a"], "a const change moved the reader's hash: re-open D-0011"
    assert h_old["1b"] != h_body["1b"], "the instrument is blind to a body change"
    reused = dict(_regions_of_a(new_exe, old))  # the old run's counts, on the new binary
    fresh = dict(_regions_of_a(new_exe, new))
    then_, else_, _ = sorted(c for c in fresh if c > 30)  # `{ 1 }`, `{ 2 }`, the tail
    assert (fresh[then_], fresh[else_]) == (1, 0), fresh  # 3 > 1: the new binary takes `then`
    assert (reused[then_], reused[else_]) == (0, 1), reused  # the old counts credit `else`
    assert old_exe != new_exe
