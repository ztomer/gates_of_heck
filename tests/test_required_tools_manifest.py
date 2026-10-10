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
    re.compile(r"^\s*need ([\w.-]+) \"", re.M),
    re.compile(r"command -v ([\w.-]+) >/dev/null 2>&1 \|\| (?:die|\{)"),
    re.compile(r"if ! command -v ([\w.-]+) >/dev/null 2>&1; then"),
    re.compile(r"if not shutil\.which\(\"([\w.-]+)\"\)"),
    re.compile(r"python3 -m ([\w.-]+) --version >/dev/null 2>&1 \\\s*\n\s*\|\|"),
    re.compile(r"cargo ([\w-]+) --version >/dev/null 2>&1 \\\s*\n\s*\|\|"),
    # The native ports (Phase N1) refuse through one helper, `pyformat::on_path("tool")`.
    re.compile(r"\bon_path\(\"([\w.-]+)\"\)"),
]


def _manifest() -> list[dict]:
    rows = []
    for line in MANIFEST.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        layer, tool, kind, install, site = line.split("\t")
        rows.append(dict(layer=layer, tool=tool, kind=kind, install=install, site=site))
    return rows


def _scanned() -> set[tuple[str, str]]:
    found = set()
    for base, pattern in (
        ("gates", "*.sh"),
        ("checks", "*.sh"),
        ("checks", "*.py"),
        ("scripts", "*.sh"),
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
    )
    assert out.returncode == 0, out.stderr
    assert "cargo install --locked cargo-machete" in out.stdout
    assert "brew install swiftlint" in out.stdout


def test_repo_detection_finds_the_layers_a_repo_declares(tmp_path):
    """A workflow should not have to know which layers its repo uses either."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "gate.sh").write_text('"$GOH/gates/rust_gate.sh" .\n')
    (tmp_path / ".gatesrc").write_text("GOH_PYTHON_FORMATTED=1\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "x.sh").write_text("true\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    out = subprocess.run(
        [sys.executable, str(CLI), "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
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
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["cargo", "shellcheck"], out.stdout  # cargo builds bin/goh (N3)


def test_an_unknown_layer_is_refused_not_ignored():
    out = subprocess.run(
        [sys.executable, str(CLI), "--layer", "rsut"], capture_output=True, text=True, timeout=30
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
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    out = subprocess.run(
        [sys.executable, str(CLI), "--repo", str(tmp_path), "--names"],
        capture_output=True,
        text=True,
        timeout=30,
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
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    r = subprocess.run(
        [sys.executable, str(CLI), "--repo", str(repo), "--names"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_shellcheck_is_required_exactly_when_shell_lint_has_files(tmp_path: Path) -> None:
    """`_tracks_shell` and `goh shell-lint`'s scope are one rule: a Python hook under `hooks/` is
    not shell (2026-10-10), so it alone must not demand shellcheck, and a git hook still does."""
    assert "shellcheck" not in _names_for(tmp_path / "py", {"hooks/claude/n.py": "print(1)\n"})
    assert "shellcheck" in _names_for(tmp_path / "sh", {"hooks/pre-commit": "#!/bin/sh\n"})
