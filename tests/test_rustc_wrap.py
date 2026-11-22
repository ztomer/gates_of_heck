"""lib/rustc_wrap.py wires lib/goh-rustc machine-wide (GOH.RUSTCWRAPWIRE): a trampoline that runs
goh's wrapper, or plain sccache when goh is gone; ~/.zshenv's RUSTC_WRAPPER (which outranks cargo's
config) and ~/.cargo/config.toml's `build.rustc-wrapper` both name it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from env_probe import probe_sh, read_probe

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import rustc_wrap  # noqa: E402

del sys.path[0]

ZSHENV = "# Shared Rust compile cache\nexport RUSTC_WRAPPER=sccache\nexport CARGO_INCREMENTAL=0\n"
CONFIG = '[env]\nCC = "/usr/bin/clang"\n\n[build]\nrustc-wrapper = "sccache"\nbuild-dir = "x"\n'


def _paths(tmp: Path, goh: Path | None = None) -> rustc_wrap.Paths:
    return rustc_wrap.Paths(
        zshenv=tmp / ".zshenv",
        cargo_config=tmp / ".cargo" / "config.toml",
        bin=tmp / "bin" / "goh-rustc",
        goh=goh or ROOT,
    )


def _seed(p: rustc_wrap.Paths) -> None:
    p.zshenv.write_text(ZSHENV)
    p.cargo_config.parent.mkdir(parents=True)
    p.cargo_config.write_text(CONFIG)


def test_apply_points_both_settings_at_the_trampoline_and_keeps_every_other_line(
    tmp_path: Path,
) -> None:
    p = _paths(tmp_path)
    _seed(p)
    before = rustc_wrap.check(p)
    assert len(before) == 3, before  # the trampoline, the zshenv line, the cargo key
    rustc_wrap.apply(p)
    assert rustc_wrap.check(p) == []
    z = p.zshenv.read_text().splitlines()
    assert z[0] == "# Shared Rust compile cache" and z[2] == "export CARGO_INCREMENTAL=0"
    assert z[1] == rustc_wrap.zshenv_line(p.bin)
    c = p.cargo_config.read_text()
    assert f'rustc-wrapper = "{p.bin}"' in c and 'build-dir = "x"' in c and "CC =" in c
    assert os.access(p.bin, os.X_OK)
    once = (p.zshenv.read_text(), c, p.bin.read_text())
    rustc_wrap.apply(p)
    assert (p.zshenv.read_text(), p.cargo_config.read_text(), p.bin.read_text()) == once


def test_apply_adds_what_is_absent(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    p.zshenv.write_text("export A=1")
    p.cargo_config.parent.mkdir(parents=True)
    p.cargo_config.write_text('[env]\nCC = "cc"\n')
    rustc_wrap.apply(p)
    assert rustc_wrap.check(p) == []
    assert p.zshenv.read_text().splitlines() == ["export A=1", rustc_wrap.zshenv_line(p.bin)]
    key = f'rustc-wrapper = "{p.bin}"   {rustc_wrap.MARK}'
    assert p.cargo_config.read_text().splitlines()[-2:] == ["[build]", key]


def test_check_names_a_second_wrapper_export(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _seed(p)
    rustc_wrap.apply(p)
    p.zshenv.write_text(p.zshenv.read_text() + "export RUSTC_WRAPPER=sccache\n")
    assert [m for m in rustc_wrap.check(p) if "RUSTC_WRAPPER" in m], rustc_wrap.check(p)


def _run(argv: list[str], **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _recorder(at: Path, seen: Path) -> Path:
    at.parent.mkdir(parents=True, exist_ok=True)
    at.write_text(f'#!/bin/sh\necho "$*" > "{seen}.args"\n{probe_sh(seen)}\n')
    at.chmod(0o755)
    return at


def test_the_trampoline_runs_gohs_wrapper_which_drops_cargos_variables(tmp_path: Path) -> None:
    """The installed route end to end: an exported CARGO_TERM_COLOR, the variable that cost every
    hit in the design's probe, never reaches what runs after goh's real wrapper."""
    p = _paths(tmp_path)
    _seed(p)
    rustc_wrap.apply(p)
    seen = tmp_path / "seen"
    nxt = _recorder(tmp_path / "next.sh", seen)
    path = os.environ["PATH"]
    r = _run([str(p.bin), "rustc", "-vV"], PATH=path, GOH_RUSTC_NEXT=str(nxt), CARGO_TERM_COLOR="x")
    assert r.returncode == 0, r.stderr
    assert "CARGO_TERM_COLOR" not in read_probe(seen)
    assert Path(f"{seen}.args").read_text() == "rustc -vV\n"


def test_a_missing_goh_falls_back_to_plain_sccache(tmp_path: Path) -> None:
    goh = tmp_path / "goh"
    (goh / "lib").mkdir(parents=True)
    p = _paths(tmp_path, goh)
    _seed(p)
    rustc_wrap.apply(p)
    seen = tmp_path / "seen"
    _recorder(tmp_path / "fakebin" / "sccache", seen)
    path = f"{tmp_path / 'fakebin'}:{os.environ['PATH']}"
    r = _run([str(p.bin), "rustc", "-vV"], PATH=path, CARGO_TERM_COLOR="x")
    assert r.returncode == 0, r.stderr
    assert Path(f"{seen}.args").read_text() == "rustc -vV\n"
    assert "CARGO_TERM_COLOR" in read_probe(seen), "plain sccache got goh's filtering"


@pytest.mark.skipif(shutil.which("zsh") is None, reason="no zsh")
def test_the_zshenv_line_names_sccache_when_the_trampoline_is_gone(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _seed(p)
    rustc_wrap.apply(p)
    ask = ["zsh", "-f", "-c", f'. "{p.zshenv}"; print -r -- "$RUSTC_WRAPPER"']
    path = os.environ["PATH"]
    assert _run(ask, PATH=path).stdout.strip() == str(p.bin)
    p.bin.unlink()
    assert _run(ask, PATH=path).stdout.strip() == "sccache"


def test_the_trampoline_names_the_main_checkout_never_a_worktree(tmp_path: Path) -> None:
    main = rustc_wrap.main_checkout(ROOT)
    assert (main / ".git").is_dir(), f"{main} is not the main checkout"
    assert rustc_wrap.main_checkout(tmp_path) == tmp_path  # not a checkout: itself
