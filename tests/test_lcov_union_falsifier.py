"""The falsifier of GOH.SPANFORGIVE: the producer's line rows are the union over instantiations.

lcov_merge.py judges a line by its own best count (DA), with no function-span forgiveness. That is
right only while llvm-cov's lcov export folds every instantiation of a generic into each line's
count, so a zero-count clone can never leave a line another instantiation ran at DA:0. This builds
one tiny program with rustc (no cargo), with `g::<i32>` run and `g::<u8>` instantiated but never
run, and pins both halves: the export carries the zero-count clone as its own FN record (the
instrument can see clones), and no line `g` ran reads 0 while `f`'s untaken arm does. When LLVM
starts exporting per-instantiation line rows, the second assertion fails and the ruling re-opens.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _small_rust import SMALL
from conftest import REPO_ROOT


def _llvm_bin() -> Path | None:
    if shutil.which("rustc") is None:
        return None
    r = subprocess.run(["rustc", "--print", "target-libdir"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    bins = Path(r.stdout.strip()).parent / "bin"
    return bins if (bins / "llvm-profdata").exists() and (bins / "llvm-cov").exists() else None


LLVM = _llvm_bin()
pytestmark = pytest.mark.skipif(LLVM is None, reason="rustc's llvm-tools component not installed")

SRC = """fn g<T: std::fmt::Display>(x: T, loud: bool) -> String {
    if loud {
        return format!("{x}!");
    }
    x.to_string()
}
fn f(x: u32) -> u32 {
    if x > 10 {
        return 0;
    }
    x + 1
}
fn main() {
    println!("{} {} {}", g(1i32, true), g(2i32, false), f(3));
    if std::env::args().count() > 99 {
        println!("{}", g(3u8, false));
    }
}
"""


def _run(*cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL, **kw)  # fmt: skip


def test_a_zero_count_clone_never_zeroes_a_line_another_instantiation_ran(tmp_path: Path) -> None:
    (tmp_path / "main.rs").write_text(SRC)
    _run("rustc", "--crate-name", "prog", "-C", "instrument-coverage", *SMALL, "-o", "prog",
         "main.rs", cwd=tmp_path)  # fmt: skip
    _run(str(tmp_path / "prog"), env={"LLVM_PROFILE_FILE": str(tmp_path / "run.profraw")})
    _run(str(LLVM / "llvm-profdata"), "merge", "-o", "run.profdata", "run.profraw", cwd=tmp_path)
    lcov = _run(str(LLVM / "llvm-cov"), "export", "-format=lcov", "prog",
                "-instr-profile=run.profdata", cwd=tmp_path).stdout  # fmt: skip
    fnda = [r.split(":", 1)[1].split(",", 1) for r in lcov.splitlines() if r.startswith("FNDA:")]
    g_counts = sorted(int(c) for c, name in fnda if "1g" in name)
    assert g_counts[0] == 0 and g_counts[-1] > 0, f"no zero-count clone of g to judge: {fnda}"
    da = {}
    for row in lcov.splitlines():
        if row.startswith("DA:"):
            ln, cnt = row[3:].split(",")[:2]
            da[int(ln)] = max(da.get(int(ln), 0), int(cnt))
    zero = sorted(ln for ln, c in da.items() if c == 0)
    assert zero == [9, 16], f"g's lines must read their run instantiation, f's arm 0: {zero}"
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "part-workspace.info").write_text(lcov)
    r = subprocess.run([sys.executable, str(REPO_ROOT / "gates" / "lcov_merge.py"), "--floor",
                        "100", str(parts)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 1 and ": 9, 16" in r.stdout, r.stdout + r.stderr
