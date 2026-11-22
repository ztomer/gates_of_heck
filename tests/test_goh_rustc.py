"""lib/goh_rustc.sh, cargo's rustc-wrapper ahead of sccache (GOH.SCCACHEENV, F1b): the variables
only cargo reads leave the environment before sccache keys the compile, so an exported
CARGO_TERM_COLOR or CARGO_TARGET_DIR no longer makes a key no other build shares."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from timing_bounds import patience

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT / "lib" / "goh_rustc.sh"
DROPPED = ("CARGO_TARGET_DIR", "CARGO_HOME", "CARGO_INCREMENTAL", "CARGO_BUILD_BUILD_DIR")
DROPPED += ("CARGO_TERM_COLOR",)


def test_the_shim_drops_cargos_own_variables_and_keeps_the_crates(tmp_path: Path) -> None:
    seen = tmp_path / "seen"
    nxt = tmp_path / "next.sh"
    nxt.write_text(f'#!/bin/sh\necho "$*" > "{seen}.args"\nenv > "{seen}"\n')
    nxt.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "GOH_RUSTC_NEXT": str(nxt), "CARGO_PKG_NAME": "x"}
    env.update(dict.fromkeys(DROPPED, "/somewhere"))
    r = subprocess.run(
        [str(SHIM), "rustc", "--crate-name", "x", "a b.rs"],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    names = {line.split("=", 1)[0] for line in seen.read_text().splitlines()}
    assert not names & set(DROPPED), names & set(DROPPED)
    assert "CARGO_PKG_NAME" in names, "a variable cargo sets for the crate was dropped"
    assert Path(f"{seen}.args").read_text() == "rustc --crate-name x a b.rs\n"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.load_sensitive  # a private sccache server and two real compiles
def test_an_exported_cargo_term_color_hits_through_the_shim(tmp_path: Path) -> None:
    """The i probe of the design, as a test: one crate built warm, then rebuilt clean with
    CARGO_TERM_COLOR exported. Straight to sccache it misses (the instrument can see a miss);
    through the shim it hits. A private server: its own port, cache dir and empty config, so the
    machine's cache and its stats are never touched."""
    sccache, cargo = shutil.which("sccache"), shutil.which("cargo")
    if sccache is None or cargo is None:
        pytest.skip("no sccache or cargo on PATH")
    crate = tmp_path / "crate"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "shimprobe"\nversion = "0.0.0"\nedition = "2021"\n'
    )
    (crate / "src" / "lib.rs").write_text("pub fn f() -> u32 { 7 }\n")
    (tmp_path / "sccache.toml").write_text("")
    # RUSTC_WRAPPER in the environment outranks the `build.rustc-wrapper` each build names
    gone = ("CARGO_", "SCCACHE_", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER")
    env = {k: v for k, v in os.environ.items() if not k.startswith(gone)}
    env.update(
        SCCACHE_SERVER_PORT=str(_free_port()),
        SCCACHE_DIR=str(tmp_path / "cache"),
        SCCACHE_CONF=str(tmp_path / "sccache.toml"),
        SCCACHE_IDLE_TIMEOUT="120",  # a backstop: the finally below stops it
        GOH_RUSTC_NEXT=sccache,
    )

    def run(*argv: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            cwd=crate,
            env={**env, **extra},
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=patience(120),
        )

    def build(wrapper: str, *flags: str, **extra: str) -> int:
        """Rust cache hits of one clean build through `wrapper`."""
        dirs = ["--config", f"build.build-dir='{tmp_path / 'bd'}'"]
        dirs += ["--target-dir", str(tmp_path / "t"), "--config", "build.incremental=false"]
        assert run(cargo, "clean", *dirs).returncode == 0
        before = stats()
        wrap = ["--config", f"build.rustc-wrapper='{wrapper}'"]
        # after `build`: before it, cargo 1.100 drops `build.rustc-wrapper` (probed)
        r = run(cargo, "build", *wrap, "-q", *flags, *dirs, **extra)
        assert r.returncode == 0, r.stderr
        return stats() - before

    def stats() -> int:
        r = run(sccache, "--show-stats", "--stats-format", "json")
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)["stats"]["cache_hits"]["counts"].get("Rust", 0)

    assert run(sccache, "--start-server").returncode == 0
    try:
        # Each route is warmed first with the colour on the COMMAND LINE: cargo hands rustc
        # `--color` from either spelling, so only the environment differs. The user's `[env]`
        # CARGO_INCREMENTAL, which the shim drops too, keys the two routes apart, as it would
        # once every build goes through the shim.
        build(sccache, "--color", "never")
        raw = build(sccache, CARGO_TERM_COLOR="never")
        build(str(SHIM), "--color", "never")
        # plus a variable no build has had yet: a shim that passed them on could not hit the key
        # the raw build left behind
        fresh = {"CARGO_TERM_COLOR": "never", "CARGO_TERM_PROGRESS_WHEN": "never"}
        shimmed = build(str(SHIM), **fresh)
    finally:
        run(sccache, "--stop-server")
    assert raw == 0, "straight to sccache an exported CARGO_TERM_COLOR hit: the probe is blind"
    assert shimmed >= 1, "through the shim the exported CARGO_TERM_COLOR still missed"
