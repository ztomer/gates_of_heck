"""lib/goh-rustc keeps the build dir's path out of sccache's Rust keys (GOH.SCCACHEPATHS, design
GOH.CACHEEVICT section 4, F2 and F3).

* F2: a proc-macro dylib's install name was its absolute build-dir path, so its bytes, and the key
  of every crate that uses it, differed per build dir. On macOS the shim names it
  `@rpath/lib<crate>.dylib`; rustc dlopens proc-macros by path and never reads the name.
* F3: a crate whose build script writes OUT_DIR got the build dir's path in its dep-info
  (`include!(concat!(env!("OUT_DIR"), ...))`) and its rlib, so it and its dependents missed in
  every new build dir. The shim points OUT_DIR at a copy named by the content's hash.

A checkout path that differs (the workspace member's cwd, design cause E) still misses: sccache
0.19 hashes the cwd and has no Rust path remap, and SCCACHE_BASEDIRS is C-only and unsound."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from env_probe import probe_sh, read_probe
from timing_bounds import patience

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT / "lib" / "goh-rustc"
INSTALL_NAME = "link-arg=-Wl,-install_name,@rpath/libpm.dylib"


def _shim(tmp: Path, *args: str, **extra: str) -> tuple[list[str], dict[str, str]]:
    """The argv and the OUT_DIR the next stage got from the shim."""
    seen = tmp / "seen"
    nxt = tmp / "next.sh"
    nxt.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "{seen}.args"\n'
        f"{probe_sh(seen, keep=('OUT_DIR',))}\n"
    )
    nxt.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "GOH_RUSTC_NEXT": str(nxt)}
    env["GOH_OUTDIR_STORE"] = str(tmp / "store")
    r = subprocess.run(
        [str(SHIM), "rustc", *args],
        env={**env, **extra},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return Path(f"{seen}.args").read_text().splitlines(), read_probe(seen)


@pytest.mark.parametrize("spelling", [("--crate-type", "proc-macro"), ("--crate-type=proc-macro",)])
def test_a_proc_macro_gets_a_path_free_install_name_on_macos(tmp_path: Path, spelling) -> None:
    argv = ["--crate-name", "pm", *spelling, "src/lib.rs"]
    args, _ = _shim(tmp_path, *argv, GOH_RUSTC_OS="Darwin")
    assert args == ["rustc", *argv, "-C", INSTALL_NAME]


def test_other_crates_and_other_systems_pass_unchanged(tmp_path: Path) -> None:
    lib = ["--crate-name", "pm", "--crate-type", "lib", "src/lib.rs"]
    assert _shim(tmp_path, *lib, GOH_RUSTC_OS="Darwin")[0] == ["rustc", *lib]
    pm = ["--crate-name", "pm", "--crate-type", "proc-macro", "src/lib.rs"]
    assert _shim(tmp_path, *pm, GOH_RUSTC_OS="Linux")[0] == ["rustc", *pm]


def _out_dir(at: Path, value: str) -> Path:
    (at / "sub").mkdir(parents=True)
    (at / "v.rs").write_text(value)
    (at / "sub" / "w.txt").write_text("w")
    return at


def test_out_dir_is_named_by_its_content(tmp_path: Path) -> None:
    a = _out_dir(tmp_path / "a" / "out", "pub const V: u32 = 1;\n")
    b = _out_dir(tmp_path / "b" / "out", "pub const V: u32 = 1;\n")
    c = _out_dir(tmp_path / "c" / "out", "pub const V: u32 = 2;\n")
    got = [Path(_shim(tmp_path, "x.rs", OUT_DIR=str(d))[1]["OUT_DIR"]) for d in (a, b, c)]
    assert got[0] == got[1], "the same content got two paths"
    assert got[0] != got[2], "different content got one path"
    for orig, cas in zip((a, c), (got[0], got[2]), strict=True):
        assert cas.parent == tmp_path / "store"
        assert (cas / "v.rs").read_text() == (orig / "v.rs").read_text()
        assert (cas / "sub" / "w.txt").read_text() == "w"
    assert sorted(p.name for p in (tmp_path / "store").iterdir()) == sorted(
        {got[0].name, got[2].name}
    )


def test_file_boundaries_are_part_of_the_content(tmp_path: Path) -> None:
    """`ab` + `` and `a` + `b` concatenate alike: a hash of the bytes alone would share them."""
    one, two = tmp_path / "one", tmp_path / "two"
    for d, (a, b) in ((one, ("ab", "")), (two, ("a", "b"))):
        d.mkdir()
        (d / "a").write_text(a)
        (d / "b").write_text(b)
    got = {_shim(tmp_path, "x.rs", OUT_DIR=str(d))[1]["OUT_DIR"] for d in (one, two)}
    assert len(got) == 2, got


def test_an_out_dir_the_shim_cannot_copy_is_passed_on_unchanged(tmp_path: Path) -> None:
    a = _out_dir(tmp_path / "a" / "out", "x")
    (tmp_path / "store").write_text("a file where the store should be")
    assert _shim(tmp_path, "x.rs", OUT_DIR=str(a))[1]["OUT_DIR"] == str(a)
    missing = str(tmp_path / "nowhere")
    assert _shim(tmp_path, "x.rs", OUT_DIR=missing)[1]["OUT_DIR"] == missing
    assert "OUT_DIR" not in _shim(tmp_path, "x.rs")[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _crate(at: Path, toml: str, files: dict[str, str]) -> None:
    for rel, text in {"Cargo.toml": toml, **files}.items():
        (at / rel).parent.mkdir(parents=True, exist_ok=True)
        (at / rel).write_text(text)


def _pkg(name: str, extra: str = "") -> str:
    return f'[package]\nname = "{name}"\nversion = "0.0.0"\nedition = "2021"\n{extra}'


def _shared(root: Path, pm_value: int, od_value: int) -> None:
    """Four crates at ONE path both checkouts depend on, as registry crates are: pm a proc-macro,
    usepm a user of it, od a crate that includes its build script's OUT_DIR, useod a user of od."""
    _crate(
        root / "pm",
        _pkg("pm", "[lib]\nproc-macro = true\n"),
        {
            "src/lib.rs": "use proc_macro::TokenStream;\n#[proc_macro]\n"
            f'pub fn v(_: TokenStream) -> TokenStream {{ "{pm_value}u32".parse().unwrap() }}\n'
        },
    )
    _crate(
        root / "usepm",
        _pkg("usepm", '[dependencies]\npm = { path = "../pm" }\n'),
        {"src/lib.rs": "pub fn f() -> u32 { pm::v!() }\n"},
    )
    _crate(
        root / "od",
        _pkg("od"),
        {
            "build.rs": 'fn main() { let d = std::env::var("OUT_DIR").unwrap();\n'
            'std::fs::write(format!("{d}/v.rs"), '
            f'"pub const V: u32 = {od_value};").unwrap(); }}\n',
            "src/lib.rs": 'include!(concat!(env!("OUT_DIR"), "/v.rs"));\n',
        },
    )
    _crate(
        root / "useod",
        _pkg("useod", '[dependencies]\nod = { path = "../od" }\n'),
        {"src/lib.rs": "pub fn g() -> u32 { od::V }\n"},
    )


