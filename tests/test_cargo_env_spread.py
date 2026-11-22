"""check_cargo_env_spread: no goh code puts a cargo CONFIG variable in the environment of a cargo it
runs (GOH.SCCACHEENV, close-the-class).

sccache 0.19 hashes every CARGO_* variable rustc inherits into the Rust key (`compiler/rust.rs`,
step 8: all of them but CARGO_MAKEFLAGS, CARGO_REGISTRIES_*, CARGO_BUILD_JOBS and
CARGO_ENCODED_RUSTFLAGS). Cargo passes its own environment down to rustc, so an exported
`CARGO_BUILD_BUILD_DIR`, `CARGO_TARGET_DIR` or `CARGO_TERM_COLOR` -- a path per run, or a value no
other build sets -- made a fresh key for every compile under it: 68.0% of the machine's Rust misses
over 8.5 h (design GOH.CACHEEVICT section 4, F1; probe: `CARGO_TERM_COLOR` in the environment 0
hits of 12, `--color never` on the command line 12 of 12). Cargo keeps `--config`, `--target-dir`
and `--color` to itself, so the cure is the command line.

The gate reads every tracked Python, shell, Rust and .mjs file for a SET of one of cargo's config
variables: `VAR=` (a shell assignment or prefix, a Python keyword), `export VAR`, a `"VAR":` dict
key, `env["VAR"] =`, and `setenv`/`putenv`/`.env("VAR"`. A read (`${VAR:-}`, `.get("VAR")`), a
comment and an `echo` of a value are not sets. Known limit, stated: a name held in a variable and
set through it (`for key in ("CARGO_HOME", ...): env[key] = ...`) is not seen.

ALLOWED names each remaining set and why it spreads no key; an entry the tree no longer has FAILS,
so the list only shrinks.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Cargo's config variables (`CARGO_<SECTION>_<KEY>`, CARGO_HOME, CARGO_TARGET_DIR) and the ones
# cargo-llvm-cov reads; CARGO_BUILD_JOBS is one sccache leaves out of the key.
VAR = (
    r"CARGO_(?:TARGET_DIR|HOME|INCREMENTAL|BUILD_(?!JOBS\b)[A-Z_]+|TERM_[A-Z_]+|PROFILE_[A-Z_]+"
    r"|NET_[A-Z_]+|HTTP_[A-Z_]+|CACHE_[A-Z_]+|UNSTABLE_[A-Z_]+|LLVM_COV_[A-Z_]+)\b"
)
SETS = re.compile(
    rf"(?<![\w$.{{])({VAR})="
    rf"|export\s+({VAR})"
    rf"|[\"']({VAR})[\"']\s*:"
    rf"|\[[\"']({VAR})[\"']\]\s*=(?!=)"
    rf"|(?:setenv|putenv|\.env)\(\s*[\"']({VAR})[\"']"
)
ECHO = re.compile(r"\becho\b")

# (path, variable) -> why this set spreads no sccache key. Delete an entry when its set goes.
ALLOWED = {
    ("crates/goh/src/crates_behind.rs", "CARGO_TERM_COLOR"): "cargo update --dry-run: no rustc",
    ("lib/step_control.py", "CARGO_HOME"): "cargo fetch into a private home: no rustc",
    ("gates/_coverage_rust.sh", "CARGO_TARGET_DIR"): "coverage runs RUSTC_WRAPPER=''",
    ("gates/_coverage_rust.sh", "CARGO_BUILD_BUILD_DIR"): "coverage runs RUSTC_WRAPPER=''",
    ("gates/_coverage_rust.sh", "CARGO_INCREMENTAL"): "coverage runs RUSTC_WRAPPER=''",
    ("gates/_coverage_rust.sh", "CARGO_LLVM_COV_BUILD_DIR"): "coverage runs RUSTC_WRAPPER=''",
    ("tests/_drift_guard.py", "CARGO_BUILD_BUILD_DIR"): "external subcommands but clippy only",
    ("tests/_drift_guard.py", "CARGO_INCREMENTAL"): "external subcommands but clippy only",
    ("tests/suite_home.py", "CARGO_HOME"): "HOME moves; the toolchain must not (F1b strips it)",
    ("tests/test_check_mcp_wire_build.py", "CARGO_HOME"): "a fake cargo: no rustc",
    ("tests/test_coverage_external.py", "CARGO_TARGET_DIR"): "coverage, RUSTC_WRAPPER=''",
    ("tests/test_coverage_rust_exports.py", "CARGO_BUILD_BUILD_DIR"): "a fake cargo: no rustc",
    ("tests/test_proven_artifacts.py", "CARGO_TERM_COLOR"): "a fake cargo: no rustc",
    ("tests/test_share_grow.py", "CARGO_HOME"): "worker_share's runner env, no cargo run",
    ("tests/test_suite_drift.py", "CARGO_BUILD_BUILD_DIR"): "the caller-named build-dir case",
}


def sets_in(text: str) -> list[tuple[int, str]]:
    """(line, variable) of every cargo config variable `text` sets."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("#", "//")):
            continue
        for m in SETS.finditer(line):
            if ECHO.search(line, 0, m.start()):
                continue
            found.append((n, next(g for g in m.groups() if g)))
    return found


