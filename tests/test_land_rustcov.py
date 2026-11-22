"""A land measures the diff coverage of its own changed Rust lines (GOH.LANDRUSTCOV).

The calibration, planted on a REAL instrumented run: a fixture crate's land adds an arm to its
binary; the land's selected test (the external command's argument) that drives the arm is green,
one that drives only the old arm is red naming the new arm's line, and a land changing no Rust
file measures nothing and builds nothing. lib/land_rustcov.py; the coverage gate's --diff-only.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, commit_all, git, hermetic_env
from timing_bounds import patience

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None
    or subprocess.run(
        ["cargo", "llvm-cov", "--version"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode,
    reason="cargo llvm-cov not available",
)

HEAD = (
    "fn main() {\n"
    "    let arg = std::env::args().last().unwrap_or_default();\n"
    "    match arg.as_str() {\n"
    '        "a" => println!("1"),\n'
)
ARM_B = '        "b" => println!("2"),\n'  # line 5 once landed
END = "        _ => {}\n    }\n}\n"


def _judge(crate: Path, *tests: str) -> subprocess.CompletedProcess:
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
        "from lib import land_rustcov; "
        "sys.exit(land_rustcov.judge(Path(sys.argv[2]), 'main', sys.argv[3:]))"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(REPO_ROOT), str(crate), *tests],
        cwd=crate, capture_output=True, text=True, timeout=patience(300), check=False,
        env=hermetic_env(GATES_OF_HECK_SUITE_NESTED_OK="1"), stdin=subprocess.DEVNULL,
    )  # fmt: skip


def _crate(tmp_path: Path) -> Path:
    crate = tmp_path / "fx"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "fx"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    (crate / ".gitignore").write_text("target/\n")
    # The land's selected tests reach the external command as its arguments (after `-q`), as
    # goh's reach tools/pytest.sh: here the last is the arm the instrumented binary is run with.
    (crate / ".gatesrc").write_text("GOH_COV_RUST_EXTERNAL='\"$GOH_COVERAGE_BIN_DIR/fx\"'\n")
    (crate / "src" / "main.rs").write_text(HEAD + END)
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=crate, check=True, stdin=subprocess.DEVNULL
    )
    fast_init(crate, "main")
    commit_all(crate, "base")
    git(crate, "checkout", "-q", "-b", "lane")
    return crate


def test_a_lands_uncovered_rust_line_is_red_naming_it_and_a_covered_one_green(
    tmp_path: Path,
) -> None:
    crate = _crate(tmp_path)
    (crate / "src" / "main.rs").write_text(HEAD + ARM_B + END)
    commit_all(crate, "arm b")

    r = _judge(crate, "a")  # the selected test drives only the old arm
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "src/main.rs:5" in out, out
    assert "floor" not in out.lower(), out  # a subset run judges no floor

    r = _judge(crate, "b")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "changed executable lines covered" in out, out


def test_a_land_changing_no_rust_file_measures_nothing(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    (crate / "README").write_text("docs only\n")
    commit_all(crate, "docs")
    r = _judge(crate)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "no Rust file changed -- nothing to measure" in out, out
    assert not (crate / "target").exists(), out  # no instrumented build was made


def test_the_measured_set_is_the_selections_binary_runners() -> None:
    """Only a test that runs the binary can cover a Rust line: the rest of the selection is not
    run instrumented, and a node id keeps its node."""
    from lib import land_rustcov

    chosen = [
        "tests/test_goh_entry.py",
        "tests/test_land_rustcov.py::test_the_measured_set_is_the_selections_binary_runners",
        "tests/test_fast_tests.py",
    ]
    got = land_rustcov.selected(REPO_ROOT, chosen)
    assert got == ["tests/test_goh_entry.py"], got


def test_the_pre_steps_second_run_measures_with_the_binarys_judges_moved_into_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a Rust change (`plan`), the chosen tests that run the binary leave the judges' run and
    the second run is the coverage gate's subset mode over them and the rest: the run replaces
    the plain one, it is never a second pass. Without one, both runs are plain pytest.sh."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import affected_tests as at

    judge = "tests/test_goh_entry.py::test_every_dispatched_check_is_a_real_subcommand"
    plain = "tests/test_fast_tests.py::test_the_set_is_cheap"
    chosen = {judge: "defines a test_every_/test_no_ test", plain: "changed"}
    seen: list[list[str]] = []
    monkeypatch.setattr(at, "timed", lambda _label, argv: seen.append(argv) or 0)
    monkeypatch.setattr(at.fast_tests, "collects", lambda *_a: 0)
    monkeypatch.setattr(at, "phases", lambda _root, c: ([judge], [plain]))
    monkeypatch.setenv("GATES_OF_HECK_SUITE_NESTED_OK", "1")
    monkeypatch.delenv("GOH_LAND_VERDICTS_RECORD", raising=False)

    assert at.run(REPO_ROOT, ["crates/goh/src/main.rs"], chosen, None, ("b" * 40, "CMD")) == 0
    assert len(seen) == 1, seen  # the binary's judge left the judges' run, which is then empty
    cov = seen[0]
    assert "--diff-only" in cov and f"GOH_CHANGE_BASE={'b' * 40}" in cov, cov
    ext = cov[cov.index("--external") + 1]
    assert ext.startswith("CMD -q ") and judge in ext and plain in ext, ext

    seen.clear()
    assert at.run(REPO_ROOT, ["crates/goh/src/main.rs"], chosen, None) == 0
    assert [a[-1] for a in seen] == [judge, plain] and "--diff-only" not in seen[1], seen
