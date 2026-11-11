"""gates/required_tools.tsv -- the ONE list of tools the gates require, pinned to every site.

Reported 2026-10-05 by the divoom/antiknob session: v0.20.0 made a missing shellcheck / swiftlint /
cargo-machete a hard failure (rightly), and antiknob's first CI run went red because its workflow
kept its OWN copy of the tool list, written before the requirement existed. Every consumer workflow
was such a copy, and each one drifted the day a gate gained a requirement.

So the list is published here, consumers install from it (`gates/required_tools.py --repo .
--install`), and this test makes the manifest and the code unable to disagree: every place a gate
or checker refuses for a missing tool must be a manifest row naming that file, and every row must
name a place that really refuses. A requirement added without a row is red here, before any
consumer's CI can find out.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "gates" / "required_tools.tsv"
CLI = ROOT / "gates" / "required_tools.py"

# Each shape of "refuse when this tool is absent" the gates use. A NEW shape must be added here,
# which is the point: the scan is the definition of a requirement site.
SITES = [
    re.compile(r"\bgoh_require ([\w.-]+)"),
    # A module the Python gates run through their interpreter (gates/_py.sh, 2026-10-10).
    re.compile(r"\bgoh_py_require ([\w.-]+)"),
    re.compile(r"^\s*need ([\w.-]+) \"", re.MULTILINE),
    re.compile(r"command -v ([\w.-]+) >/dev/null 2>&1 \|\| (?:die|\{)"),
    re.compile(r"if ! command -v ([\w.-]+) >/dev/null 2>&1; then"),
    re.compile(r"if not shutil\.which\(\"([\w.-]+)\"\)"),
    re.compile(r"(?:python3|\"\$GOH_PY\") -m ([\w.-]+) --version >/dev/null 2>&1 \\\s*\n\s*\|\|"),
    # The house interpreter (lib/house_python.sh, G2d): every gate sources it, and it refuses a
    # machine whose newest python3 is below the manifest's floor -- read off this exact row shape.
    re.compile(r'\$2 == "(python3)" && \$6 != "-"'),
    re.compile(r"cargo ([\w-]+) --version >/dev/null 2>&1 \\\s*\n\s*\|\|"),
    # The native ports (Phase N1) refuse through one helper, `pyformat::on_path("tool")`.
    re.compile(r"\bon_path\(\"([\w.-]+)\"\)"),
    # A module the goh suite imports, refused by name in tools/pytest.sh (layer `goh-suite`).
    re.compile(r"-c 'import \w+' 2>/dev/null \\\s*\n\s*\|\| die \"([\w.-]+) is not installed"),
]


def _manifest() -> list[dict]:
    rows = []
    for line in MANIFEST.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        layer, tool, kind, install, site, _floor = line.split("\t")
        rows.append({"layer": layer, "tool": tool, "kind": kind, "install": install, "site": site})
    return rows


def _scanned() -> set[tuple[str, str]]:
    found = set()
    for base, pattern in (
        ("gates", "*.sh"),
        ("checks", "*.sh"),
        ("checks", "*.py"),
        ("scripts", "*.sh"),
        ("lib", "*.sh"),
        (
            "tools",
            "*.py",
        ),  # tools/ship.py's git-cliff (layer `ship`, never inferred: CI ships nothing)
        ("tools", "*.sh"),  # tools/pytest.sh's pytest (layer `goh-suite`)
        ("crates/goh/src", "**/*.rs"),
    ):
        for path in sorted((ROOT / base).glob(pattern)):
            if path.name.startswith("."):
                continue  # a scratch copy, not a gate
            # Comment lines are prose about requirements, not requirements.
            text = "\n".join(
                ln
                for ln in path.read_text(errors="replace").splitlines()
                if not ln.lstrip().startswith(("#", "//"))
            )
            rel = path.relative_to(ROOT).as_posix()
            for rx in SITES:
                for m in rx.finditer(text):
                    tool = m.group(1)
                    found.add((rel, f"cargo-{tool}" if rx.pattern.startswith("cargo") else tool))
    return found


def test_every_requirement_site_is_a_manifest_row_and_back():
    rows = {(r["site"], r["tool"]) for r in _manifest()}
    scanned = _scanned()
    assert scanned - rows == set(), (
        f"required in code, missing from the manifest: {sorted(scanned - rows)}"
    )
    assert rows - scanned == set(), (
        f"manifest rows no code requires (inert): {sorted(rows - scanned)}"
    )


def test_every_row_says_how_to_install_it():
    for r in _manifest():
        assert r["install"].strip() and r["kind"] in {"brew", "cargo", "pip", "system"}, r


def test_the_cli_lists_a_layer_and_prints_install_commands():
    out = subprocess.run(
        [sys.executable, str(CLI), "--layer", "rust", "--layer", "swift", "--install"],
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    # The cargo tools go through a presence guard -- see
    # test_the_cargo_install_survives_a_binary_that_already_exists for why. brew is a plain
    # install: it is already idempotent.
    assert "cargo-machete" in out.stdout and "cargo install --locked" in out.stdout
    assert "brew install swiftlint" in out.stdout


def _install_lines(rows: list[dict]) -> list[str]:
    """`install_lines` for a hand-made selection -- so a test can name ONE tool and not depend on
    what happens to be on PATH. Loaded by path: the CLI is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("required_tools_under_test", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [ln for ln in mod.install_lines(rows) if ln.strip() and not ln.startswith("#")]


def test_the_cargo_install_survives_a_binary_that_already_exists(tmp_path):
    """`cargo install` REFUSES a binary it did not record, and a CI cache restores exactly that.

    `taiki-e/install-action` writes `~/.cargo/bin/<tool>` with no `.crates.toml` record, and
    `Swatinem/rust-cache` caches `~/.cargo/bin`, so a tool that was once installed another way is
    a bare binary the next `cargo install` will not overwrite: `error: binary `cargo-audit`
    already exists in destination`, exit 101. Measured on ztools' CI 2026-10-14..16 -- and because
    the workflow runs the manifest's commands under `set -euo pipefail`, every gate step after the
    install was skipped, for two days, without the gate ever running.

    The manifest's contract is PRESENCE (the tools a gate refuses without), so "already on PATH"
    is a satisfied requirement, whatever put it there. Proved with fakes rather than on a runner:
    a `cargo` stub that fails the way the real one does, and a `cargo-audit` stub already on PATH.

    Both directions, because a guard that skips is only evidence if the same command FAILS when
    there is nothing to skip.
    """
    cmds = _install_lines(
        [
            {
                "layer": "rust",
                "tool": "cargo-audit",
                "kind": "cargo",
                "install": "cargo install cargo-audit",
                "site": "tests/fake",
            }
        ]
    )
    assert cmds, "a cargo row must emit an install command"
    script = "\n".join(cmds)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "cargo-audit"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    # Cargo refuses to install over a binary it has no record of -- the real failure, verbatim.
    (bindir / "cargo").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  [ "$a" = install ] && { echo \'error: binary `cargo-audit` already exists in'
        " destination' >&2; exit 101; }\n"
        "done\n"
        "exit 0\n"
    )
    (bindir / "cargo").chmod(0o755)
    # A minimal PATH -- NOT the machine's: the real cargo-audit may be installed here, and the
    # "absent" direction below would then find it and prove nothing.
    env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-eu", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )

    present = run()
    assert present.returncode == 0, (
        "the emitted install is not idempotent: a tool already on PATH made it fail, which is the "
        f"failure that stopped every later CI step\n{present.stderr}"
    )

    stub.unlink()
    absent = run()
    assert absent.returncode != 0, (
        "with nothing on PATH the install must actually RUN and fail -- otherwise the guard "
        "above proved nothing"
    )


def test_repo_detection_finds_the_layers_a_repo_declares(tmp_path):
    """A workflow should not have to know which layers its repo uses either."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "gate.sh").write_text('"$GOH/gates/rust_gate.sh" .\n')
    (tmp_path / ".gatesrc").write_text("GOH_PYTHON_FORMATTED=1\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, stdin=subprocess.DEVNULL)
    (tmp_path / "x.sh").write_text("true\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    out = subprocess.run(
        [sys.executable, str(CLI), "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    tools = {line.split("\t")[1] for line in out.stdout.splitlines() if line.strip()}
    assert {"cargo-machete", "ruff", "shellcheck"} <= tools, out.stdout
    assert "swiftlint" not in tools, out.stdout


def test_ruff_is_only_for_repos_that_opted_into_the_format_check():
    """`--layer structural` must not install a tool only GOH_PYTHON_FORMATTED needs."""
    out = subprocess.run(
        [sys.executable, str(CLI), "--layer", "structural", "--names"],
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["cargo", "shellcheck"], out.stdout  # cargo builds bin/goh (N3)


def test_an_unknown_layer_is_refused_not_ignored():
    out = subprocess.run(
        [sys.executable, str(CLI), "--layer", "rsut"],
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert out.returncode == 2 and "rsut" in out.stderr


@pytest.mark.parametrize(
    ("gatesrc", "wanted"), [("GOH_RUST_LINT_CARGO=cargo-zigbuild\n", True), ("", False)]
)
def test_the_cross_lint_tools_are_for_repos_that_name_zigbuild(tmp_path, gatesrc, wanted):
    """media_server's --target clippy needs cargo-zigbuild AND zig; a repo that does not set it
    must not be told to install either (BACKLOG P1g)."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "gate.sh").write_text('"$GOH/gates/rust_gate.sh" .\n')
    (tmp_path / ".gatesrc").write_text(gatesrc)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, stdin=subprocess.DEVNULL)
    out = subprocess.run(
        [sys.executable, str(CLI), "--repo", str(tmp_path), "--names"],
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    names = set(out.stdout.split())
    assert ({"cargo-zigbuild", "zig"} <= names) is wanted, out.stdout
    assert ({"cargo-zigbuild", "zig"} & names == set()) is (not wanted), out.stdout


def _names_for(repo: Path, files: dict[str, str]) -> list[str]:
    fast_init(repo)
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    r = subprocess.run(
        [sys.executable, str(CLI), "--repo", str(repo), "--names"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_shellcheck_is_required_exactly_when_shell_lint_has_files(tmp_path: Path) -> None:
    """`_tracks_shell` and `goh shell-lint`'s scope are one rule: a Python hook under `hooks/` is
    not shell (2026-10-10), so it alone must not demand shellcheck, and a git hook still does."""
    assert "shellcheck" not in _names_for(tmp_path / "py", {"hooks/claude/n.py": "print(1)\n"})
    assert "shellcheck" in _names_for(tmp_path / "sh", {"hooks/pre-commit": "#!/bin/sh\n"})


def _cli(*args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args], capture_output=True, text=True, timeout=30,
        stdin=subprocess.DEVNULL, check=False, env={**os.environ, **env},
    )  # fmt: skip


def test_goh_suite_as_a_coverage_external_needs_pytest(tmp_path):
    """goh's first hosted run lost its external export to "No module named pytest": the suite ran
    as GOH_COV_RUST_EXTERNAL, and no layer said it needs pytest (CI.PUBLIC2)."""
    fast_init(tmp_path)
    (tmp_path / ".gatesrc").write_text("GOH_COV_RUST_EXTERNAL='GOH_TEST_BIN=x ./tools/pytest.sh'\n")
    got = _cli("--repo", str(tmp_path))
    assert got.returncode == 0, got.stderr
    assert any(ln.split("\t")[:2] == ["goh-suite", "pytest"] for ln in got.stdout.splitlines())
    (tmp_path / ".gatesrc").write_text("GOH_COV_FLOOR=95\n")
    assert "goh-suite" not in _cli("--repo", str(tmp_path)).stdout


def test_goh_suite_installs_the_modules_its_tests_import():
    """Run 42930175911: twelve golden_core tests errored with `NameError: PILImage` -- the suite
    builds its PNGs through Pillow, the parity tier is numpy's, and the hosted runner had neither,
    because no layer said the suite needs them (CI.PUBLIC2). tools/pytest.sh refuses without them,
    and the goh-suite layer installs them."""
    got = _cli("--layer", "goh-suite", "--install")
    assert got.returncode == 0, got.stderr
    line = next(ln for ln in got.stdout.splitlines() if "pip install" in ln)
    for pkg in ("pytest", "pillow", "numpy", "coverage"):
        assert pkg in line.split(), line


def test_goh_suite_installs_the_tools_its_fixture_gates_run():
    """Run 42944992082: the suite gates fixture repos through the Python layer (`<python> -m ruff`)
    and ships them through tools/ship.py (git-cliff), and the hosted runner had neither, because
    goh's own gates declare no python or ship layer -- 23 test_ship and ~45 Python-gate tests went
    red on a missing tool, not a defect (CI.PUBLIC2). The goh-suite layer installs both."""
    got = _cli("--layer", "goh-suite", "--install")
    assert got.returncode == 0, got.stderr
    pip = next(ln for ln in got.stdout.splitlines() if "pip install" in ln)
    assert "ruff" in pip.split(), pip
    brew = next((ln for ln in got.stdout.splitlines() if ln.startswith("brew install")), "")
    assert "git-cliff" in brew.split(), got.stdout


def test_pip_installs_into_the_house_interpreter():
    """Not PATH's first python3: a hosted macOS runner carries two 3.15s (CI.PUBLIC2)."""
    got = _cli("--layer", "goh-suite", "--install")
    line = next(ln for ln in got.stdout.splitlines() if "pip install" in ln)
    assert line.startswith('"$(bash ') and "lib/house_python.sh" in line, line
    assert " -m pip install pytest" in line, line
    want = subprocess.run(
        ["bash", str(ROOT / "lib" / "house_python.sh")], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=True,
    ).stdout.strip()  # fmt: skip
    said = subprocess.run(
        ["bash", "-c", line.split(" -m pip install ")[0] + " -c 'import sys; print(sys.executable)'"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True,
    ).stdout.strip()  # fmt: skip
    assert os.path.realpath(said) == os.path.realpath(subprocess.run(
        [want, "-c", "import sys; print(sys.executable)"], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=True,
    ).stdout.strip())  # fmt: skip


@pytest.mark.parametrize("os_name", ["Darwin", "Linux"])
def test_brew_is_printed_only_for_macos(tmp_path, os_name):
    """multitop's ubuntu run died on `brew: command not found` (run 42601978121, CI.PUBLIC2)."""
    lines = [
        ln for ln in _cli("--layer", "swift", "--install", GOH_TOOLS_OS=os_name).stdout.splitlines()
        if ln and not ln.startswith("#")
    ]  # fmt: skip
    assert len(lines) == 1, lines
    if os_name == "Darwin":
        assert lines[0] == "brew install swiftlint", lines
        return
    assert "brew install" not in lines[0], lines
    bindir = tmp_path / "bin"
    bindir.mkdir()
    path = f"{bindir}:/usr/bin:/bin"
    run = lambda: subprocess.run(
        ["/bin/bash", "-c", lines[0]], capture_output=True, text=True, env={"PATH": path},
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    missing = run()
    assert missing.returncode != 0 and "swiftlint is missing" in missing.stderr, missing
    (bindir / "swiftlint").write_text("#!/bin/sh\n")
    (bindir / "swiftlint").chmod(0o755)
    assert run().returncode == 0, "a tool already on PATH is done"


# The staged gates goh's own pre-commit runs on its own tree (gates/commit_gate.sh: structural with
# its shell lint, the python and swift twins, the rustfmt check) -- what
# tests/test_install.py::test_self_hosted_repo_satisfies_its_own_hook commits through.
SELF_HOSTED_HOOK_SITES = (
    "gates/py_staged.sh",
    "gates/swift_staged.sh",
    "crates/goh/src/rustfmt_staged.rs",
    "crates/goh/src/shell_lint.rs",
)


def test_goh_suite_installs_every_tool_its_self_hosted_hook_runs():
    """Run 42957429139: the self-hosted hook test died "swiftlint is not installed" on the hosted
    runner, after ruff (run 42950563400) -- one tool at a time, because no rule said the suite's
    layer must carry what goh's own hook needs (CI.PUBLIC2). Every tool a hook site requires is a
    goh-suite row, with a named floor where the tool is not the toolchain's own."""
    rows = _manifest()
    hook = {r["tool"] for r in rows if r["site"] in SELF_HOSTED_HOOK_SITES}
    suite = {r["tool"] for r in rows if r["layer"] == "goh-suite"}
    assert hook - suite == set(), (
        f"the hook needs, the goh-suite layer lacks: {sorted(hook - suite)}"
    )
    floors = {
        ln.split("\t")[1]: ln.split("\t")[5]
        for ln in MANIFEST.read_text().splitlines()
        if ln.startswith("goh-suite\t")
    }
    for tool in hook - {"ruff", "rustfmt"}:  # ruff: pinned per repo (GOH.RUFFPIN); rustfmt: rustup's
        assert floors[tool] != "-", f"goh-suite {tool} names no release"
