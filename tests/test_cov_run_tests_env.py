"""The coverage runner gives each test binary cargo's `[env]` table (GOH.SWEEPREDS130).

`gates/_cov_run_tests.py` starts the built test binaries itself (GOH.COVPARBINS), so nothing cargo
does for a `cargo test` run happens unless the runner does it. token-pool's `.cargo/config.toml`
turns its user config layer off with `[env] POOL_USER_CONFIG_DIR = ""`; under v0.130.0's runner
the variable was absent and `the_gate_runs_with_the_user_layer_off` went red (2026-11-17). These
cases pin the table's rules as cargo documents them: every `.cargo/config.toml` from the run's
directory up, then `$CARGO_HOME/config.toml`, the deeper file winning a key; the environment
winning unless `force`; `relative` resolved against the directory holding `.cargo`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "gates"))
from _cargo_config import _cargo_env_table


def _cfg(dir_: Path, body: str, name: str = "config.toml") -> None:
    (dir_ / ".cargo").mkdir(parents=True, exist_ok=True)
    (dir_ / ".cargo" / name).write_text(body)


def test_a_string_entry_reaches_the_test(tmp_path: Path) -> None:
    _cfg(tmp_path, '[env]\nPOOL_USER_CONFIG_DIR = ""\n')
    assert _cargo_env_table(tmp_path, {}, tmp_path / "home") == {"POOL_USER_CONFIG_DIR": ""}


def test_the_deeper_file_wins_and_the_home_file_is_lowest(tmp_path: Path) -> None:
    home = tmp_path / "cargo-home"
    home.mkdir()
    (home / "config.toml").write_text('[env]\nA = "home"\nB = "home"\nC = "home"\n')
    _cfg(tmp_path / "ws", '[env]\nA = "ws"\nB = "ws"\n')
    _cfg(tmp_path / "ws" / "crate", '[env]\nA = "crate"\n')
    got = _cargo_env_table(tmp_path / "ws" / "crate", {}, home)
    assert got == {"A": "crate", "B": "ws", "C": "home"}


def test_the_environment_wins_unless_the_entry_is_forced(tmp_path: Path) -> None:
    _cfg(tmp_path, '[env]\nKEPT = "config"\nFORCED = { value = "config", force = true }\n')
    got = _cargo_env_table(tmp_path, {"KEPT": "env", "FORCED": "env"}, tmp_path / "home")
    assert got == {"FORCED": "config"}


def test_a_relative_entry_resolves_against_the_directory_holding_dot_cargo(tmp_path: Path) -> None:
    _cfg(tmp_path / "ws", '[env]\nR = { value = "fixtures/x", relative = true }\nP = "plain/x"\n')
    got = _cargo_env_table(tmp_path / "ws" / "sub", {}, tmp_path / "home")
    assert got == {"R": str(tmp_path / "ws" / "fixtures" / "x"), "P": "plain/x"}


def test_the_legacy_name_is_read_and_config_toml_wins_beside_it(tmp_path: Path) -> None:
    _cfg(tmp_path / "a", '[env]\nL = "legacy"\n', name="config")
    assert _cargo_env_table(tmp_path / "a", {}, tmp_path / "home") == {"L": "legacy"}
    _cfg(tmp_path / "a", '[env]\nL = "toml"\n')
    assert _cargo_env_table(tmp_path / "a", {}, tmp_path / "home") == {"L": "toml"}


def test_no_config_anywhere_is_no_variable(tmp_path: Path) -> None:
    assert _cargo_env_table(tmp_path, {}, tmp_path / "home") == {}


def test_an_unreadable_config_is_refused_naming_its_file(tmp_path: Path) -> None:
    _cfg(tmp_path, "[env\n")
    with pytest.raises(ValueError, match=r"\.cargo/config\.toml"):
        _cargo_env_table(tmp_path, {}, tmp_path / "home")
