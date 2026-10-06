"""The native binary is judged by the SOURCE it was built from, not only its version (C3, R4a).

2026-10-05: the hook's `bin/goh` was built before C1 added the staged ruff-format step. Same version
number, so the version check passed it, and two unformatted files were committed through a gate
that announced every step it knew -- the step it did not know printed nothing. A version answers
"which release"; only the source answers "which steps".

So `build.rs` embeds the git tree of `crates/` and `Cargo.lock` it was built from (`goh source-tree`
prints it, `dirty` for an uncommitted build), and `gates/_goh_bin.sh` -- the one resolver every
caller uses -- compares it to HEAD's. A bin/goh that does not match is REBUILT from HEAD
(`scripts/build-goh.sh --if-stale`, under its lock) and then used; if the rebuild fails, the Python
checkers run and the line says so. An explicit GOH_BIN stays trusted, as documented.
"""

import subprocess
from pathlib import Path

from test_gate_environment import _clean_checkout_of_todays_gates
from conftest import hermetic_env


def _fake(path: Path, version: str, tree: str | None) -> Path:
    tree_line = f'[ "$1" = source-tree ] && {{ echo "{tree}"; exit 0; }}\n' if tree else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/bash\n"
        f'[ "$1" = --version ] && {{ echo "goh {version}"; exit 0; }}\n'
        + tree_line
        + 'echo "fake native structural ran"\nexit 0\n'
    )
    path.chmod(0o755)
    return path


def _head_tree(checkout: Path) -> str:
    specs = [f"HEAD:{i}" for i in ("crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml")]
    out = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", *specs],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return " ".join(out)


def _version(checkout: Path) -> str:
    line = next(
        l for l in (checkout / "Cargo.toml").read_text().splitlines() if l.startswith("version = ")
    )
    return line.split('"')[1]


def _structural(checkout: Path, tmp_path: Path, **env: str):
    repo = tmp_path / "consumer"
    repo.mkdir(exist_ok=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return subprocess.run(
        ["bash", str(checkout / "gates" / "structural.sh"), "--staged"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(checkout), **env),
        timeout=120,
    )


def _fake_builder(checkout: Path, version: str, stamp: str, ok: bool = True) -> Path:
    """Stands in for scripts/build-goh.sh: records the call, then lands a HEAD-stamped binary."""
    calls = checkout.parent / "builder.calls"
    body = f'echo "$*" >> "{calls}"\n' + (
        f"cat > \"{checkout}/bin/goh\" <<'EOF'\n"
        f'#!/bin/bash\n[ "$1" = --version ] && {{ echo "goh {version}"; exit 0; }}\n'
        f'[ "$1" = source-tree ] && {{ echo "{stamp}"; exit 0; }}\n'
        'echo "rebuilt native ran"\nexit 0\nEOF\n'
        f'chmod +x "{checkout}/bin/goh"\nexit 0\n'
        if ok
        else "echo 'cargo exploded' >&2\nexit 1\n"
    )
    (checkout / "scripts" / "build-goh.sh").write_text("#!/bin/bash\n" + body)
    return calls


def test_a_binary_built_from_heads_source_serves_the_gate(tmp_path):
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    _fake(checkout / "bin" / "goh", _version(checkout), _head_tree(checkout))
    calls = _fake_builder(checkout, _version(checkout), _head_tree(checkout))
    got = _structural(checkout, tmp_path)
    assert "fake native structural ran" in got.stdout, got.stdout + got.stderr
    assert not calls.exists(), "a current binary was rebuilt"


def test_a_stale_dirty_or_unstamped_binary_is_rebuilt_from_head_and_used(tmp_path):
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    for tree in ("0000 0000 0000 0000", "dirty", None):  # None: built before stamps existed
        _fake(checkout / "bin" / "goh", _version(checkout), tree)
        calls = _fake_builder(checkout, _version(checkout), _head_tree(checkout))
        got = _structural(checkout, tmp_path)
        assert "rebuilt native ran" in got.stdout, (tree, got.stdout + got.stderr)
        assert calls.read_text().split() == ["--if-stale"], (tree, calls.read_text())
        calls.unlink()


def test_a_failed_rebuild_falls_back_to_the_python_checkers_and_says_so(tmp_path):
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    _fake(checkout / "bin" / "goh", _version(checkout), "0000 0000 0000 0000")
    _fake_builder(checkout, _version(checkout), _head_tree(checkout), ok=False)
    got = _structural(checkout, tmp_path)
    assert "fake native structural ran" not in got.stdout, got.stdout
    assert got.returncode == 0, got.stdout + got.stderr  # the Python checkers ran, and passed
    assert "rebuild failed" in got.stderr and "cargo exploded" in got.stderr, got.stderr


def test_an_explicit_goh_bin_is_still_trusted(tmp_path):
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    fake = _fake(tmp_path / "named-goh", _version(checkout), "0000 0000 0000 0000")
    calls = _fake_builder(checkout, _version(checkout), _head_tree(checkout))
    got = _structural(checkout, tmp_path, GOH_BIN=str(fake))
    assert "fake native structural ran" in got.stdout, got.stdout + got.stderr
    assert not calls.exists()


def test_the_real_binary_reports_the_tree_it_was_built_from(goh: Path):
    """The fixture build is from this working tree: `dirty` while an input has uncommitted edits,
    else exactly HEAD's trees. Never empty, never a stale value."""
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([str(goh), "source-tree"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    inputs = ["crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml"]
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", *inputs],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert out.stdout.strip() == ("dirty" if dirty else _head_tree(root)), out.stdout


def test_goh_live_runs_the_working_trees_binary_not_heads(tmp_path: Path) -> None:
    """GOH_LIVE=1 is "run the working tree"; the native binary is part of it. bin/goh is HEAD's
    (C3), so with an uncommitted Rust change goh.sh used to run HEAD's binary under GOH_LIVE -- a
    new subcommand read "unrecognized" and a test of an uncommitted fix could pass against old code
    (found 2026-10-06, Phase N1). Planted: an uncommitted change to `goh --help`. The build reuses
    one target dir across runs (GOH_LIVE_TARGET_DIR), so only the first run compiles."""
    import os
    import subprocess

    from test_gate_environment import _clean_checkout_of_todays_gates

    gates = _clean_checkout_of_todays_gates(tmp_path)
    cli = gates / "crates" / "goh" / "src" / "cli.rs"
    before = cli.read_text()
    after = before.replace(
        "/// Fail when a crate is exempt from its workspace lint policy",
        "/// LIVE-RUST-EDIT-9031",
        1,
    )
    assert after != before, "the plant found nothing to replace: the test would prove nothing"
    cli.write_text(after)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "PYTEST_"))}
    env.update(
        GOH_LIVE="1",
        GOH_LIVE_TARGET_DIR=str(Path(__file__).resolve().parents[1] / "target" / "goh-live-test"),
    )
    live = subprocess.run(
        ["bash", str(gates / "gates" / "goh.sh"), "lints", "--help"],
        cwd=gates,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    assert "LIVE-RUST-EDIT-9031" in live.stdout, live.stdout + live.stderr
    env.pop("GOH_LIVE")
    head_cache = tmp_path / "head-cache"
    env["GOH_HEAD_CACHE"] = str(head_cache)
    env["GOH_NO_NATIVE"] = "1"  # HEAD's run must not build a second binary in this scratch clone
    head = subprocess.run(
        ["bash", str(gates / "gates" / "goh.sh"), "lints", "--help"],
        cwd=gates,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    assert "LIVE-RUST-EDIT-9031" not in head.stdout + head.stderr
