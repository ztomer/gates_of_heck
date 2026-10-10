"""A step the judged tree declares and the running binary lacks is NAMED (BACKLOG 1.4).

The version check and the source stamp both compare HEAD with the binary, so both pass while the
tree being committed declares a step HEAD has not got -- and a step that does not run prints
exactly what a step that passes prints. The binary embeds the manifest it was built from
(`goh structural --list-steps`); structural.sh reads the judged tree's manifest and names the gap.
"""

from __future__ import annotations

from pathlib import Path

from test_goh_structural import INVENTORY_CASE, MANIFEST, _run_bash, make_repo, manifest_steps

FUTURE = "a step from a later commit"


def _list(goh: Path) -> list[str]:
    import subprocess

    r = subprocess.run(
        [str(goh), "structural", "--list-steps"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_the_binary_lists_exactly_the_manifest(goh: Path) -> None:
    assert _list(goh) == manifest_steps()


def _with_manifest(tmp_path: Path, extra: list[str]) -> Path:
    text = MANIFEST.read_text() + "".join(f"{name}\n" for name in extra)
    return make_repo(tmp_path, {**INVENTORY_CASE, "crates/goh/structural_steps.txt": text.encode()})


def test_a_step_the_tree_declares_and_the_binary_lacks_is_named(goh: Path, tmp_path: Path) -> None:
    repo = _with_manifest(tmp_path, [FUTURE])
    r = _run_bash(repo, staged=True, goh=goh)
    assert FUTURE in r.stderr, r.stderr
    assert "NOT carried" in r.stderr, r.stderr


def test_a_tree_whose_steps_the_binary_carries_says_nothing(goh: Path, tmp_path: Path) -> None:
    repo = _with_manifest(tmp_path, [])
    r = _run_bash(repo, staged=True, goh=goh)
    assert "NOT carried" not in r.stderr, r.stderr
    assert "cannot list its steps" not in r.stderr, r.stderr


def test_a_binary_that_cannot_list_its_steps_is_said_never_read_as_carrying_all(
    tmp_path: Path,
) -> None:
    old = tmp_path / "bin" / "goh"
    old.parent.mkdir()
    # Answers the version check like a current binary, and refuses --list-steps like an old one.
    version = next(
        line.split('"')[1]
        for line in (MANIFEST.parents[2] / "Cargo.toml").read_text().splitlines()
        if line.startswith("version = ")
    )
    old.write_text(
        f'#!/bin/sh\ncase "$*" in --version) echo "goh {version}";; *list-steps*) exit 2;; *) exit 0;; esac\n'
    )
    old.chmod(0o755)
    repo = _with_manifest(tmp_path / "repo", [])
    r = _run_bash(repo, staged=True, goh=old)
    assert "cannot list its steps" in r.stderr, r.stderr