def check_cargo_env_spread(root: Path) -> tuple[list[str], list[str]]:
    """(sets the allowlist does not name, allowlist entries the tree no longer has)."""
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "*.py", "*.sh", "*.rs", "*.mjs"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=True,
    ).stdout.split("\0")
    seen: set[tuple[str, str]] = set()
    bad = []
    for rel in filter(None, tracked):
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, var in sets_in(text):
            seen.add((rel, var))
            if (rel, var) not in ALLOWED:
                bad.append(f"{rel}:{n}: sets {var}")
    stale = [f"{p}: {v}" for p, v in ALLOWED if (p, v) not in seen]
    return bad, stale


def test_no_goh_code_sets_a_cargo_config_variable_for_a_cargo_it_runs() -> None:
    bad, stale = check_cargo_env_spread(ROOT)
    why = (
        "sccache hashes every CARGO_* rustc inherits: pass --config K=V, --target-dir or --color "
        "on cargo's command line instead (GOH.SCCACHEENV)"
    )
    assert bad == [], why + ":\n" + "\n".join(bad)
    assert stale == [], "allowlist entries the tree no longer has (delete them):\n" + "\n".join(
        stale
    )


def test_the_gate_sees_each_shape_of_a_set() -> None:
    planted = (
        "export CARGO_TARGET_DIR=/t\n"
        "CARGO_TERM_COLOR=never cargo build\n"
        'env = dict(os.environ, CARGO_BUILD_BUILD_DIR="/b")\n'
        'env = {**os.environ, "CARGO_INCREMENTAL": "0"}\n'
        'env["CARGO_PROFILE_DEV_DEBUG"] = "0"\n'
        'cmd.env("CARGO_HOME", "/h");\n'
        'monkeypatch.setenv("CARGO_NET_OFFLINE", "true")\n'
    )
    got = [v for _, v in sets_in(planted)]
    assert got == [
        "CARGO_TARGET_DIR",
        "CARGO_TERM_COLOR",
        "CARGO_BUILD_BUILD_DIR",
        "CARGO_INCREMENTAL",
        "CARGO_PROFILE_DEV_DEBUG",
        "CARGO_HOME",
        "CARGO_NET_OFFLINE",
    ]


def test_a_read_a_comment_an_echo_or_an_unhashed_variable_is_not_a_set() -> None:
    clean = (
        'd="${CARGO_TARGET_DIR:-target}"\n'
        "# export CARGO_TARGET_DIR=/t\n"
        'echo "CARGO_HOME=${CARGO_HOME:-}" >> log\n'
        'os.environ.get("CARGO_HOME")\n'
        'if env["CARGO_HOME"] == x: pass\n'
        "export CARGO_BUILD_JOBS=4\n"
        'env["CARGO_PKG_NAME"] = "x"\n'
    )
    assert sets_in(clean) == []


def test_a_stale_allowlist_entry_fails(tmp_path: Path, monkeypatch) -> None:
    """The ratchet bites both ways: a set it does not name fails, and so does a name with no set."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, stdin=subprocess.DEVNULL)
    (tmp_path / "a.sh").write_text("export CARGO_TERM_COLOR=never\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "a.sh"], check=True, stdin=subprocess.DEVNULL
    )
    monkeypatch.setattr(sys.modules[__name__], "ALLOWED", {("gone.py", "CARGO_HOME"): "gone"})
    bad, stale = check_cargo_env_spread(tmp_path)
    assert bad == ["a.sh:1: sets CARGO_TERM_COLOR"]
    assert stale == ["gone.py: CARGO_HOME"]