@pytest.mark.load_sensitive(reason="a private sccache server and six small real builds")
@pytest.mark.skipif(sys.platform != "darwin", reason="F2 is macOS-only: ELF proc-macros unverified")
def test_shared_crates_hit_across_build_dirs_and_miss_when_their_content_changes(
    tmp_path: Path,
) -> None:
    """The c/d against a/b probe of the design, as a test. One top crate, built from a fresh
    checkout and build dir each time, uses the four shared crates. Straight to sccache the second
    build dir hits none of od, usepm, useod (the instrument can see the misses); through the shim it
    hits all three. A changed proc-macro then misses its user only, a changed build-script output
    od and useod only.
    A private server: its own port, cache dir and empty config."""
    sccache, cargo = shutil.which("sccache"), shutil.which("cargo")
    if sccache is None or cargo is None:
        pytest.skip("no sccache or cargo on PATH")
    shared, top = tmp_path / "shared", tmp_path / "proto"
    _shared(shared, 1, 1)
    deps = "[dependencies]\n" + "".join(
        f'{c} = {{ path = "{shared}/{c}" }}\n' for c in ("usepm", "useod")
    )
    _crate(
        top, _pkg("top", deps), {"src/lib.rs": "pub fn h() -> u32 { usepm::f() + useod::g() }\n"}
    )
    (tmp_path / "sccache.toml").write_text("")
    gone = ("CARGO_", "SCCACHE_", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "GOH_")
    env = {k: v for k, v in os.environ.items() if not k.startswith(gone)}
    env.update(
        SCCACHE_SERVER_PORT=str(_free_port()),
        SCCACHE_DIR=str(tmp_path / "cache"),
        SCCACHE_CONF=str(tmp_path / "sccache.toml"),
        SCCACHE_IDLE_TIMEOUT="120",  # a backstop: the finally below stops it
        GOH_RUSTC_NEXT=sccache,
        GOH_OUTDIR_STORE=str(tmp_path / "store"),
    )

    def run(*argv: str, cwd: Path = tmp_path) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=patience(180),
        )

    def stats() -> tuple[int, int]:
        r = run(sccache, "--show-stats", "--stats-format", "json")
        assert r.returncode == 0, r.stderr
        s = json.loads(r.stdout)["stats"]
        return s["cache_hits"]["counts"].get("Rust", 0), s["compile_requests"]

    def build(wrapper: str, name: str) -> int:
        """Rust cache hits of one build of top, from a checkout and build dir of its own, through
        `wrapper`. top itself always misses: its cwd is the checkout (cause E, kept)."""
        checkout = tmp_path / name / "top"
        shutil.copytree(top, checkout)
        flags = ["--config", f"build.rustc-wrapper='{wrapper}'", "-q"]
        flags += ["--config", f"build.build-dir='{tmp_path / name}'", "--color", "never"]
        flags += ["--target-dir", str(tmp_path / name / "t"), "--config", "build.incremental=false"]
        before = stats()[0]
        r = run(cargo, "build", *flags, cwd=checkout)
        assert r.returncode == 0, r.stderr
        hits = stats()[0] - before
        requests = stats()[1]
        r = run(cargo, "build", *flags, cwd=checkout)
        assert r.returncode == 0, r.stderr
        assert stats()[1] == requests, f"{name}: a second build in the same dir was not fresh"
        return hits

    assert run(sccache, "--start-server").returncode == 0
    try:
        build(sccache, "raw-a")
        raw = build(sccache, "raw-b")
        build(str(SHIM), "shim-a")
        shimmed = build(str(SHIM), "shim-b")
        _shared(shared, 2, 1)
        pm_changed = build(str(SHIM), "shim-c")
        _shared(shared, 2, 2)
        od_changed = build(str(SHIM), "shim-d")
    finally:
        run(sccache, "--stop-server")
    # Straight to sccache, a second build dir hits FEWER shared crates than through the shim: that
    # is both the probe's calibration (it can see a miss) and the shim's reason to exist. The count
    # is sccache's, not ours: 0 of 3 under 0.19, 1 of 3 under 0.20, which stopped hashing
    # CARGO_TARGET_DIR (#3050) and hashes proc-macro externs by metadata (#2882) (measured
    # 2026-11-23). Pinning raw == 0 made the first 0.20 install turn every land red.
    assert raw < shimmed, (
        f"straight to sccache a second build dir hit {raw} of 3, as many as the shim ({shimmed}):"
        " the probe is blind, or sccache now does the shim's job (then retire the shim)"
    )
    assert shimmed == 3, "through the shim od, usepm and useod should all hit"
    assert pm_changed == 2, "a changed proc-macro: only usepm should miss"
    assert od_changed == 1, "a changed build-script output: od and useod should miss"
