"""The coverage runner starts every test binary through cargo's configured runner (GOH.COVRUNNER).

`gates/_cov_run_tests.py` starts the built test binaries itself (GOH.COVPARBINS), so a runner a
repo declares in `.cargo/config.toml` (`[target.<triple>] runner` or `[target.'cfg(..)'] runner`)
never ran under the coverage gate: ztools' `tools/cargo_test_runner.sh` sandboxes HOME for its
tests, and `rust/tests/test_home_is_sandboxed.rs` was red under coverage, green under `cargo test`
(push log ztools-1c7056f-20261117T163203.log). The fixture crate's runner sets a variable its test
asserts: the same crate goes through `cargo test` and through the gate, and both must see it. The
resolver cases pin cargo's rules (`target_runner` in cargo's compilation.rs): the triple key and
its CARGO_TARGET_<TRIPLE>_RUNNER variable first, else exactly one matching `cfg(..)` key (two are
refused, as cargo refuses them), a string split on whitespace or an array, and a program with a
slash relative to the directory holding `.cargo`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "gates"))
from _cargo_config import _cargo_runner, _cfg_matches

HOST = "aarch64-apple-darwin"
CFGS = {"unix": None, "target_os": {"macos"}, "target_arch": {"aarch64"}, "target_family": {"unix"}}


def _cfg(dir_: Path, body: str) -> None:
    (dir_ / ".cargo").mkdir(parents=True, exist_ok=True)
    (dir_ / ".cargo" / "config.toml").write_text(body)


def _runner(cwd: Path, environ: dict[str, str] | None = None) -> list[str] | None:
    return _cargo_runner(cwd, environ or {}, cwd / "no-home", lambda: (HOST, CFGS))


def test_a_cfg_runner_with_a_slash_resolves_against_the_directory_holding_dot_cargo(
    tmp_path: Path,
) -> None:
    _cfg(tmp_path / "ws", "[target.'cfg(unix)']\nrunner = \"tools/run.sh --flag\"\n")
    assert _runner(tmp_path / "ws" / "crate") == [
        str(tmp_path / "ws" / "tools" / "run.sh"),
        "--flag",
    ]


def test_the_triple_key_wins_over_a_cfg_key_and_the_variable_over_the_file(tmp_path: Path) -> None:
    _cfg(
        tmp_path,
        f'[target.{HOST}]\nrunner = ["trip", "a b"]\n[target.\'cfg(unix)\']\nrunner = "cfg"\n',
    )
    assert _runner(tmp_path) == ["trip", "a b"]
    env = {"CARGO_TARGET_AARCH64_APPLE_DARWIN_RUNNER": "envrun -x"}
    assert _runner(tmp_path, env) == ["envrun", "-x"]


def test_a_cfg_that_does_not_match_is_no_runner_and_two_that_match_are_refused(
    tmp_path: Path,
) -> None:
    _cfg(tmp_path, "[target.'cfg(windows)']\nrunner = \"w\"\n")
    assert _runner(tmp_path) is None
    _cfg(
        tmp_path,
        "[target.'cfg(unix)']\nrunner = \"u\"\n"
        '[target.\'cfg(target_os = "macos")\']\nrunner = "m"\n',
    )
    with pytest.raises(ValueError, match="several matching"):
        _runner(tmp_path)


def test_the_deeper_file_wins_and_no_config_is_no_runner(tmp_path: Path) -> None:
    def no_rustc() -> tuple[str, dict]:
        raise AssertionError("rustc asked although no runner is declared")

    _cfg(tmp_path, '[env]\nX = "1"\n')
    assert _cargo_runner(tmp_path, {}, tmp_path / "no-home", no_rustc) is None
    _cfg(tmp_path, "[target.'cfg(unix)']\nrunner = \"outer\"\n")
    _cfg(tmp_path / "in", "[target.'cfg(unix)']\nrunner = \"inner\"\n")
    assert _runner(tmp_path / "in") == ["inner"]


@pytest.mark.parametrize(
    ("expr", "want"),
    [
        ("cfg(unix)", True),
        ("cfg(windows)", False),
        ('cfg(target_os = "macos")', True),
        ('cfg(all(unix, target_arch = "aarch64"))', True),
        ('cfg(any(windows, target_os = "linux"))', False),
        ("cfg(not(windows))", True),
        ("cfg(all())", True),
        ("cfg(any())", False),
    ],
)
def test_cfg_expressions_evaluate_as_cargo_evaluates_them(expr: str, want: bool) -> None:
    assert _cfg_matches(expr, CFGS) is want


# ---- the fixture crate, end to end -------------------------------------------------------------

_HAVE_LLVM_COV = (
    shutil.which("cargo") is not None
    and not subprocess.run(
        ["cargo", "llvm-cov", "--version"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode
)

TEST = """\
#[test]
fn the_runner_ran() {
    assert_eq!(
        std::env::var("COV_FROM_RUNNER").as_deref(),
        Ok("runner"),
        "the .cargo/config.toml runner started this binary, as cargo starts it"
    );
}
"""


def _crate(root: Path) -> Path:
    fast_init(root)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "tools").mkdir()
    (root / "Cargo.toml").write_text(
        '[package]\nname = "covrun"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    (root / "src" / "lib.rs").write_text("pub fn one() -> u32 {\n    1\n}\n")
    (root / "tests" / "runner.rs").write_text(TEST)
    run = root / "tools" / "run.sh"
    run.write_text('#!/bin/bash\nexport COV_FROM_RUNNER=runner\nexec "$@"\n')
    run.chmod(0o755)
    _cfg(root, "[target.'cfg(unix)']\nrunner = \"tools/run.sh\"\n")
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=root, check=True, stdin=subprocess.DEVNULL
    )
    return root


@pytest.mark.skipif(not _HAVE_LLVM_COV, reason="cargo llvm-cov not available")
def test_the_configured_runner_starts_the_binary_under_cargo_test_and_under_the_gate(
    tmp_path: Path,
) -> None:
    crate = _crate(tmp_path / "c")
    env = hermetic_env(drop_git=True, GOH_COV_CACHE=str(tmp_path / "cache"))
    env.pop("COV_FROM_RUNNER", None)
    plain = subprocess.run(
        ["cargo", "test", "--quiet", "--test", "runner"], cwd=crate, capture_output=True,
        text=True, timeout=patience(600), stdin=subprocess.DEVNULL, env=env, check=False,
    )  # fmt: skip
    assert plain.returncode == 0, plain.stdout + plain.stderr
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust",
         "--floor", "0", str(crate)],
        cwd=crate, capture_output=True, text=True, timeout=patience(600), stdin=subprocess.DEVNULL,
        env=env, check=False,
    )  # fmt: skip
    log = crate / "target" / "llvm-cov" / "lcov-parts" / "part-workspace.info.log"
    assert r.returncode == 0, r.stdout + r.stderr + (log.read_text() if log.exists() else "")
    assert "the_runner_ran ... ok" in log.read_text()
    assert os.environ.get("COV_FROM_RUNNER") is None


def test_rustflags_cfgs_are_the_targets_cfgs_as_cargo_asks_rustc() -> None:
    from _cargo_config import _rustc_target, _rustflags

    assert _rustflags({"CARGO_ENCODED_RUSTFLAGS": "--cfg\x1fcovrun_probe", "RUSTFLAGS": "x"}) == [
        "--cfg", "covrun_probe",
    ]  # fmt: skip
    assert _rustflags({"RUSTFLAGS": " --cfg  a "}) == ["--cfg", "a"]
    if shutil.which("rustc") is None:
        pytest.skip("no rustc")
    triple, cfgs = _rustc_target(None, {"RUSTFLAGS": "--cfg covrun_probe"})
    assert triple and "covrun_probe" in cfgs and "target_os" in cfgs
